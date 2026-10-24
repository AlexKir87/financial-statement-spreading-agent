"""Plan step 15: canonical period keys computed in code, and the checks that pair on them."""

from datetime import date
from decimal import Decimal

import pytest

from app.periods import canonical_period, parse_key, parse_period, sort_key
from app.reconcile import rules as lines
from app.reconcile import run_reconciliations


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Three Months Ended June 27, 2026", "2026-03-28..2026-06-27"),
        ("Nine Months Ended June 27, 2026", "2025-09-28..2026-06-27"),
        ("Six months ended June 30 2025", "2025-01-01..2025-06-30"),
        ("Quarter Ended Aug 2, 2026", "2026-05-03..2026-08-02"),
        ("Two Quarters Ended August 2, 2026", "2026-02-03..2026-08-02"),
        ("Three Quarters Ended Jun 28, 2026", "2025-09-29..2026-06-28"),
        ("Fiscal Year Ended September 27, 2025", "2024-09-28..2025-09-27"),
        ("Year ended December 31, 2025", "2025-01-01..2025-12-31"),
        ("Twenty-Six Weeks Ended June 28, 2026", "2025-12-29..2026-06-28"),
        ("Three Months Ended November 30, 2025", "2025-09-01..2025-11-30"),
        ("June 30 2026", "2026-06-30"),
        ("AUGUST 31, 2026", "2026-08-31"),
        ("Sept 27, 2025", "2025-09-27"),
        ("December 31 2025", "2025-12-31"),
    ],
)
def test_headings_become_canonical_keys(label: str, key: str) -> None:
    span = parse_period(label)
    assert span is not None and span.key == key


@pytest.mark.parametrize("label", ["FY2023", "Current", "2026", "Prior", "", "Q3"])
def test_unreadable_headings_are_not_guessed(label: str) -> None:
    assert parse_period(label) is None


def test_a_bare_year_takes_the_months_ended_phrase_of_its_page() -> None:
    page = (
        "UNAUDITED CONDENSED STATEMENTS OF CASH FLOWS\nTHREE MONTHS ENDED AUGUST 31,\n2026 2025\n"
    )
    assert canonical_period("2026", page) == "2026-06-01..2026-08-31"
    assert canonical_period("2025", page) == "2025-06-01..2025-08-31"


def test_a_bare_year_without_such_a_phrase_stays_unresolved() -> None:
    assert canonical_period("2026", "Revenue 5,000") is None


def test_a_full_heading_ignores_the_page_phrase() -> None:
    page = "SIX MONTHS ENDED JUNE 30,\n2026 2025"
    assert canonical_period("Three Months Ended June 27, 2026", page) == "2026-03-28..2026-06-27"


def test_keys_round_trip_and_sort_oldest_first_longest_first() -> None:
    keys = ["2026-06-27", "2025-09-28..2026-06-27", "2026-03-28..2026-06-27", "2025-09-27"]
    for k in keys:
        span = parse_key(k)
        assert span is not None and span.key == k
    assert sorted(keys, key=sort_key) == [
        "2025-09-27",
        "2025-09-28..2026-06-27",
        "2026-03-28..2026-06-27",
        "2026-06-27",
    ]
    assert parse_key("FY2023") is None
    assert parse_key("2026-06-27").end == date(2026, 6, 27)  # type: ignore[union-attr]


# --- the checks pair statements by key --------------------------------------------------

BS_PRIOR, BS_NOW = "2025-09-27", "2026-06-27"
YTD, QTR = "2025-09-28..2026-06-27", "2026-03-28..2026-06-27"


