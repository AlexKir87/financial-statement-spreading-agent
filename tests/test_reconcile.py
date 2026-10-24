"""Tests for step 4: the six reconciliation checks (pure code, no model, no network).

The base filing is the anonymised one from the spec's eval note: it balances, retained
earnings roll 800 + 500 = 1,300, and cash reconciles 500 - 300 + 100 = 300 = 900 - 600.
"""

import copy
import inspect
import random
import types
import typing
from decimal import Decimal

import pytest
from pydantic import ValidationError

import app.reconcile as reconcile_pkg
from app.config import Settings
from app.reconcile import checks, engine, rules, run_reconciliations
from app.state import CHECK_ORDER, CheckKey, CheckResult

PERIODS = ("FY2022", "FY2023")

FY2022: dict[str, int] = {
    "is_revenue": 4500,
    "is_cost_of_sales": -2800,
    "is_gross_profit": 1700,
    "is_operating_expenses": -1100,
    "is_operating_profit": 600,
    "is_interest_expense": -100,
    "is_profit_before_tax": 500,
    "is_income_tax": -150,
    "is_net_income": 350,
    "bs_cash": 600,
    "bs_receivables": 500,
    "bs_inventory": 400,
    "bs_total_current_assets": 1500,
    "bs_ppe": 1300,
    "bs_total_assets": 2800,
    "bs_payables": 600,
    "bs_borrowings": 900,
    "bs_total_liabilities": 1500,
    "bs_share_capital": 500,
    "bs_retained_earnings": 800,
    "bs_total_equity": 1300,
    "bs_total_liabilities_and_equity": 2800,
}
FY2023: dict[str, int] = {
    "is_revenue": 5000,
    "is_cost_of_sales": -3000,
    "is_gross_profit": 2000,
    "is_operating_expenses": -1200,
    "is_operating_profit": 800,
    "is_interest_expense": -100,
    "is_profit_before_tax": 700,
    "is_income_tax": -200,
    "is_net_income": 500,
    "bs_cash": 900,
    "bs_receivables": 600,
    "bs_inventory": 500,
    "bs_total_current_assets": 2000,
    "bs_ppe": 1500,
    "bs_total_assets": 3500,
    "bs_payables": 700,
    "bs_borrowings": 1000,
    "bs_total_liabilities": 1700,
    "bs_share_capital": 500,
    "bs_retained_earnings": 1300,
    "bs_total_equity": 1800,
    "bs_total_liabilities_and_equity": 3500,
    "cf_net_income": 500,
    "cf_depreciation_amortisation": 100,
    "cf_change_receivables": -100,
    "cf_change_inventory": -100,
    "cf_change_payables": 100,
    "cf_net_cash_operating": 500,
    "cf_capex": -300,
    "cf_net_cash_investing": -300,
    "cf_net_cash_financing": 100,
    "cf_net_change_in_cash": 300,
}

Spread = dict[str, dict[str, Decimal]]


def _spread(**changes: int | None) -> Spread:
    """The base filing. `changes` edit FY2023 lines; `None` removes the line."""
    fy2023 = {**FY2023, **{k: v for k, v in changes.items() if v is not None}}
    for line_id, value in changes.items():
        if value is None:
            del fy2023[line_id]
    return {
        "FY2022": {k: Decimal(v) for k, v in FY2022.items()},
        "FY2023": {k: Decimal(v) for k, v in fy2023.items()},
    }


def _run(spread: Spread, **kwargs: typing.Any) -> engine.ReconciliationReport:
    return run_reconciliations(spread, periods=PERIODS, **kwargs)


def _results(
    report: engine.ReconciliationReport, key: CheckKey, period: str | None = None
) -> list[CheckResult]:
    return [r for r in report.results if r.key == key and (period is None or r.period == period)]


def _only(report: engine.ReconciliationReport, key: CheckKey, period: str) -> CheckResult:
    found = _results(report, key, period)
    assert len(found) == 1, found
    return found[0]


# --- The base filing -------------------------------------------------------------------


