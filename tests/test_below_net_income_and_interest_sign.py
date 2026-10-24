"""Plan step 27: lines printed below net income are memo lines, and interest expense printed
positive is read with the sign the checks expect. Labels and numbers are invented."""

from decimal import Decimal

from app.mapping import load_mapping_table
from app.reconcile import run_reconciliations
from app.repair import build_spread
from app.sections import row_sections
from app.state import MappedLine, RawLine, mapping_key

PERIOD = "2026-01-01..2026-06-30"
BELOW = "is_below_net_income"


def test_step27_the_memo_line_exists() -> None:
    kinds = {ln.line_id: (ln.statement, ln.kind) for ln in load_mapping_table().lines}
    assert kinds[BELOW] == ("IS", "memo")


def test_step27_rows_after_the_printed_net_income_have_their_own_section() -> None:
    rows = [
        "Income before taxes 500",
        "Income tax expense 100",
        "Net income 400",
        "Foreign currency translation adjustment (30)",
        "Comprehensive income 370",
        "Basic earnings per share 2.92",
    ]
    assert row_sections("IS", rows) == [
        None,
        None,
        None,
        "below_net_income",
        "below_net_income",
        "below_net_income",
    ]


def test_step27_a_heading_still_opens_its_section_below_net_income() -> None:
    rows = [
        "Net loss (428)",
        "Less: earnings attributable to noncontrolling interest 16",
        "Loss per share",
        "Basic (0.67)",
        "Weighted average shares outstanding:",
        "Basic 700",
    ]
    assert row_sections("IS", rows) == [
        None,
        "below_net_income",
        "per_share",
        "per_share",
        "share_count",
        "share_count",
    ]


def test_step27_net_income_that_names_a_group_does_not_open_the_section() -> None:
    rows = [
        "Net earnings including noncontrolling interests 1,044.9",
        "Net earnings attributable to noncontrolling interests (0.4)",
        "Net earnings attributable to the parent 1,045.3",
    ]
    assert row_sections("IS", rows) == [None, None, None]


def test_step27_a_total_row_below_net_income_does_not_close_the_section() -> None:
    rows = ["Net income 400", "Other comprehensive income (50)", "Total comprehensive income 350"]
    assert row_sections("IS", rows)[-1] == "below_net_income"


def test_step27_below_net_income_labels_are_memo_and_per_share_rules_still_apply() -> None:
    table = load_mapping_table()
    sec = "below_net_income"
    assert table.lookup("IS", "Comprehensive income", sec) == BELOW
    noncontrolling = "Less: net earnings attributable to noncontrolling interests"
    assert table.lookup("IS", noncontrolling, sec) == BELOW
    assert table.lookup("IS", "Other income (expense), net", sec) == BELOW
    assert table.lookup("IS", "Basic earnings per share", sec) == "is_eps_basic"
    # outside the section a label with no rule is left to the model, a rule keeps working
    assert table.lookup("IS", "Comprehensive income", None) is None
    assert table.lookup("IS", "Net income", None) == "is_net_income"


def _case(rows: list[tuple[str, str, str]]):
    lines, mapping = [], {}
    for n, (line_id, label, text) in enumerate(rows, start=1):
        line = RawLine(
            raw_line_id=f"r{n}",
            doc_id="doc1",
            page=1,
            original_label=label,
            original_value_text=text,
            statement="IS",
            period=PERIOD,
            scale="thousands",
            currency="USD",
        )
        lines.append(line)
        mapping[mapping_key(PERIOD, line.raw_line_id)] = MappedLine(
            raw_line_id=line.raw_line_id,
            period=PERIOD,
            standard_line_id=line_id,
            confidence=0.9,
            rationale="x",
            origin="model",
        )
    return lines, mapping


INTEREST_POSITIVE = [
    ("is_revenue", "Revenues", "1,000"),
    ("is_operating_expenses", "Operating costs", "(400)"),
    ("is_operating_profit", "Operating income", "600"),
    ("is_interest_expense", "Interest expense", "90"),
    ("is_non_operating_items", "Nonoperating (income) expense, net", "(6)"),
    ("is_profit_before_tax", "Income before taxes", "516"),
    ("is_income_tax", "Income taxes", "(100)"),
    ("is_net_income", "Net income", "416"),
]