def _spread(cash_change: int = 300, cf_ni: int = 500) -> dict[str, dict[str, Decimal]]:
    d = Decimal
    return {
        BS_PRIOR: {
            lines.BS_CASH: d(600),
            lines.BS_RETAINED_EARNINGS: d(800),
            lines.BS_TOTAL_ASSETS: d(1000),
            lines.BS_TOTAL_LIABILITIES_AND_EQUITY: d(1000),
        },
        BS_NOW: {
            lines.BS_CASH: d(900),
            lines.BS_RETAINED_EARNINGS: d(1300),
            lines.BS_TOTAL_ASSETS: d(1500),
            lines.BS_TOTAL_LIABILITIES_AND_EQUITY: d(1500),
        },
        YTD: {
            lines.IS_NET_INCOME: d(500),
            lines.CF_NET_INCOME: d(cf_ni),
            lines.CF_NET_CHANGE_IN_CASH: d(cash_change),
        },
        QTR: {lines.IS_NET_INCOME: d(200)},
    }


def _periods() -> list[str]:
    return sorted(_spread(), key=sort_key)


def _results(spread: dict[str, dict[str, Decimal]]) -> dict[tuple[str, str], str]:
    report = run_reconciliations(spread, periods=_periods())
    return {(r.key, r.period): r.status for r in report.results}


def test_each_check_runs_on_the_periods_it_belongs_to() -> None:
    got = _results(_spread())
    assert got[("bs_balances", BS_PRIOR)] == "pass" and got[("bs_balances", BS_NOW)] == "pass"
    assert ("bs_balances", YTD) not in got and ("bs_balances", QTR) not in got
    assert got[("cf_cash_reconciles", YTD)] == "pass"
    assert got[("ni_pl_equals_cf", YTD)] == "pass"
    assert ("ni_pl_equals_cf", BS_NOW) not in got and ("cf_cash_reconciles", BS_NOW) not in got
    assert got[("re_rollforward", YTD)] == "pass"


def test_a_wrong_cash_change_fails_on_the_year_to_date_key() -> None:
    got = _results(_spread(cash_change=250))
    assert got[("cf_cash_reconciles", YTD)] == "fail"


def test_a_net_income_mismatch_fails_on_the_year_to_date_key() -> None:
    assert _results(_spread(cf_ni=450))[("ni_pl_equals_cf", YTD)] == "fail"


def test_a_quarter_without_a_cash_flow_is_not_checked_against_one() -> None:
    got = _results(_spread())
    assert got.get(("ni_pl_equals_cf", QTR), "not_applicable") == "not_applicable"
    assert got.get(("cf_cash_reconciles", QTR), "not_applicable") == "not_applicable"


def test_a_prior_balance_sheet_within_a_week_of_the_start_is_found() -> None:
    spread = _spread()
    spread["2025-09-28"] = spread.pop(BS_PRIOR)  # printed a day late: still the prior balance sheet
    report = run_reconciliations(spread, periods=sorted(spread, key=sort_key))
    status = {(r.key, r.period): r.status for r in report.results}
    assert status[("cf_cash_reconciles", YTD)] == "pass"


def test_no_prior_balance_sheet_is_not_applicable_with_an_issue() -> None:
    spread = _spread()
    del spread[BS_PRIOR]
    report = run_reconciliations(spread, periods=sorted(spread, key=sort_key))
    status = {(r.key, r.period): r.status for r in report.results}
    assert status[("cf_cash_reconciles", YTD)] == "not_applicable"
    assert any(i.where == f"cf_cash_reconciles:{YTD}" for i in report.open_issues)


# --- extraction applies the keys ---------------------------------------------------------

from tests.test_extract_map import FakeClient, caller_for, extraction, line, page  # noqa: E402


def _extract(text: str, period: str) -> tuple[list[str], list[str | None], list[str]]:
    from app.extract import extract_statements

    reply = extraction([line("Net income", period, "500", "CF")])
    result = extract_statements([page(text=text)], caller_for(FakeClient(reply)))
    return (
        [rl.period for rl in result.raw_lines],
        [rl.period_label for rl in result.raw_lines],
        [i.code for i in result.open_issues],
    )


def test_extraction_stores_the_key_and_keeps_the_printed_heading() -> None:
    text = "CASH FLOWS Nine Months Ended June 27, 2026\nNet income 500"
    keys, labels, issues = _extract(text, "Nine Months Ended June 27, 2026")
    assert keys == ["2025-09-28..2026-06-27"] and labels == ["Nine Months Ended June 27, 2026"]
    assert "period_not_canonical" not in issues


