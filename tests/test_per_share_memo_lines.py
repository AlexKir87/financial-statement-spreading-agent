"""Plan step 25: per-share figures and share counts have memo lines the checks never add up.
Labels and numbers are invented; no client data."""

from decimal import Decimal

import pytest

from app.mapping import load_mapping_table
from app.reconcile import run_reconciliations
from app.sections import row_sections

MEMO = (
    "is_eps_basic",
    "is_eps_diluted",
    "is_shares_basic",
    "is_shares_diluted",
    "is_dividends_per_share",
)


def test_cr_step25_the_memo_lines_exist_and_are_memo() -> None:
    kinds = {ln.line_id: (ln.statement, ln.kind) for ln in load_mapping_table().lines}
    for line_id in MEMO:
        assert kinds[line_id] == ("IS", "memo")


@pytest.mark.parametrize(
    ("label", "line_id"),
    [
        ("Basic loss per share", "is_eps_basic"),
        ("Diluted loss per share", "is_eps_diluted"),
        ("Earnings per share - basic", "is_eps_basic"),
        ("Earnings per common share-diluted", "is_eps_diluted"),
        ("Basic earnings per share", "is_eps_basic"),
        ("Weighted-average shares outstanding-basic", "is_shares_basic"),
        ("Diluted weighted-average number of shares outstanding", "is_shares_diluted"),
        ("Weighted average shares outstanding - diluted", "is_shares_diluted"),
        ("Dividends declared per common share", "is_dividends_per_share"),
    ],
)
def test_cr_step25_per_share_labels_have_rules(label: str, line_id: str) -> None:
    assert load_mapping_table().lookup("IS", label) == line_id


def test_cr_step25_a_bare_basic_follows_its_heading() -> None:
    rows = [
        "Net income 100 90",
        "Earnings per common share",
        "Basic $ 7.98 $ 1.46",
        "Diluted $ 7.94 $ 1.46",
        "Weighted average shares outstanding:",
        "Basic 1,139.6 1,136.4",
        "Diluted 1,143.8 1,139.8",
    ]
    assert row_sections("IS", rows) == [
        None,
        "per_share",
        "per_share",
        "per_share",
        "share_count",
        "share_count",
        "share_count",
    ]
    table = load_mapping_table()
    assert table.lookup("IS", "Basic", "per_share") == "is_eps_basic"
    assert table.lookup("IS", "Diluted", "per_share") == "is_eps_diluted"
    assert table.lookup("IS", "Basic", "share_count") == "is_shares_basic"
    assert table.lookup("IS", "Diluted", "share_count") == "is_shares_diluted"


def test_cr_step25_a_bare_basic_without_a_heading_is_left_to_the_model() -> None:
    assert load_mapping_table().lookup("IS", "Basic", None) is None


def test_cr_step25_memo_lines_are_not_added_into_net_income() -> None:
    period = "2026-01-01..2026-06-30"
    base = {
        "is_revenue": Decimal(1000),
        "is_cost_of_sales": Decimal(-600),
        "is_operating_expenses": Decimal(-200),
        "is_income_tax": Decimal(-50),
        "is_net_income": Decimal(150),
    }
    noisy = {
        **base,
        "is_eps_basic": Decimal("3.33"),
        "is_eps_diluted": Decimal("3.32"),
        "is_shares_basic": Decimal("709.1"),
        "is_shares_diluted": Decimal("711.1"),
        "is_dividends_per_share": Decimal("1.86"),
    }
    plain = run_reconciliations({period: base}, periods=[period])
    with_memo = run_reconciliations({period: noisy}, periods=[period])
    assert [(r.key, r.status, r.difference) for r in plain.results] == [
        (r.key, r.status, r.difference) for r in with_memo.results
    ]