def test_step27_interest_and_other_items_printed_as_expenses_take_the_checks_sign() -> None:
    lines, mapping = _case(INTEREST_POSITIVE)
    notes: list[str] = []
    cells = build_spread(lines, mapping, notes=notes)[PERIOD]
    assert cells["is_interest_expense"] == Decimal(-90)
    assert cells["is_non_operating_items"] == Decimal(6)
    assert cells["is_income_tax"] == Decimal(-100)
    assert sorted(notes) == [f"{PERIOD} is_interest_expense", f"{PERIOD} is_non_operating_items"]


def test_step27_interest_printed_negative_is_left_alone() -> None:
    rows = [
        ("is_operating_profit", "Operating income", "600"),
        ("is_interest_expense", "Interest expense", "(90)"),
        ("is_profit_before_tax", "Income before taxes", "510"),
    ]
    lines, mapping = _case(rows)
    notes: list[str] = []
    cells = build_spread(lines, mapping, notes=notes)[PERIOD]
    assert cells["is_interest_expense"] == Decimal(-90)
    assert notes == []


def test_step27_a_subtotal_that_fits_no_reading_changes_nothing() -> None:
    rows = [
        ("is_operating_profit", "Operating income", "600"),
        ("is_interest_expense", "Interest expense", "90"),
        ("is_profit_before_tax", "Income before taxes", "400"),
    ]
    lines, mapping = _case(rows)
    cells = build_spread(lines, mapping)[PERIOD]
    assert cells["is_interest_expense"] == Decimal(90)


def test_step27_the_income_statement_checks_pass_after_the_signs_are_read() -> None:
    lines, mapping = _case(INTEREST_POSITIVE)
    report = run_reconciliations(build_spread(lines, mapping), periods=[PERIOD])
    by_key = {r.key: r.status for r in report.results}
    assert by_key["lines_complete"] == "pass"
    assert by_key["subtotals_match_source"] == "pass"


def test_step27_rows_between_operating_profit_and_profit_before_tax_are_non_operating() -> None:
    rows = [
        "Operating income 600",
        "Gain on divestiture 50",
        "Interest expense (90)",
        "Income before taxes 560",
        "Income tax expense (100)",
        "Net income 460",
    ]
    assert row_sections("IS", rows) == [
        None,
        "non_operating",
        "non_operating",
        None,
        None,
        None,
    ]
    table = load_mapping_table()
    assert table.lookup("IS", "Gain on divestiture", "non_operating") == "is_non_operating_items"
    assert table.lookup("IS", "Interest expense", "non_operating") == "is_interest_expense"
    assert table.lookup("IS", "Income before taxes", None) is None


def test_step27_a_statement_without_a_profit_before_tax_row_ends_the_block_at_tax() -> None:
    rows = ["Operating income 600", "Other, net 5", "Income tax expense (100)", "Net income 505"]
    assert row_sections("IS", rows) == [None, "non_operating", None, None]


def test_step27_other_items_between_the_costs_and_operating_profit_are_operating_income() -> None:
    rows = [
        "Gross profit 4,524",
        "Other income, net 18",
        "Operating profit 4,542",
        "Interest expense (535)",
    ]
    assert row_sections("IS", rows) == [None, "operating_block", None, "non_operating"]
    from app.mapping.lines import section_line

    assert section_line("IS", "operating_block", "is_other_items") == "is_operating_income_items"
    assert section_line("IS", "operating_block", "is_operating_expenses") == "is_operating_expenses"
    assert section_line("IS", None, "is_other_items") == "is_other_items"


def test_step27_cash_flow_rows_after_net_financing_have_their_own_section() -> None:
    rows = [
        "Cash flows from financing activities",
        "Other 32",
        "Net cash used by financing activities (8,513)",
        "Effect of exchange rate changes on cash 2",
        "Cash at end of period 7,239",
        "Interest paid 457",
    ]
    assert row_sections("CF", rows) == [
        "financing",
        "financing",
        "financing",
        "cash_summary",
        "cash_summary",
        "cash_summary",
    ]


def test_step27_a_detail_cash_line_in_the_cash_summary_becomes_a_memo_line() -> None:
    from app.mapping.lines import summary_detail_to_memo

    assert (
        summary_detail_to_memo("CF", "cash_summary", "cf_other_financing")
        == "cf_supplemental_items"
    )
    assert summary_detail_to_memo("CF", "cash_summary", "cf_cash_end") == "cf_cash_end"
    assert summary_detail_to_memo("CF", "cash_summary", "cf_fx_effect") == "cf_fx_effect"
    assert summary_detail_to_memo("CF", "financing", "cf_other_financing") == "cf_other_financing"
    assert summary_detail_to_memo("IS", "cash_summary", "is_other_items") == "is_other_items"