def test_extraction_resolves_a_bare_year_from_the_page() -> None:
    keys, labels, _ = _extract("THREE MONTHS ENDED AUGUST 31,\nNet income 500", "2026")
    assert keys == ["2026-06-01..2026-08-31"] and labels == ["2026"]


def test_a_date_like_heading_that_cannot_be_read_keeps_its_text_with_an_open_issue() -> None:
    keys, _, issues = _extract("Net income 500", "Ended in the spring of 2026")
    assert keys == ["Ended in the spring of 2026"] and "period_not_canonical" in issues


def test_a_plain_label_stays_as_printed_without_an_issue() -> None:
    keys, _, issues = _extract("Net income 500", "FY2023")
    assert keys == ["FY2023"] and "period_not_canonical" not in issues


def test_a_plain_date_on_a_cash_flow_line_takes_the_page_phrase() -> None:
    page = "CASH FLOWS\nNine Months Ended June 27, 2026 June 28, 2025\nNet income 500"
    assert canonical_period("June 27, 2026", page, "CF") == "2025-09-28..2026-06-27"
    assert canonical_period("June 28, 2025", page, "CF") == "2024-09-29..2025-06-28"


def test_a_plain_date_on_a_balance_sheet_stays_a_balance_sheet_date() -> None:
    page = "BALANCE SHEETS\nSix months ended June 27, 2026 (note)\nCash 500"
    assert canonical_period("June 27, 2026", page, "BS") == "2026-06-27"


def test_a_page_with_two_phrases_does_not_resolve_a_plain_date_or_a_bare_year() -> None:
    page = "Three Months Ended June 27, 2026   Nine Months Ended June 27, 2026\nRevenue 5"
    assert canonical_period("June 27, 2026", page, "IS") is None
    assert canonical_period("2026", page, "IS") is None


# --- verifier findings: compound numbers, balance sheet years, ambiguity, missing statements ---


@pytest.mark.parametrize(
    ("label", "key"),
    [
        ("Thirty-nine weeks ended June 27, 2026", "2025-09-28..2026-06-27"),
        ("Twenty-four Weeks Ended June 27, 2026", "2026-01-11..2026-06-27"),
        ("Thirty-six weeks ended June 27, 2026", "2025-10-19..2026-06-27"),
        ("39 Weeks Ended June 27, 2026", "2025-09-28..2026-06-27"),
        ("Years Ended December 31, 2025", "2025-01-01..2025-12-31"),
    ],
)
def test_compound_number_words_and_plural_years(label: str, key: str) -> None:
    span = parse_period(label)
    assert span is not None and span.key == key


def test_a_bare_year_balance_sheet_column_is_a_date_never_a_duration() -> None:
    page = "Three months ended June 30\nBALANCE SHEET\n2026 2025"
    assert canonical_period("2026", page, "BS") == "2026-06-30"
    assert canonical_period("2025", page, "BS") == "2025-06-30"
    assert canonical_period("2026", "Cash 5", "BS") is None


def test_three_and_nine_months_is_two_phrases_so_a_bare_year_stays_unresolved() -> None:
    page = "For the three and nine months ended September 30\n2026 2025"
    assert canonical_period("2026", page, "IS") is None


def test_a_bare_year_takes_the_date_whose_year_matches_on_a_52_week_calendar() -> None:
    page = "52 Weeks Ended January 31, 2026 and February 1, 2025\n2026 2025"
    assert canonical_period("2026", page, "IS") == "2025-02-02..2026-01-31"
    assert canonical_period("2025", page, "IS") == "2024-02-04..2025-02-01"


def test_a_missing_balance_sheet_on_canonical_keys_is_reported() -> None:
    spread = {YTD: {lines.IS_NET_INCOME: Decimal(500), lines.CF_NET_INCOME: Decimal(500)}}
    report = run_reconciliations(spread, periods=[YTD])
    assert any(i.code == "statement_missing_in_source" for i in report.open_issues)
    keys = {r.key for r in report.results}
    assert "bs_balances" in keys


