"""Printed sections (plan step 19): a label's meaning follows the heading above its row."""

from pathlib import Path

import pytest

from app.extract import extract_statements
from app.mapping import load_mapping_table, map_lines
from app.mapping.table import DEFAULT_DIR, MappingTableError
from app.sections import heading_section, row_sections
from app.state import DigitizedPage, RawLine, mapping_key
from tests.test_extract_map import FakeClient, caller_for, extraction, line

CASH_FLOW = """CONSOLIDATED STATEMENT OF CASH FLOWS
Quarters Ended Six Months Ended
In millions 2026 2025
Operating activities
Net income $ 1,742 $ 1,655
Other ( 33 ) ( 44 )
Cash provided by operations 2,115 1,890
Investing activities
Capital expenditures ( 412 ) ( 380 )
Other ( 61 ) ( 58 )
Cash used for investing activities ( 455 ) ( 448 )
Financing activities
Other ( 90 ) ( 77 )
Cash used for financing activities ( 1,310 ) ( 287 )
Cash and equivalents at beginning of period 2,100 1,900
Cash and equivalents at end of period 1,500 1,800"""

INCOME = """Consolidated Statements of Earnings
Sales
Products $ 7,411 $ 6,902
Services 1,215 1,104
Total sales 8,626 8,006
Operating costs and expenses
Products ( 5,310 ) ( 5,102 )
Services ( 980 ) ( 1,064 )
Gross profit 2,336 734"""

BALANCE = """Consolidated Balance Sheets
Assets
Current assets
Cash 100 90
Deferred income taxes 107 99
Total assets 500 450
Liabilities and equity
Current liabilities
Accounts payable 70 60
Deferred income taxes 12 11
Equity
Retained earnings 300 280"""


@pytest.mark.parametrize(
    ("statement", "row", "section"),
    [
        ("CF", "Cash flows – operating activities:", "operating"),
        ("CF", "Investing activities", "investing"),
        ("CF", "FINANCING ACTIVITIES:", "financing"),
        ("CF", "Net cash used for investing activities ( 890 )", None),  # a total, has digits
        ("IS", "Sales", "revenue"),
        ("IS", "Operating costs and expenses", "costs"),
        ("IS", "Products 1,000", None),
        ("BS", "Current assets", "assets"),
        ("BS", "Current liabilities:", "liabilities"),
        ("BS", "Liabilities and shareholders' equity", "liabilities"),
        ("BS", "Total stockholders' equity", "equity"),
        ("BS", "Total assets 5,000", None),
        ("XX", "Operating activities", None),
        ("CF", "Change in restricted cash related to financing activities", None),
        ("BS", "Equity method investments — —", None),
        ("BS", "Deferred tax liabilities, current — —", None),
        ("IS", "Cost of sales:", "costs"),
        ("IS", "Cost of goods sold", "costs"),
    ],
)
def test_heading_rows_open_a_section(statement: str, row: str, section: str | None) -> None:
    assert heading_section(statement, row) == section


def test_a_row_takes_the_last_heading_above_it() -> None:
    rows = CASH_FLOW.splitlines()
    sections = dict(zip(rows, row_sections("CF", rows), strict=True))
    assert sections["Other ( 33 ) ( 44 )"] == "operating"
    assert sections["Other ( 61 ) ( 58 )"] == "investing"
    assert sections["Other ( 90 ) ( 77 )"] == "financing"
    assert sections["CONSOLIDATED STATEMENT OF CASH FLOWS"] is None


def _page(text: str) -> DigitizedPage:
    return DigitizedPage(
        doc_id="doc1", page=1, text=text, digitizer_name="pdf-text-layer", digitizer_version="1"
    )


def test_extraction_records_the_section_of_each_line() -> None:
    reply = extraction(
        [
            line("Other", "2026", "( 33 )", "CF"),
            line("Other", "2026", "( 61 )", "CF"),
            line("Other", "2026", "( 90 )", "CF"),
            line("Capital expenditures", "2026", "( 412 )", "CF"),
        ],
        scale="millions",
    )
    result = extract_statements([_page(CASH_FLOW)], caller_for(FakeClient(reply)))
    assert [r.section for r in result.raw_lines] == [
        "operating",
        "investing",
        "financing",
        "investing",
    ]


