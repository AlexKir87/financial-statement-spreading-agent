"""Plan step 26: gaps left after the r10 live run. Invented labels and numbers, no client data."""

import json

from app.mapping import load_mapping_table, map_lines
from app.state import RawLine, Usage, mapping_key
from app.tools import ModelReply, StructuredCaller, TokenBudget

PERIOD = "2026-06-30"


class _Client:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    def complete(
        self, *, model: str, system: str, user: str, max_tokens: int | None = None
    ) -> ModelReply:
        self.calls += 1
        return ModelReply(text=self.reply, usage=Usage(input_tokens=1, output_tokens=0))


def _raw(rid: str, label: str, value: str) -> RawLine:
    return RawLine(
        raw_line_id=rid,
        doc_id="doc1",
        page=2,
        original_label=label,
        original_value_text=value,
        statement="BS",
        period=PERIOD,
        scale="millions",
        currency="USD",
    )


def _caller(reply: str) -> StructuredCaller:
    return StructuredCaller(
        client=_Client(reply),
        model="test/model-1",
        budget=TokenBudget(cap=10_000),
        prompt_version="v1",
    )


def _caller_for(client: _Client) -> StructuredCaller:
    return StructuredCaller(
        client=client, model="test/model-1", budget=TokenBudget(cap=10_000), prompt_version="v1"
    )


def _answer(*items: tuple[str, str, float]) -> str:
    return json.dumps(
        {
            "mappings": [
                {"key": k, "standard_line_id": i, "confidence": c, "rationale": "fits"}
                for k, i, c in items
            ]
        }
    )


def test_cr_step26_the_last_printed_total_of_equity_wins_over_equity_before_minority_interest() -> (
    None
):
    # "Total shareholders' deficit" is printed before the minority line and "Total deficit" after
    # it; only the second holds everything, whatever confidence the model gave each.
    lines = [
        _raw("r1", "Total shareholders deficit", "-7674.3"),
        _raw("r2", "Noncontrolling interests", "7.2"),
        _raw("r3", "Total deficit", "-7667.1"),
    ]
    reply = _answer(
        ("k1", "bs_total_equity", 0.95),
        ("k2", "bs_other_equity", 0.9),
        ("k3", "bs_total_equity", 0.6),
    )
    result = map_lines(lines, load_mapping_table(), _caller(reply))
    kept = {m.raw_line_id: m.standard_line_id for m in result.mapping.values()}
    assert kept.get("r3") == "bs_total_equity"
    assert "r1" not in kept
    assert mapping_key(PERIOD, "r1") not in result.mapping


def test_cr_step26_a_liabilities_and_equity_line_never_wins_by_being_last() -> None:
    lines = [
        _raw("r1", "Total deficit", "-7667.1"),
        _raw("r2", "Total liabilities and deficit", "28294.7"),
    ]
    reply = _answer(("k1", "bs_total_equity", 0.9), ("k2", "bs_total_equity", 0.5))
    result = map_lines(lines, load_mapping_table(), _caller(reply))
    assert [m.raw_line_id for m in result.mapping.values()] == ["r1"]


def test_cr_step26_a_rule_hit_still_beats_the_last_printed_equity_total() -> None:
    lines = [_raw("r1", "Total equity", "6115"), _raw("r2", "Total deficit", "6100")]
    reply = _answer(("k1", "bs_total_equity", 0.9))
    result = map_lines(lines, load_mapping_table(), _caller(reply))
    assert [m.raw_line_id for m in result.mapping.values()] == ["r1"]


# --- Plan step 26: the retained earnings column of the equity statement -----------------------

from decimal import Decimal  # noqa: E402

from app.extract import extract_statements  # noqa: E402
from app.reconcile import run_reconciliations  # noqa: E402
from app.state import DigitizedPage  # noqa: E402

D = Decimal


def _re_result(spread: dict[str, dict[str, Decimal]]):
    report = run_reconciliations(
        spread, periods=("2025-12-31", "2026-01-01..2026-06-30", "2026-06-30")
    )
    return [r for r in report.results if r.key == "re_rollforward" and r.status != "not_applicable"]


def test_cr_step26_equity_column_movements_explain_a_buyback_charged_to_retained_earnings() -> None:
    spread = {
        "2025-12-31": {"bs_retained_earnings": D(800)},
        "2026-01-01..2026-06-30": {"is_net_income": D(500), "bs_re_column_movements": D(300)},
        "2026-06-30": {"bs_retained_earnings": D(1100)},
    }
    (result,) = _re_result(spread)
    assert result.status == "pass"


def test_cr_step26_a_wrong_column_total_still_fails_with_the_exact_difference() -> None:
    spread = {
        "2025-12-31": {"bs_retained_earnings": D(800)},
        "2026-01-01..2026-06-30": {"is_net_income": D(500), "bs_re_column_movements": D(290)},
        "2026-06-30": {"bs_retained_earnings": D(1100)},
    }
    (result,) = _re_result(spread)
    assert result.status == "fail" and result.difference == D(-10)


def test_cr_step26_dividends_paid_are_not_added_again_when_the_column_is_read() -> None:
    spread = {
        "2025-12-31": {"bs_retained_earnings": D(800)},
        "2026-01-01..2026-06-30": {
            "is_net_income": D(500),
            "cf_dividends_paid": D(-100),
            "bs_re_column_movements": D(400),
        },
        "2026-06-30": {"bs_retained_earnings": D(1200)},
    }
    (result,) = _re_result(spread)
    assert result.status == "pass"