def test_base_filing_every_applicable_check_passes_with_zero_difference() -> None:
    report = _run(_spread())
    statuses = {(r.key, r.period): r.status for r in report.results}
    assert statuses == {
        ("bs_balances", "FY2022"): "pass",
        ("bs_balances", "FY2023"): "pass",
        ("cf_cash_reconciles", "FY2022"): "not_applicable",
        ("cf_cash_reconciles", "FY2023"): "pass",
        ("re_rollforward", "FY2022"): "not_applicable",
        ("re_rollforward", "FY2023"): "pass",
        ("ni_pl_equals_cf", "FY2022"): "not_applicable",
        ("ni_pl_equals_cf", "FY2023"): "pass",
        ("subtotals_match_source", "FY2022"): "pass",
        ("subtotals_match_source", "FY2023"): "pass",
        ("lines_complete", "FY2022"): "pass",
        ("lines_complete", "FY2023"): "pass",
    }
    assert all(r.difference == 0 for r in report.results if r.status == "pass")


def test_base_filing_figures_for_the_cash_and_rollforward_checks() -> None:
    report = _run(_spread())
    cash = _only(report, "cf_cash_reconciles", "FY2023")
    assert (cash.expected, cash.actual) == (Decimal(300), Decimal(300))  # 900 - 600 and CF
    roll = _only(report, "re_rollforward", "FY2023")
    assert (roll.expected, roll.actual) == (Decimal(1300), Decimal(1300))  # 800 + 500
    assert {(t.line_id, t.period) for t in roll.terms} == {
        ("bs_retained_earnings", "FY2022"),
        ("bs_retained_earnings", "FY2023"),
        ("is_net_income", "FY2023"),
    }


def test_results_follow_the_canonical_check_order_then_period_order() -> None:
    report = _run(_spread())
    keys = [r.key for r in report.results]
    assert keys == sorted(keys, key=CHECK_ORDER.index)
    for key in CHECK_ORDER:
        assert [r.period for r in _results(report, key)] == list(PERIODS)


def test_each_not_applicable_result_has_an_open_issue_that_says_where_and_why() -> None:
    report = _run(_spread())
    na = [r for r in report.results if r.status == "not_applicable"]
    assert len(na) == 3
    assert {i.where for i in report.open_issues} == {f"{r.key}:{r.period}" for r in na}
    assert all(i.code == "check_not_applicable" and i.detail for i in report.open_issues)


# --- Each check passes, fails with a difference of 50 --------------------------------

FIFTY_CASES: list[tuple[CheckKey, dict[str, int], Decimal]] = [
    # (check, edit to FY2023, expected difference = actual - expected)
    ("bs_balances", {"bs_total_assets": 3550}, Decimal(-50)),
    ("cf_cash_reconciles", {"cf_net_change_in_cash": 350}, Decimal(50)),
    ("re_rollforward", {"bs_retained_earnings": 1350}, Decimal(-50)),
    ("ni_pl_equals_cf", {"cf_net_income": 550}, Decimal(50)),
    ("subtotals_match_source", {"is_gross_profit": 2050}, Decimal(-50)),
    ("lines_complete", {"bs_inventory": 450}, Decimal(-50)),
]


@pytest.mark.parametrize(("key", "edit", "difference"), FIFTY_CASES)
def test_check_fails_with_the_exact_difference(
    key: CheckKey, edit: dict[str, int], difference: Decimal
) -> None:
    report = _run(_spread(**edit))
    failed = [r for r in _results(report, key, "FY2023") if r.status == "fail"]
    assert failed, f"{key} should fail"
    assert difference in {r.difference for r in failed}
    for result in failed:
        assert result.difference == result.actual - result.expected  # type: ignore[operator]
    assert _only(_run(_spread()), key, "FY2023").status == "pass"


@pytest.mark.parametrize(
    "key",
    ["bs_balances", "cf_cash_reconciles", "re_rollforward", "ni_pl_equals_cf"],
)
def test_single_comparison_checks_report_one_result_per_period(key: CheckKey) -> None:
    edit = next(e for k, e, _ in FIFTY_CASES if k == key)
    assert len(_results(_run(_spread(**edit)), key)) == len(PERIODS)


