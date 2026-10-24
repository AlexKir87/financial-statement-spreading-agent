import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app.config import ConfigError, Settings
from app.state import (
    CHECK_ORDER,
    ChangeEntry,
    CheckResult,
    ExceptionNote,
    MappedLine,
    OpenIssue,
    RawLine,
    RunSummary,
    SpreadState,
    Usage,
    append,
    count_attempts,
    initial_state,
    mapping_key,
    merge_mapping,
    sum_usage,
)

SHA = "a" * 64


def _raw(i: str, page: int = 1) -> RawLine:
    return RawLine(
        raw_line_id=i,
        doc_id="d1",
        page=page,
        original_label="Total assets",
        original_value_text="3,500",
        statement="BS",
        period="FY2023",
        scale="thousands",
        currency="USD",
    )


def _mapped(line_id: str, origin: str = "model") -> MappedLine:
    return MappedLine.model_validate(
        {
            "raw_line_id": "r1",
            "period": "FY2023",
            "standard_line_id": line_id,
            "confidence": 0.9,
            "rationale": "label match",
            "origin": origin,
        }
    )


# --- reducers ---------------------------------------------------------------------------


def test_append_keeps_existing_and_adds_incoming():
    assert append([1, 2], [3]) == [1, 2, 3]
    assert append(None, [1]) == [1]
    assert append([1], None) == [1]


def test_merge_mapping_adds_keys_and_later_entry_wins():
    key = mapping_key("FY2023", "r1")
    first = merge_mapping({}, {key: _mapped("bs_total_assets")})
    other = mapping_key("FY2023", "r2")
    merged = merge_mapping(first, {other: _mapped("bs_cash"), key: _mapped("bs_cash", "analyst")})
    assert set(merged) == {key, other}
    assert merged[key].standard_line_id == "bs_cash"
    assert merged[key].origin == "analyst"
    assert first[key].standard_line_id == "bs_total_assets"  # inputs are not mutated


def test_sum_usage_adds_tokens():
    total = sum_usage(
        Usage(input_tokens=10, output_tokens=5), Usage(input_tokens=1, output_tokens=2)
    )
    assert (total.input_tokens, total.output_tokens, total.total_tokens) == (11, 7, 18)


def test_count_attempts_adds_and_none_resets():
    assert count_attempts(0, 1) == 1
    assert count_attempts(1, 1) == 2
    assert count_attempts(2, None) == 0


def test_reducers_apply_in_a_compiled_graph():
    def left(_: SpreadState) -> dict[str, object]:
        return {
            "raw_lines": [_raw("r1")],
            "open_issues": [OpenIssue(code="scale_assumed", where="BS", detail="a")],
            "token_usage": Usage(input_tokens=3),
            "repair_attempts": 1,
            "mapping": {mapping_key("FY2023", "r1"): _mapped("bs_total_assets")},
        }

    def right(_: SpreadState) -> dict[str, object]:
        return {
            "raw_lines": [_raw("r2")],
            "open_issues": [OpenIssue(code="low_confidence_mapping", where="IS", detail="b")],
            "token_usage": Usage(output_tokens=4),
            "repair_attempts": 1,
            "mapping": {mapping_key("FY2023", "r2"): _mapped("bs_cash")},
        }

    builder = StateGraph(SpreadState)
    builder.add_node("left", left)
    builder.add_node("right", right)
    builder.add_edge(START, "left")
    builder.add_edge(START, "right")
    builder.add_edge(["left", "right"], END)
    out = builder.compile().invoke(initial_state("run-1", "company-a"))

    assert sorted(r.raw_line_id for r in out["raw_lines"]) == ["r1", "r2"]
    assert {i.code for i in out["open_issues"]} == {"scale_assumed", "low_confidence_mapping"}
    assert out["token_usage"].total_tokens == 7
    assert out["repair_attempts"] == 2
    assert len(out["mapping"]) == 2


def test_initial_state_has_every_channel_empty():
    state = initial_state("run-1", "company-a")
    assert set(state) == set(SpreadState.__annotations__)
    assert state["raw_lines"] == [] and state["repair_attempts"] == 0
    assert state["status"] is None


# --- amounts and models -----------------------------------------------------------------