def test_cr_step26_without_the_column_the_old_rule_applies() -> None:
    spread = {
        "2025-12-31": {"bs_retained_earnings": D(800)},
        "2026-01-01..2026-06-30": {"is_net_income": D(500), "cf_dividends_paid": D(-100)},
        "2026-06-30": {"bs_retained_earnings": D(1200)},
    }
    (result,) = _re_result(spread)
    assert result.status == "pass"


EQUITY_PAGE = "\n".join(
    [
        "CONSOLIDATED STATEMENTS OF CHANGES IN EQUITY",
        "(in millions of USD)",
        "Common stock | Additional paid-in capital | Retained earnings | Total",
        "Six Months Ended June 30, 2026",
        "Net income 500 500",
        "Repurchase of common stock (200) (200)",
    ]
)


class _EquityClient:
    def __init__(self) -> None:
        self.systems: list[str] = []

    def complete(
        self, *, model: str, system: str, user: str, max_tokens: int | None = None
    ) -> ModelReply:
        self.systems.append(system)
        lines = [
            {
                "statement": "SE",
                "label": label,
                "period": "Six Months Ended June 30, 2026",
                "value_text": value,
            }
            for label, value in (("Net income", "500"), ("Repurchase of common stock", "(200)"))
        ]
        text = json.dumps({"currency": "USD", "scale": "millions", "lines": lines})
        return ModelReply(text=text, usage=Usage(input_tokens=1, output_tokens=1))


def test_cr_step26_an_equity_page_is_read_for_its_retained_earnings_column() -> None:
    client = _EquityClient()
    caller = StructuredCaller(
        client=client, model="test/model-1", budget=TokenBudget(cap=10_000), prompt_version="v1"
    )
    page = DigitizedPage(
        doc_id="doc1",
        page=4,
        text=EQUITY_PAGE,
        digitizer_name="pdf-text-layer",
        digitizer_version="1",
    )
    result = extract_statements([page], caller)
    assert "retained earnings column" in client.systems[0].lower()
    assert [(ln.statement, ln.original_value_text) for ln in result.raw_lines] == [
        ("SE", "500"),
        ("SE", "(200)"),
    ]
    assert {ln.period for ln in result.raw_lines} == {"2026-01-01..2026-06-30"}


def test_cr_step26_equity_movements_map_to_the_column_line_without_a_model_call() -> None:
    lines = [
        RawLine(
            raw_line_id=f"r{n}",
            doc_id="doc1",
            page=4,
            original_label=label,
            original_value_text=value,
            statement="SE",
            period="2026-01-01..2026-06-30",
            scale="millions",
            currency="USD",
        )
        for n, (label, value) in enumerate((("Net income", "500"), ("Repurchases", "(200)")), 1)
    ]
    cf = _raw("c1", "Net income", "500").model_copy(
        update={"statement": "CF", "period": "2026-01-01..2026-06-30"}
    )
    client = _Client(_answer())
    result = map_lines([cf, *lines], load_mapping_table(), _caller_for(client))
    assert {
        m.standard_line_id for m in result.mapping.values() if m.raw_line_id.startswith("r")
    } == {"bs_re_column_movements"}
    assert client.calls == 0


def _se(rid: str, period: str, value: str = "10") -> RawLine:
    return RawLine(
        raw_line_id=rid,
        doc_id="doc1",
        page=4,
        original_label="Net income",
        original_value_text=value,
        statement="SE",
        period=period,
        scale="millions",
        currency="USD",
    )


def test_cr_step26_an_equity_period_with_no_statement_of_its_own_is_not_mapped() -> None:
    # the equity page also holds a quarter block and a prior-year block that no IS or CF column has
    cf = _raw("r1", "Net income", "500").model_copy(
        update={"statement": "CF", "period": "2026-01-01..2026-06-30"}
    )
    lines = [cf, _se("s1", "2026-01-01..2026-06-30"), _se("s2", "2026-01-01..2026-03-31")]
    result = map_lines(lines, load_mapping_table(), _caller_for(_Client(_answer())))
    assert mapping_key("2026-01-01..2026-06-30", "s1") in result.mapping
    assert mapping_key("2026-01-01..2026-03-31", "s2") not in result.mapping
    assert [i.code for i in result.open_issues if i.where == "s2"] == ["equity_period_not_used"]


def test_cr_step26_the_column_line_is_not_offered_to_the_model_for_a_balance_sheet_row() -> None:
    table = load_mapping_table()
    assert "bs_re_column_movements" not in table.line_ids("BS")
    assert "bs_re_column_movements" in table.line_ids()


def _correction(line_id: str, raw_line_id: str):
    from app.state import Correction

    return Correction(
        correction_id="c1",
        raw_line_id=raw_line_id,
        period="2026-01-01..2026-06-30",
        standard_line_id=line_id,
        who="analyst",
        why="test",
    )


def test_cr_step26_a_correction_keeps_equity_rows_on_the_column_line_and_others_off_it() -> None:
    from datetime import UTC, datetime

    from app.corrections.apply import apply_corrections

    cf = _raw("r1", "Net income", "500").model_copy(
        update={"statement": "BS", "period": "2026-01-01..2026-06-30"}
    )
    lines = [cf, _se("s1", "2026-01-01..2026-06-30")]
    outcome = apply_corrections(
        corrections=[
            _correction("bs_re_column_movements", "r1"),
            _correction("bs_retained_earnings", "s1").model_copy(update={"correction_id": "c2"}),
        ],
        applied=[],
        raw_lines=lines,
        mapping={},
        table=load_mapping_table(),
        now=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert outcome.mapping == {}
    assert len(outcome.open_issues) == 2