def test_unbalanced_variant_from_the_eval_note_fails_balance_and_rollforward() -> None:
    report = _run(
        _spread(
            bs_retained_earnings=1250,
            bs_total_equity=1750,
            bs_total_liabilities_and_equity=3450,
        )
    )
    balance = _only(report, "bs_balances", "FY2023")
    assert (balance.status, abs(balance.difference or 0)) == ("fail", 50)
    assert _only(report, "re_rollforward", "FY2023").status == "fail"
    # The printed subtotals still chain, so the two other checks stay green.
    assert _only(report, "subtotals_match_source", "FY2023").status == "pass"
    assert _only(report, "lines_complete", "FY2023").status == "pass"
    failed = [r.key for r in report.results if r.status == "fail"]
    assert failed == ["bs_balances", "re_rollforward"]  # canonical order


def test_failure_in_a_chain_reports_every_failing_rule() -> None:
    # Gross profit printed 50 too high: the gross profit rule and the operating profit rule
    # (which builds on it) both differ, and each is its own result for `diagnose`.
    report = _run(_spread(is_gross_profit=2050))
    failed = [r for r in _results(report, "subtotals_match_source", "FY2023") if r.status == "fail"]
    assert len(failed) == 2
    assert {t.line_id for r in failed for t in r.terms} >= {
        "is_gross_profit",
        "is_operating_profit",
    }


# --- not_applicable --------------------------------------------------------------------


def test_missing_statement_is_not_applicable_with_statement_missing_issue() -> None:
    balance_sheet_only = {
        p: {k: v for k, v in c.items() if k.startswith("bs_")} for p, c in _spread().items()
    }
    report = _run(balance_sheet_only)
    for key in ("cf_cash_reconciles", "re_rollforward", "ni_pl_equals_cf"):
        assert {r.status for r in _results(report, key)} == {"not_applicable"}
    assert _only(report, "bs_balances", "FY2023").status == "pass"
    assert report.open_issues[0].code == "statement_missing_in_source"
    missing = {i.where for i in report.open_issues if i.code == "statement_missing_in_source"}
    assert {
        "cf_cash_reconciles:FY2023",
        "re_rollforward:FY2023",
        "ni_pl_equals_cf:FY2022",
        "ni_pl_equals_cf:FY2023",
    } <= missing
    # No income statement, so nothing is invented for net income.
    assert all("is_net_income" not in {t.line_id for t in r.terms} for r in report.results)


def test_first_period_has_no_prior_balance_sheet() -> None:
    report = _run(_spread())
    for key in ("cf_cash_reconciles", "re_rollforward"):
        assert _only(report, key, "FY2022").status == "not_applicable"
    detail = next(i.detail for i in report.open_issues if i.where == "re_rollforward:FY2022")
    assert "prior" in detail


def test_missing_line_in_a_present_statement_is_check_not_applicable_naming_the_line() -> None:
    report = _run(_spread(cf_net_change_in_cash=None))
    assert _only(report, "cf_cash_reconciles", "FY2023").status == "not_applicable"
    issue = next(i for i in report.open_issues if i.where == "cf_cash_reconciles:FY2023")
    assert issue.code == "check_not_applicable"
    assert "cf_net_change_in_cash" in issue.detail


def test_period_without_any_printed_total_is_not_applicable() -> None:
    sparse = {"FY2022": {"bs_cash": Decimal(600)}, "FY2023": {"bs_cash": Decimal(900)}}
    report = _run(sparse)
    for key in ("subtotals_match_source", "lines_complete"):
        assert {r.status for r in _results(report, key)} == {"not_applicable"}
    assert len([i for i in report.open_issues if i.where.startswith("lines_complete")]) == 2


# --- Tolerance -------------------------------------------------------------------------


def _off_by(units: int) -> Spread:
    # Only total assets moves, so only `bs_balances` and the assets rules see the change.
    return _spread(bs_total_assets=3500 + units)


def test_rounding_of_one_passes_and_two_fails() -> None:
    one = _only(_run(_off_by(1)), "bs_balances", "FY2023")
    assert (one.status, one.difference) == ("pass", Decimal(-1))  # exact difference is kept
    two = _only(_run(_off_by(2)), "bs_balances", "FY2023")
    assert (two.status, two.difference) == ("fail", Decimal(-2))


def test_rounding_in_the_other_direction_is_symmetric() -> None:
    assert _only(_run(_off_by(-1)), "bs_balances", "FY2023").status == "pass"
    assert _only(_run(_off_by(-2)), "bs_balances", "FY2023").status == "fail"


