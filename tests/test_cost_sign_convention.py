"""Plan step 24: costs printed as positive numbers are read with the sign the checks expect.
Labels and numbers are invented; no client data."""

from decimal import Decimal

from app.reconcile import run_reconciliations
from app.repair import build_spread
from app.state import MappedLine, RawLine, mapping_key

PERIOD = "2026-01-01..2026-06-30"
OTHER = "2025-01-01..2025-06-30"


def _case(rows: list[tuple[str, str, str]], period: str = PERIOD, prefix: str = "r"):
    """rows are (standard line id, label, printed text)."""
    lines, mapping = [], {}
    for n, (line_id, label, text) in enumerate(rows, start=1):
        line = RawLine(
            raw_line_id=f"{prefix}{n}",
            doc_id="doc1",
            page=1,
            original_label=label,
            original_value_text=text,
            statement="IS",
            period=period,
            scale="thousands",
            currency="USD",
        )
        lines.append(line)
        mapping[mapping_key(period, line.raw_line_id)] = MappedLine(
            raw_line_id=line.raw_line_id,
            period=period,
            standard_line_id=line_id,
            confidence=0.9,
            rationale="x",
            origin="model",
        )
    return lines, mapping


POSITIVE_COSTS = [
    ("is_revenue", "Net sales", "1,000"),
    ("is_cost_of_sales", "Cost of goods sold", "400"),
    ("is_gross_profit", "Gross profit", "600"),
    ("is_operating_expenses", "Selling and administrative", "350"),
    ("is_operating_profit", "Operating income", "250"),
    ("is_profit_before_tax", "Income before taxes", "250"),
    ("is_income_tax", "Income taxes", "60"),
    ("is_net_income", "Net income", "190"),
]


def test_step24_costs_printed_positive_become_negative_when_the_subtotals_prove_it() -> None:
    lines, mapping = _case(POSITIVE_COSTS)
    notes: list[str] = []
    spread = build_spread(lines, mapping, notes=notes)
    cells = spread[PERIOD]
    assert cells["is_cost_of_sales"] == Decimal(-400)
    assert cells["is_operating_expenses"] == Decimal(-350)
    assert cells["is_income_tax"] == Decimal(-60)
    assert cells["is_revenue"] == Decimal(1000)  # only cost lines change
    assert cells["is_gross_profit"] == Decimal(600)
    assert sorted(notes) == [
        f"{PERIOD} is_cost_of_sales",
        f"{PERIOD} is_income_tax",
        f"{PERIOD} is_operating_expenses",
    ]


def test_step24_the_income_statement_checks_pass_after_the_sign_is_read() -> None:
    lines, mapping = _case(POSITIVE_COSTS)
    report = run_reconciliations(build_spread(lines, mapping), periods=[PERIOD])
    by_key = {r.key: r.status for r in report.results}
    assert by_key["subtotals_match_source"] == "pass"
    assert by_key["lines_complete"] == "pass"


def test_step24_a_period_without_gross_profit_is_judged_by_the_tax_identity() -> None:
    rows = [r for r in POSITIVE_COSTS if r[0] not in ("is_gross_profit", "is_operating_profit")]
    lines, mapping = _case(rows)
    cells = build_spread(lines, mapping)[PERIOD]
    # tax proves the convention; the other cost lines follow the period
    assert cells["is_income_tax"] == Decimal(-60)
    assert cells["is_cost_of_sales"] == Decimal(-400)
    assert cells["is_operating_expenses"] == Decimal(-350)


def test_step24_costs_already_printed_negative_are_left_alone() -> None:
    rows = [
        ("is_revenue", "Net sales", "1,000"),
        ("is_cost_of_sales", "Cost of goods sold", "(400)"),
        ("is_gross_profit", "Gross profit", "600"),
        ("is_operating_expenses", "Selling and administrative", "(350)"),
        ("is_operating_profit", "Operating income", "250"),
    ]
    lines, mapping = _case(rows)
    notes: list[str] = []
    cells = build_spread(lines, mapping, notes=notes)[PERIOD]
    assert cells["is_cost_of_sales"] == Decimal(-400)
    assert cells["is_operating_expenses"] == Decimal(-350)
    assert notes == []


def test_step24_no_subtotal_to_prove_it_means_no_change() -> None:
    rows = [
        ("is_revenue", "Net sales", "1,000"),
        ("is_cost_of_sales", "Cost of goods sold", "400"),
    ]
    lines, mapping = _case(rows)
    notes: list[str] = []
    cells = build_spread(lines, mapping, notes=notes)[PERIOD]
    assert cells["is_cost_of_sales"] == Decimal(400)
    assert notes == []


def test_step24_each_period_is_judged_alone() -> None:
    first, m1 = _case(POSITIVE_COSTS, PERIOD)
    negative = [
        ("is_revenue", "Net sales", "900"),
        ("is_cost_of_sales", "Cost of goods sold", "(300)"),
        ("is_gross_profit", "Gross profit", "600"),
    ]
    second, m2 = _case(negative, OTHER, prefix="q")
    spread = build_spread([*first, *second], {**m1, **m2})
    assert spread[PERIOD]["is_cost_of_sales"] == Decimal(-400)
    assert spread[OTHER]["is_cost_of_sales"] == Decimal(-300)


def test_step24_mixed_evidence_leaves_the_untested_line_as_printed() -> None:
    rows = [
        ("is_revenue", "Net sales", "1,000"),
        ("is_cost_of_sales", "Cost of goods sold", "400"),
        ("is_gross_profit", "Gross profit", "600"),  # fits the positive form
        ("is_operating_expenses", "Selling and administrative", "(350)"),
        ("is_operating_profit", "Operating income", "250"),  # fits the negative form
        ("is_income_tax", "Income taxes", "60"),  # nothing to test it against
    ]
    lines, mapping = _case(rows)
    cells = build_spread(lines, mapping)[PERIOD]
    assert cells["is_cost_of_sales"] == Decimal(-400)
    assert cells["is_operating_expenses"] == Decimal(-350)
    assert cells["is_income_tax"] == Decimal(60)


def test_step24_a_subtotal_that_fits_neither_form_changes_nothing() -> None:
    rows = [
        ("is_revenue", "Net sales", "1,000"),
        ("is_cost_of_sales", "Cost of goods sold", "(400)"),
        ("is_gross_profit", "Gross profit", "700"),  # includes other income, fits neither
    ]
    lines, mapping = _case(rows)
    notes: list[str] = []
    cells = build_spread(lines, mapping, notes=notes)[PERIOD]
    assert cells["is_cost_of_sales"] == Decimal(-400)
    assert notes == []


def test_step24_a_tax_benefit_in_a_positive_filing_comes_out_positive() -> None:
    rows = [
        ("is_profit_before_tax", "Income before taxes", "100"),
        ("is_income_tax", "Income tax benefit", "(5)"),
        ("is_net_income", "Net income", "105"),
    ]
    lines, mapping = _case(rows)
    assert build_spread(lines, mapping)[PERIOD]["is_income_tax"] == Decimal(5)


def test_step24_a_zero_cost_is_not_flipped_or_reported() -> None:
    rows = [
        ("is_revenue", "Net sales", "1,000"),
        ("is_cost_of_sales", "Cost of goods sold", "400"),
        ("is_gross_profit", "Gross profit", "600"),
        ("is_income_tax", "Income taxes", "0"),
    ]
    lines, mapping = _case(rows)
    notes: list[str] = []
    build_spread(lines, mapping, notes=notes)
    assert notes == [f"{PERIOD} is_cost_of_sales"]
