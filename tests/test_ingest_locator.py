"""Statement locator for HTML filings (plan step 13): a full 10-Q becomes one page per statement."""

from pathlib import Path

from app.ingest import ingest_documents

TOC = (
    "<p>Index</p><p>Consolidated Statements of Operations</p><p>3</p>"
    "<p>Consolidated Balance Sheets</p><p>4</p><p>Consolidated Statements of Cash Flows</p><p>5</p>"
)
INCOME = (
    "<p>CONSOLIDATED STATEMENTS OF OPERATIONS</p><p>(Unaudited; in millions)</p>"
    "<table><tr><td>Revenue</td><td>1,000</td><td>900</td></tr>"
    "<tr><td>Net income</td><td>100</td><td>90</td></tr></table>"
)
COMPREHENSIVE = (
    "<p>CONSOLIDATED STATEMENTS OF COMPREHENSIVE INCOME</p><p>(Unaudited; in millions)</p>"
    "<table><tr><td>Other comprehensive loss</td><td>7</td></tr></table>"
)
BALANCE = (
    "<p>CONSOLIDATED BALANCE SHEETS</p><p>(Unaudited; in millions)</p>"
    "<table><tr><td>Cash</td><td>300</td><td>250</td></tr>"
    "<tr><td>Total assets</td><td>1,300</td><td>1,200</td></tr></table>"
)
CASH = (
    "<p>CONSOLIDATED STATEMENTS OF CASH FLOWS</p><p>(Unaudited; in millions)</p>"
    "<table><tr><td>Net cash from operations</td><td>120</td><td>110</td></tr></table>"
)
EQUITY = (
    "<p>CONSOLIDATED STATEMENTS OF EQUITY</p><p>(Unaudited; in millions)</p>"
    "<table><tr><td>Dividends declared</td><td>40</td></tr>"
    "<tr><td>Net income</td><td>100</td></tr></table>"
)
NOTES = "<p>Notes to the financial statements</p><p>Note 1 Basis of presentation text.</p>"


def _load(tmp_path: Path, body: str) -> list[tuple[int, str]]:
    (tmp_path / "f.htm").write_text(
        f'<html><body><ix:nonNumeric xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">x</ix:nonNumeric>'
        f"{body}</body></html>",
        encoding="utf-8",
    )
    result = ingest_documents(["f.htm"], tmp_path)
    return [(p.page, p.text or "") for p in result.digitized]


def test_full_filing_yields_one_page_per_primary_statement(tmp_path: Path) -> None:
    pages = _load(tmp_path, TOC + INCOME + COMPREHENSIVE + BALANCE + CASH + EQUITY + NOTES)
    assert [n for n, _ in pages] == [1, 2, 3, 4]
    assert "Revenue" in pages[0][1] and "Total assets" not in pages[0][1]
    assert "Total assets" in pages[1][1]
    assert "Net cash from operations" in pages[2][1]
    assert "Dividends declared" in pages[3][1]  # plan step 26: the equity statement is a page


def test_equity_statement_is_found_under_its_other_names(tmp_path: Path) -> None:
    for heading in (
        "CONSOLIDATED STATEMENTS OF CHANGES IN EQUITY",
        "CONDENSED CONSOLIDATED STATEMENTS OF SHAREHOLDERS\u2019 EQUITY",
        "Consolidated Statements of Stockholders' Equity",
    ):
        pages = _load(
            tmp_path,
            INCOME + BALANCE + EQUITY.replace("CONSOLIDATED STATEMENTS OF EQUITY", heading),
        )
        assert len(pages) == 3 and "Dividends declared" in pages[2][1], heading


def test_contents_list_notes_and_comprehensive_are_not_pages(tmp_path: Path) -> None:
    joined = " ".join(
        t
        for _, t in _load(tmp_path, TOC + INCOME + COMPREHENSIVE + BALANCE + CASH + EQUITY + NOTES)
    )
    assert "Other comprehensive loss" not in joined
    assert "Basis of presentation" not in joined
    assert "Index" not in joined


def test_combined_operations_and_comprehensive_income_is_the_income_statement(
    tmp_path: Path,
) -> None:
    combined = INCOME.replace("OPERATIONS", "OPERATIONS AND COMPREHENSIVE INCOME")
    pages = _load(tmp_path, TOC + combined + BALANCE + CASH + NOTES)
    assert len(pages) == 3 and "Revenue" in pages[0][1]


def test_missing_statement_is_simply_absent(tmp_path: Path) -> None:
    pages = _load(tmp_path, TOC + INCOME + BALANCE + NOTES)
    assert len(pages) == 2


def test_html_without_statement_headings_stays_one_page(tmp_path: Path) -> None:
    pages = _load(tmp_path, "<p>Some narrative</p><p>Revenue 1,000</p>")
    assert len(pages) == 1 and "Some narrative" in pages[0][1]


def test_each_table_row_is_its_own_line(tmp_path: Path) -> None:
    pages = _load(tmp_path, TOC + INCOME + BALANCE + CASH + NOTES)
    rows = pages[1][1].splitlines()
    assert any(r.startswith("Cash") and "300" in r and "Total assets" not in r for r in rows)
    assert any(r.startswith("Total assets") and "1,300" in r for r in rows)


def test_notes_line_ends_a_statement_window(tmp_path: Path) -> None:
    body = TOC + INCOME.replace(
        "</table>", "</table><p>Notes to the statements</p><p>Stray 5 6 7</p>"
    )
    joined = " ".join(t for _, t in _load(tmp_path, body))
    assert "Stray" not in joined


def test_heading_lookalike_in_a_note_loses_to_the_statement(tmp_path: Path) -> None:
    lookalike = "<p>Consolidated Balance Sheets</p><p>see note 4</p><p>12</p>"
    pages = _load(tmp_path, TOC + BALANCE + NOTES + lookalike)
    assert len(pages) == 1 and "Total assets" in pages[0][1]


def test_window_with_too_few_numbers_is_not_a_page(tmp_path: Path) -> None:
    thin = "<p>CONSOLIDATED BALANCE SHEETS</p><p>(Unaudited)</p><p>Cash 300</p>"
    pages = _load(tmp_path, TOC + INCOME + thin)
    assert len(pages) == 1 and "Revenue" in pages[0][1]


def test_heading_starting_with_unaudited_is_found(tmp_path: Path) -> None:
    body = (
        INCOME.replace(
            "CONSOLIDATED STATEMENTS OF OPERATIONS", "UNAUDITED CONSOLIDATED STATEMENTS OF INCOME"
        )
        + BALANCE.replace(
            "CONSOLIDATED BALANCE SHEETS", "Unaudited Condensed Consolidated Balance Sheets"
        )
        + CASH
    )
    assert len(_load(tmp_path, TOC + body)) == 3


def test_balance_sheet_location_is_not_a_statement(tmp_path: Path) -> None:
    note = "<p>BALANCE SHEET LOCATION</p><p>Assets 1 2 3</p><p>Liabilities 4 5 6</p>"
    assert len(_load(tmp_path, TOC + INCOME + note)) == 1