def test_float_amount_is_rejected_everywhere_an_amount_is_held():
    with pytest.raises(ValidationError):
        ExceptionNote(
            check="bs_balances", period="FY2023", difference=50.0, explanation="x", attempts_made=2
        )
    with pytest.raises(ValidationError):
        ExceptionNote(
            check="bs_balances", period="FY2023", difference=True, explanation="x", attempts_made=2
        )


def test_decimal_int_and_string_amounts_are_accepted_exactly():
    for value in (Decimal("50.25"), 50, "50.25"):
        note = ExceptionNote(
            check="bs_balances", period="FY2023", difference=value, explanation="x", attempts_made=2
        )
        assert isinstance(note.difference, Decimal)
    assert ExceptionNote(
        check="bs_balances", period="FY2023", difference="0.1", explanation="x", attempts_made=0
    ).difference == Decimal("0.1")


def test_non_finite_and_garbage_amounts_are_rejected():
    for bad in ("NaN", "abc", None):
        with pytest.raises(ValidationError):
            ExceptionNote(
                check="bs_balances",
                period="FY2023",
                difference=bad,
                explanation="x",
                attempts_made=0,
            )


def test_standard_line_id_must_be_prefixed_snake_case():
    for bad in ("total_assets", "bs.total_assets", "BS_total", "bs_"):
        with pytest.raises(ValidationError):
            _mapped(bad)


def test_models_are_frozen_and_forbid_extra_fields():
    line = _raw("r1")
    with pytest.raises(ValidationError):
        line.page = 2  # type: ignore[misc]
    with pytest.raises(ValidationError):
        RawLine.model_validate({**line.model_dump(), "invented": 1})


def test_check_result_figures_follow_status():
    CheckResult(key="bs_balances", period="FY2023", status="not_applicable")
    with pytest.raises(ValidationError):
        CheckResult(key="bs_balances", period="FY2023", status="not_applicable", difference=0)
    with pytest.raises(ValidationError):
        CheckResult(key="bs_balances", period="FY2023", status="fail", difference=50)


def test_change_entry_needs_a_timezone():
    with pytest.raises(ValidationError):
        ChangeEntry(who="system", what="w", when=datetime(2026, 1, 1), why="y")
    ChangeEntry(who="system", what="w", when=datetime(2026, 1, 1, tzinfo=UTC), why="y")


# --- RunSummary -------------------------------------------------------------------------


def _check(status: str = "pass", diff: object = 0) -> dict[str, object]:
    return {"status": status, "max_abs_diff": diff}


SPEC_JSON = {
    "status": "delivered_with_exceptions",
    "not_delivered_reason": None,
    "company_ref": "company-a",
    "workbook": {"path": "out/company-a_v1.xlsx", "version": 1, "verified": True},
    "scale": {"unit": "thousands", "currency": "USD"},
    "checks": {
        "bs_balances": _check("fail", 50),
        "cf_cash_reconciles": _check(),
        "re_rollforward": _check(),
        "ni_pl_equals_cf": _check(),
        "subtotals_match_source": _check(),
        "lines_complete": _check(),
    },
    "plug_figures": 0,
    "repair_attempts": 2,
    "spread": {"FY2023": {"is_net_income": 500, "bs_total_assets": 3500}},
    "trace": {
        "FY2023": {"bs_total_assets": {"doc_id": "d1", "page": 5, "original_label": "Total assets"}}
    },
    "exceptions": [
        {
            "check": "bs_balances",
            "period": "FY2023",
            "difference": 50,
            "explanation": "assets exceed liabilities plus equity",
            "attempts_made": 2,
        }
    ],
    "open_issues": [
        {
            "code": "statement_missing_in_source",
            "where": "income statement",
            "detail": "none printed",
        }
    ],
    "usage": {"total_tokens": 0},
}


def test_run_summary_round_trips_the_spec_json():
    summary = RunSummary.model_validate_json(json.dumps(SPEC_JSON))
    assert json.loads(summary.model_dump_json()) == SPEC_JSON
    assert summary.spread["FY2023"]["bs_total_assets"] == Decimal(3500)