def test_a_missing_cash_flow_and_income_statement_on_canonical_keys_is_reported() -> None:
    spread = {
        BS_NOW: {
            lines.BS_TOTAL_ASSETS: Decimal(5),
            lines.BS_TOTAL_LIABILITIES_AND_EQUITY: Decimal(5),
        }
    }
    report = run_reconciliations(spread, periods=[BS_NOW])
    status = {r.key: r.status for r in report.results}
    assert status["ni_pl_equals_cf"] == status["cf_cash_reconciles"] == "not_applicable"
    assert any(i.code == "statement_missing_in_source" for i in report.open_issues)
    assert all(
        any(i.where.startswith(f"{k}:") for i in report.open_issues)
        for k in ("ni_pl_equals_cf", "cf_cash_reconciles", "re_rollforward")
    )


def test_no_prior_balance_sheet_at_all_names_the_missing_statement() -> None:
    spread = {YTD: {lines.CF_NET_CHANGE_IN_CASH: Decimal(5), lines.IS_NET_INCOME: Decimal(5)}}
    report = run_reconciliations(spread, periods=[YTD])
    assert any(
        i.code == "statement_missing_in_source" and i.where.startswith("cf_cash_reconciles:")
        for i in report.open_issues
    )


# --- second verifier round ------------------------------------------------------------------


@pytest.mark.parametrize("dash", ["–", "—", "‑", "‐", "−"])
def test_compound_numbers_with_any_dash_character(dash: str) -> None:
    span = parse_period(f"Thirty{dash}nine weeks ended June 27, 2026")
    assert span is not None and span.key == "2025-09-28..2026-06-27"
    page = f"Thirteen and Thirty{dash}nine Weeks Ended June 27\n2026 2025"
    assert canonical_period("2026", page, "IS") is None  # two phrases: nothing rescued


def test_a_nearby_unrelated_date_is_not_taken_as_the_period_end() -> None:
    page = "Three months ended June 30\nAdopted ASU on January 1, 2026\n2026 2025"
    assert canonical_period("2026", page, "IS") == "2026-04-01..2026-06-30"


@pytest.mark.parametrize(
    "page",
    [
        "Sixteen Weeks Ended June 14\nForty Weeks Ended June 14\n2026 2025",
        "For the Three Months and Nine Months Ended September 30\n2026 2025",
        "Quarter Ended June 30\nSix Months Ended June 30\n2026 2025",
        "Three Months Ended June 30 and a note on the Year Ended December 31\n2026",
    ],
)
def test_two_periods_on_one_page_are_never_resolved_to_one(page: str) -> None:
    assert canonical_period("2026", page, "IS") is None


def test_sixteen_week_quarters_are_read() -> None:
    span = parse_period("Sixteen Weeks Ended June 14, 2026")
    assert span is not None and span.days == 112
    assert canonical_period("2026", "Sixteen Weeks Ended June 14\n2026 2025", "IS") is not None


def test_a_page_phrase_with_no_number_is_a_quarter() -> None:
    assert canonical_period("2026", "Quarter Ended June 30\n2026 2025", "IS") == (
        "2026-04-01..2026-06-30"
    )


@pytest.mark.parametrize(
    "page",
    [
        "Three Months Ended    Six Months Ended\nJune 30,\n2026 2025 2026 2025",
        "Three Months Ended    Six Months Ended\nJune 30, June 30,\n2026 2025 2026 2025",
        "For the Three Months Ended    For the Six Months Ended June 30,\n2026 2025",
        "Three Months Ended\tNine Months Ended\nSeptember 30,\n2026 2026",
        "Thirteen Weeks Ended    Twenty-Six Weeks Ended\nJune 27, 2026 2026",
        "Three Months Ended June 30 Six Months\n2026 2025 2026 2025",
        "Quarter Ended June 30 Year to Date June 30\n2026 2026",
    ],
)
def test_two_headers_over_one_date_line_are_not_resolved(page: str) -> None:
    assert canonical_period("2026", page, "IS") is None