def test_the_same_label_and_value_under_two_headings_has_no_section() -> None:
    text = "Operating activities\nOther ( 5 ) ( 6 )\nInvesting activities\nOther ( 5 ) ( 7 )"
    reply = extraction([line("Other", "2026", "( 5 )", "CF")], scale="millions")
    result = extract_statements([_page(text)], caller_for(FakeClient(reply)))
    assert [r.section for r in result.raw_lines] == [None]


def _raw(rid: str, label: str, statement: str, section: str | None) -> RawLine:
    return RawLine(
        raw_line_id=rid,
        doc_id="doc1",
        page=1,
        original_label=label,
        original_value_text="1",
        statement=statement,  # type: ignore[arg-type]
        period="FY2023",
        section=section,
        scale="millions",
        currency="USD",
    )


def _mapped_ids(lines: list[RawLine]) -> dict[str, str]:
    client = FakeClient()
    result = map_lines(lines, load_mapping_table(), caller_for(client))
    assert client.calls == []  # every one of these is a rule hit
    return {k: v.standard_line_id for k, v in result.mapping.items()}


def test_section_rules_place_other_lines_without_calling_the_model() -> None:
    ids = _mapped_ids(
        [
            _raw("a", "Other", "CF", "operating"),
            _raw("b", "Other", "CF", "investing"),
            _raw("c", "Other, net", "CF", "financing"),
            _raw("d", "Deferred income taxes", "BS", "assets"),
            _raw("e", "Deferred income taxes", "BS", "liabilities"),
            _raw("f", "Products", "IS", "revenue"),
            _raw("g", "Products", "IS", "costs"),
            _raw("h", "Services", "IS", "costs"),
        ]
    )
    assert ids == {
        mapping_key("FY2023", "a"): "cf_other_operating",
        mapping_key("FY2023", "b"): "cf_other_investing",
        mapping_key("FY2023", "c"): "cf_other_financing",
        mapping_key("FY2023", "d"): "bs_other_noncurrent_assets",
        mapping_key("FY2023", "e"): "bs_other_liabilities",
        mapping_key("FY2023", "f"): "is_revenue",
        mapping_key("FY2023", "g"): "is_cost_of_sales",
        mapping_key("FY2023", "h"): "is_cost_of_sales",
    }


def test_opening_and_closing_cash_have_their_own_lines() -> None:
    ids = _mapped_ids(
        [
            _raw("a", "Cash and cash equivalents at beginning of period", "CF", None),
            _raw("b", "Cash and cash equivalents, end of period", "CF", None),
            _raw("c", "Accrued liabilities", "BS", None),
        ]
    )
    assert ids == {
        mapping_key("FY2023", "a"): "cf_cash_beginning",
        mapping_key("FY2023", "b"): "cf_cash_end",
        mapping_key("FY2023", "c"): "bs_other_liabilities",
    }


def test_a_section_rule_does_not_apply_without_a_section() -> None:
    table = load_mapping_table()
    assert table.lookup("CF", "Other") is None
    assert table.lookup("CF", "Other", "investing") == "cf_other_investing"
    assert table.lookup("CF", "Other", "none-such") is None


def test_the_model_sees_the_section() -> None:
    client = FakeClient(
        '{"mappings": [{"key": "k1", "standard_line_id": "cf_other_operating",'
        ' "confidence": 0.9, "rationale": "by section"}]}'
    )
    map_lines([_raw("a", "Sundry", "CF", "operating")], load_mapping_table(), caller_for(client))
    assert "k1 | CF | operating | Sundry" in client.calls[0]["user"]


def test_income_and_balance_sections_follow_the_printed_headings() -> None:
    income = INCOME.splitlines()
    got = dict(zip(income, row_sections("IS", income), strict=True))
    assert got["Products $ 7,411 $ 6,902"] == "revenue"
    assert got["Products ( 5,310 ) ( 5,102 )"] == "costs_to_gross_profit"
    balance = BALANCE.splitlines()
    got = dict(zip(balance, row_sections("BS", balance), strict=True))
    assert got["Deferred income taxes 107 99"] == "assets"
    assert got["Deferred income taxes 12 11"] == "liabilities"
    assert got["Retained earnings 300 280"] == "equity"