_SETTINGS_ENV = {
    "COGNITIA_OPENROUTER_API_KEY": "sk-test-not-a-real-key",
    "SPREAD_CHECKPOINT_DB": "state/ckpt.sqlite",
    "SPREAD_OUTPUT_DIR": "state/out",
    "SPREAD_MAX_TOKENS_PER_RUN": "60000",
}


def test_step_29_settings_tolerance_passes_two_and_fails_three() -> None:
    # Filings round each line on its own, so a section of many lines can foot two units off.
    tolerance = Settings.from_env(_SETTINGS_ENV).rounding_tolerance_units
    two = _only(_run(_off_by(2), tolerance=tolerance), "bs_balances", "FY2023")
    assert (two.status, two.difference) == ("pass", Decimal(-2))
    three = _only(_run(_off_by(3), tolerance=tolerance), "bs_balances", "FY2023")
    assert (three.status, three.difference) == ("fail", Decimal(-3))


def test_tolerance_is_the_one_setting() -> None:
    assert _only(_run(_off_by(1), tolerance=Decimal(0)), "bs_balances", "FY2023").status == "fail"
    assert _only(_run(_off_by(2), tolerance=Decimal(2)), "bs_balances", "FY2023").status == "pass"
    with pytest.raises(ValueError, match="negative"):
        _run(_spread(), tolerance=Decimal(-1))


def test_a_passing_sum_check_reports_its_largest_rounding() -> None:
    result = _only(_run(_spread(bs_inventory=499)), "lines_complete", "FY2023")
    assert (result.status, result.difference) == ("pass", Decimal(-1))


# --- Other rules of the checks ---------------------------------------------------------


def test_dividends_and_other_stated_movements_enter_the_rollforward() -> None:
    dividends = _spread(bs_retained_earnings=1200, cf_dividends_paid=-100)
    assert _only(_run(dividends), "re_rollforward", "FY2023").status == "pass"
    other = _spread(bs_retained_earnings=1330, bs_re_other_movements=30)
    assert _only(_run(other), "re_rollforward", "FY2023").status == "pass"


def test_cash_flow_starting_profit_stands_in_for_net_income() -> None:
    spread = _spread(cf_net_income=None, cf_starting_profit=500)
    assert _only(_run(spread), "ni_pl_equals_cf", "FY2023").status == "pass"


def test_combined_liabilities_and_equity_line_is_enough_for_the_balance_check() -> None:
    spread = _spread(bs_total_liabilities=None, bs_total_equity=None)
    result = _only(_run(spread), "bs_balances", "FY2023")
    assert (result.status, result.actual) == ("pass", Decimal(3500))


def test_a_total_printed_without_detail_is_not_a_lost_line() -> None:
    # The base cash flow prints financing as one total with no detail lines.
    result = _only(_run(_spread()), "lines_complete", "FY2023")
    assert result.status == "pass"
    lost = _only(_run(_spread(cf_capex=None)), "lines_complete", "FY2023")
    assert lost.status == "pass"  # investing has no detail left, so there is nothing to compare


def test_a_lost_detail_line_fails_lines_complete() -> None:
    result = _only(_run(_spread(bs_receivables=None)), "lines_complete", "FY2023")
    assert (result.status, result.difference) == ("fail", Decimal(-600))


# --- Inputs ----------------------------------------------------------------------------


def test_periods_must_be_unique_and_cover_the_spread() -> None:
    with pytest.raises(ValueError, match="unique"):
        run_reconciliations(_spread(), periods=("FY2022", "FY2022"))
    with pytest.raises(ValueError, match="FY2023"):
        run_reconciliations(_spread(), periods=("FY2022",))


def test_a_float_amount_is_rejected_not_rounded() -> None:
    spread: dict[str, typing.Any] = _spread()
    spread["FY2023"]["bs_total_assets"] = 3500.0
    with pytest.raises(ValidationError):
        _run(spread)


# --- No plug figure --------------------------------------------------------------------


def _public_functions() -> list[tuple[str, typing.Callable[..., typing.Any]]]:
    found: dict[str, typing.Callable[..., typing.Any]] = {}
    for module in (checks, engine, rules, reconcile_pkg):
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if not name.startswith("_") and fn.__module__.startswith("app.reconcile"):
                found[f"{fn.__module__}.{fn.__name__}"] = fn  # re-exports collapse to one entry
    return list(found.items())