@pytest.mark.parametrize(
    "text",
    [
        "Thirty - nine weeks ended June 27, 2026",
        "Thirty-\nnine weeks ended June 27, 2026",
        "Thirty  nine weeks ended June 27, 2026",
        "Thirty­nine weeks ended June 27, 2026",
        "Thirty nine weeks ended June 27, 2026",
        "Thirty nine weeks ended June 27, 2026",
        "Thirty－nine weeks ended June 27, 2026",
        "One hundred four weeks ended June 27, 2026",
    ],
)
def test_compound_counts_never_read_as_their_last_word(text: str) -> None:
    key = canonical_period(text)
    # 39 weeks or unread, never 9 or 4 weeks
    assert key in (None, "2025-09-28..2026-06-27")


@pytest.mark.parametrize(
    "text",
    [
        "Half year ended June 30, 2026",
        "Half-year ended June 30, 2026",
        "Five years ended December 31, 2026",
        "2 years ended December 31, 2026",
    ],
)
def test_multi_year_and_half_year_headings_are_not_twelve_months(text: str) -> None:
    assert canonical_period(text) is None
    assert canonical_period("2026", text + "\n2026", "IS") is None


def test_a_footnote_date_near_the_phrase_is_not_taken() -> None:
    page = "Three months ended June 30\nsee Note 3 (July 2, 2026 offering)\n2026 2025"
    assert canonical_period("2026", page, "IS") == "2026-04-01..2026-06-30"


@pytest.mark.parametrize(
    "label",
    [
        "Year ended December 31, 2025 and three months ended March 31, 2026",
        "Six months ended June 30, 2026 and the quarter ended June 30, 2026",
        "Quarter ended March 31, 2026 and nine months ended Sept 30, 2026",
        "Three months ended June 30, 2026 compared with year ended December 31, 2025",
        "Quarter Ended June 30, 2026 Quarter Ended June 30, 2025",
        "Quarters Ended June 30, 2026 Quarters Ended June 30, 2025",
        "Three Months Ended June 30, 2026 Quarter Ended June 30, 2025",
    ],
)
def test_a_label_naming_two_periods_is_not_read(label: str) -> None:
    assert canonical_period(label) is None


@pytest.mark.parametrize("text", ["0 months ended Jan 3, 2027", "99 months ended Jan 3, 2027"])
def test_absurd_counts_are_not_read(text: str) -> None:
    assert canonical_period(text) is None


def test_plural_quarters_ended_is_one_quarter() -> None:
    assert canonical_period("Quarters Ended June 30, 2026") == "2026-04-01..2026-06-30"
    assert canonical_period("Quarter Ended June 30, 2026") == "2026-04-01..2026-06-30"


def test_plural_quarters_on_the_page_rescue_a_bare_year() -> None:
    page = "Quarters Ended\nJune 30,\n2026 2025"
    assert canonical_period("2026", page, "IS") == "2026-04-01..2026-06-30"


def test_a_pair_of_columns_under_one_year_gets_two_keys_when_written_out_in_full() -> None:
    page = "Quarters Ended Six Months Ended\nJune 30, June 30,\n2026 2025 2026 2025"
    keys = [
        canonical_period(label, page, "IS")
        for label in (
            "Quarters Ended June 30, 2026",
            "Six Months Ended June 30, 2026",
            "Quarters Ended June 30, 2025",
            "Six Months Ended June 30, 2025",
        )
    ]
    assert keys == [
        "2026-04-01..2026-06-30",
        "2026-01-01..2026-06-30",
        "2025-04-01..2025-06-30",
        "2025-01-01..2025-06-30",
    ]
    assert len(set(keys)) == 4


def test_a_written_out_heading_with_a_year_the_page_does_not_print_is_refused() -> None:
    page = "Quarters Ended Six Months Ended\nJune 30,\n2026 2025"
    assert canonical_period("Six Months Ended June 30, 2031", page, "IS") is None