def test_an_income_statement_subtotal_closes_the_section() -> None:
    rows = [
        "Net revenues:",
        "Company-operated stores 1,000 900",
        "Total net revenues 1,200 1,100",
        "Product and distribution costs 300 280",
        "Operating income 500 450",
        "Income tax expense 120 110",
    ]
    assert row_sections("IS", rows) == ["revenue", "revenue", "revenue", None, None, None]


def test_gross_profit_closes_the_cost_block() -> None:
    income = INCOME.splitlines() + ["Other income, net 33 14"]
    got = row_sections("IS", income)
    assert got[-1] == "operating_block"  # between gross profit and operating profit (step 27)
    assert got[income.index("Products ( 5,310 ) ( 5,102 )")] == "costs_to_gross_profit"


def test_a_cost_block_with_a_cost_of_sales_heading_closes_the_revenue_section() -> None:
    rows = [
        "Net sales:",
        "Products 100 90",
        "Services 50 40",
        "Total 150 130",
        "Cost of sales:",
        "Products ( 40 ) ( 36 )",
        "Gross profit 110 94",
        "Other income (expense), net 3 2",
    ]
    assert row_sections("IS", rows) == [
        "revenue",
        "revenue",
        "revenue",
        "revenue",
        "costs_to_gross_profit",
        "costs_to_gross_profit",
        "costs_to_gross_profit",
        "operating_block",
    ]


def test_total_costs_and_income_from_operations_close_the_costs_section() -> None:
    rows = ["Costs and expenses", "Rent 5 4", "Total costs and expenses 5 4", "Interest income 1 1"]
    assert row_sections("IS", rows)[-1] != "costs"
    rows = [*rows[:3], "Income from operations 9 8", "Interest income 1 1"]
    assert row_sections("IS", rows)[-2:] == [None, "non_operating"]


def test_a_section_rule_with_an_unknown_section_is_rejected(tmp_path: Path) -> None:
    for name in ("standard_lines.csv", "mapping_rules.csv"):
        (tmp_path / name).write_text((DEFAULT_DIR / name).read_text(encoding="utf-8"))
    (tmp_path / "section_rules.csv").write_text(
        "statement,section,label,standard_line_id\nCF,investng,Other,cf_other_investing\n"
    )
    with pytest.raises(MappingTableError, match="section_rules.csv"):
        load_mapping_table(tmp_path)


def test_a_cost_block_followed_by_gross_profit_is_cost_of_sales() -> None:
    rows = [
        "Sales",
        "Total sales 100 90",
        "Costs and expenses",
        "Products ( 40 ) ( 36 )",
        "Other unallocated, net ( 5 ) ( 4 )",
        "Total operating costs and expenses ( 45 ) ( 40 )",
        "Gross profit 55 50",
        "Other income, net 3 2",
    ]
    assert row_sections("IS", rows) == [
        "revenue",
        "revenue",
        "costs_to_gross_profit",
        "costs_to_gross_profit",
        "costs_to_gross_profit",
        "costs_to_gross_profit",
        None,
        "operating_block",
    ]


def test_a_cost_block_followed_by_operating_income_stays_costs() -> None:
    rows = [
        "Revenues",
        "Total revenues 100 90",
        "Operating costs and expenses",
        "Food and paper ( 30 ) ( 28 )",
        "Other ( 5 ) ( 4 )",
        "Total operating costs and expenses ( 35 ) ( 32 )",
        "Operating income 65 58",
    ]
    assert row_sections("IS", rows) == [
        "revenue",
        "revenue",
        "costs",
        "costs",
        "costs",
        "costs",
        None,
    ]


def test_generic_other_lines_follow_the_kind_of_cost_block() -> None:
    table = load_mapping_table()
    for label in ("Other", "Other operating (income) expense, net", "Other unallocated, net"):
        assert table.lookup("IS", label, "costs") == "is_operating_expenses"
        assert table.lookup("IS", label, "costs_to_gross_profit") == "is_cost_of_sales"