def _flatten(annotation: typing.Any) -> set[typing.Any]:
    args = typing.get_args(annotation)
    if not args:
        return {annotation}
    flat: set[typing.Any] = {typing.get_origin(annotation)}
    for arg in args:
        flat |= _flatten(arg)
    return flat


def test_no_function_in_reconcile_returns_a_number_or_a_spread_shaped_mapping() -> None:
    functions = _public_functions()
    assert {n for n, _ in functions} >= {
        "app.reconcile.engine.run_reconciliations",
        "app.reconcile.checks.run_checks",
    }
    forbidden = {Decimal, int, float, str, dict, typing.Any}
    for name, fn in functions:
        hints = typing.get_type_hints(fn)
        assert "return" in hints, f"{name} needs a return annotation"
        returned = hints["return"]
        if name == "app.reconcile.checks.run_checks":
            # The one dict: check key -> CheckOutput (results and issues, no numbers).
            assert typing.get_args(returned)[1] is checks.CheckOutput
            continue
        leaves = _flatten(returned) - {types.NoneType}
        assert not (leaves & forbidden), f"{name} returns {returned}"


def test_every_figure_in_a_result_comes_from_the_spread() -> None:
    spread = _spread(bs_total_assets=3550, cf_net_change_in_cash=350)
    before = copy.deepcopy(spread)
    report = _run(spread)
    assert spread == before  # nothing was inserted or edited
    for result in report.results:
        for term in result.terms:
            assert spread[term.period][term.line_id] == term.value
        if result.status != "not_applicable":
            assert result.difference == result.actual - result.expected  # type: ignore[operator]


LINE_POOL = sorted({*FY2022, *FY2023})


def test_random_spreads_never_gain_a_figure_and_stay_consistent() -> None:
    rng = random.Random(20261021)  # noqa: S311  # fixed seed: the draw is the same on every run
    for _ in range(300):
        spread: Spread = {
            p: {i: Decimal(rng.randint(-5, 5000)) for i in LINE_POOL if rng.random() < 0.8}
            for p in PERIODS
        }
        before = copy.deepcopy(spread)
        report = _run(spread)
        assert spread == before
        issues = {i.where for i in report.open_issues}
        for result in report.results:
            if result.status == "not_applicable":
                assert f"{result.key}:{result.period}" in issues
                continue
            assert (result.status == "pass") == (abs(result.difference or 0) <= 1)  # type: ignore[arg-type]
            assert result.terms
            for term in result.terms:
                assert spread[term.period][term.line_id] == term.value
        assert len(report.open_issues) == len(
            [r for r in report.results if r.status == "not_applicable"]
        )


# --- Verifier findings -----------------------------------------------------------------


def test_omitted_intermediate_subtotal_is_not_counted_as_zero() -> None:
    # Operating profit is printed but profit before tax is not: 800 - 100 - 200 = 500.
    spread = _spread(is_profit_before_tax=None)
    assert _only(_run(spread), "subtotals_match_source", "FY2023").status == "pass"
    # Total assets printed without total current assets: 900 + 600 + 500 + 1,500.
    spread = _spread(bs_total_current_assets=None)
    assert _only(_run(spread), "subtotals_match_source", "FY2023").status == "pass"
    # The expansion still catches a real difference.
    wrong = _spread(bs_total_current_assets=None, bs_total_assets=3550)
    assert _only(_run(wrong), "subtotals_match_source", "FY2023").status == "fail"


def test_starting_profit_line_is_a_detail_of_operating_cash_flow() -> None:
    spread = _spread(cf_net_income=None, cf_starting_profit=500)
    assert _only(_run(spread), "lines_complete", "FY2023").status == "pass"


def test_rollforward_is_not_applicable_when_dividends_cannot_be_known() -> None:
    no_cf = {
        p: {k: v for k, v in c.items() if not k.startswith("cf_")} for p, c in _spread().items()
    }
    assert _only(_run(no_cf), "re_rollforward", "FY2023").status == "not_applicable"


def test_empty_periods_are_refused() -> None:
    with pytest.raises(ValueError, match="empty"):
        run_reconciliations({}, periods=())