def test_run_summary_fractional_amount_round_trips_exactly():
    data = {**SPEC_JSON, "spread": {"FY2023": {"is_net_income": "12.5"}}}
    out = json.loads(RunSummary.model_validate(data).model_dump_json())
    assert out["spread"] == {"FY2023": {"is_net_income": "12.5"}}


def test_run_summary_rejects_a_float_amount():
    bad = {**SPEC_JSON, "spread": {"FY2023": {"is_net_income": 500.5}}}
    with pytest.raises(ValidationError):
        RunSummary.model_validate_json(json.dumps(bad))


def test_plug_figures_is_always_zero():
    assert RunSummary.model_validate(SPEC_JSON).plug_figures == 0
    with pytest.raises(ValidationError):
        RunSummary.model_validate({**SPEC_JSON, "plug_figures": 1})
    assert RunSummary.model_fields["plug_figures"].default == 0


def test_not_delivered_needs_reason_and_no_workbook():
    ok = RunSummary(
        status="not_delivered", not_delivered_reason="unsupported_language", company_ref="company-b"
    )
    assert ok.workbook is None
    with pytest.raises(ValidationError):
        RunSummary(status="not_delivered", company_ref="company-b")
    with pytest.raises(ValidationError):
        RunSummary.model_validate(
            {**SPEC_JSON, "status": "not_delivered", "not_delivered_reason": "out_of_scope"}
        )


def test_delivered_cannot_carry_a_failed_check_or_exception():
    with pytest.raises(ValidationError):
        RunSummary.model_validate({**SPEC_JSON, "status": "delivered"})


def test_delivered_run_reports_all_six_checks():
    partial = {**SPEC_JSON, "checks": {"bs_balances": _check("fail", 50)}}
    with pytest.raises(ValidationError):
        RunSummary.model_validate(partial)
    assert set(SPEC_JSON["checks"]) == set(CHECK_ORDER)  # type: ignore[call-overload]


# --- config -----------------------------------------------------------------------------

ENV = {
    "COGNITIA_OPENROUTER_API_KEY": "sk-test-not-a-real-key",
    "SPREAD_CHECKPOINT_DB": "state/ckpt.sqlite",
    "SPREAD_OUTPUT_DIR": "state/out",
    "SPREAD_MAX_TOKENS_PER_RUN": "60000",
}


def test_settings_read_from_env_with_defaults():
    s = Settings.from_env(ENV)
    assert s.max_tokens_per_run == 60000
    assert s.max_repair_attempts == 2
    assert s.rounding_tolerance_units == 2  # plan step 29
    assert s.openrouter_api_key.get_secret_value() == ENV["COGNITIA_OPENROUTER_API_KEY"]


def test_settings_optional_values_are_read():
    s = Settings.from_env(
        {
            **ENV,
            "SPREAD_MAX_REPAIR_ATTEMPTS": "3",
            "SPREAD_PROMPT_VERSION": "p7",
            "EVAL_JUDGE_MODEL": "judge-x",
            "COGNITIA_ANTHROPIC_API_KEY": "k",
        }
    )
    assert (s.max_repair_attempts, s.prompt_version, s.eval_judge_model) == (3, "p7", "judge-x")
    assert s.anthropic_api_key is not None


def test_missing_required_variables_are_named_without_values():
    env = {k: v for k, v in ENV.items() if k != "SPREAD_OUTPUT_DIR"}
    with pytest.raises(ConfigError, match="SPREAD_OUTPUT_DIR"):
        Settings.from_env(env)


def test_invalid_value_error_names_the_variable_not_the_value():
    with pytest.raises(ConfigError) as err:
        Settings.from_env({**ENV, "SPREAD_MAX_TOKENS_PER_RUN": "lots-secret-1"})
    assert "SPREAD_MAX_TOKENS_PER_RUN" in str(err.value)
    assert "lots-secret-1" not in str(err.value)
    with pytest.raises(ConfigError):
        Settings.from_env({**ENV, "SPREAD_MAX_REPAIR_ATTEMPTS": "-1"})


def test_secret_never_appears_in_repr_or_dump():
    s = Settings.from_env(ENV)
    assert ENV["COGNITIA_OPENROUTER_API_KEY"] not in repr(s)
    assert ENV["COGNITIA_OPENROUTER_API_KEY"] not in s.model_dump_json()
