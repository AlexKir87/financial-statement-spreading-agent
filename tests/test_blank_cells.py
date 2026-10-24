"""Plan step 30: an empty cell in a statement row is kept visible, so a lone number is read in
its own period column. Company A's cash flow prints `Proceeds from dispositions` with the first
column empty and 35 in the second; read as the first column it broke the investing total by 35 in
both periods. Synthetic figures, no network."""

import json
from pathlib import Path

from app.extract import extract_statements
from app.ingest import ingest_documents
from app.prompts import load_prompt
from app.state import DigitizedPage, Usage
from app.tools import ModelReply, StructuredCaller, TokenBudget

NIL = (
    '<td><ix:nonFraction unitRef="usd" contextRef="c1" xsi:nil="true" name="x:Y">'
    "</ix:nonFraction>&#160;</td><td/>"
)
HTML = (
    '<html><body><ix:nonNumeric xmlns:ix="http://www.xbrl.org/2013/inlineXBRL">x</ix:nonNumeric>'
    "<p>CONSOLIDATED STATEMENTS OF CASH FLOWS</p><p>(Unaudited; in millions)</p>"
    "<table><tr><td>Six months ended June 30</td><td>2026</td><td>2025</td></tr>"
    "<tr><td>Capital expenditure</td><td>(20)</td><td>(11)</td></tr>"
    f"<tr><td>Proceeds from dispositions</td>{NIL}<td>35</td></tr>"
    "<tr><td>Net cash from investing</td><td>(20)</td><td>24</td></tr></table></body></html>"
)
TEXT = """CONSOLIDATED STATEMENTS OF CASH FLOWS
(Unaudited; in millions)
Six months ended June 30
2026 2025
Capital expenditure (20) (11)
Proceeds from dispositions [blank] 35
Net cash from investing (20) 24
"""
PERIODS = ("Six months ended June 30, 2026", "Six months ended June 30, 2025")


def test_an_empty_value_cell_stays_visible_in_the_page_text(tmp_path: Path) -> None:
    (tmp_path / "f.htm").write_text(HTML, encoding="utf-8")
    page = ingest_documents(["f.htm"], tmp_path).digitized[0]
    assert "Proceeds from dispositions [blank] 35" in page.text.splitlines()


def test_the_extraction_prompt_explains_the_blank_marker() -> None:
    assert "[blank]" in load_prompt("extract_statements", "v1")


class _Client:
    def __init__(self, dispositions_period: str) -> None:
        self.dispositions_period = dispositions_period

    def complete(self, *, model: str, system: str, user: str, max_tokens: int | None = None):
        rows = [
            ("Capital expenditure", PERIODS[0], "(20)"),
            ("Capital expenditure", PERIODS[1], "(11)"),
            ("Proceeds from dispositions", self.dispositions_period, "35"),
            ("Net cash from investing", PERIODS[0], "(20)"),
            ("Net cash from investing", PERIODS[1], "24"),
        ]
        lines = [{"statement": "CF", "label": a, "period": b, "value_text": c} for a, b, c in rows]
        text = json.dumps({"currency": "USD", "scale": "millions", "lines": lines})
        return ModelReply(text=text, usage=Usage(input_tokens=1, output_tokens=1))


def _run(period: str):
    page = DigitizedPage(
        doc_id="doc-x", page=1, text=TEXT, digitizer_name="pdf-text-layer", digitizer_version="1"
    )
    caller = StructuredCaller(
        client=_Client(period), model="m", budget=TokenBudget(cap=10_000), prompt_version="v1"
    )
    return extract_statements([page], caller)


def test_a_number_read_into_the_empty_column_is_dropped_and_flagged() -> None:
    result = _run(PERIODS[0])
    assert not [ln for ln in result.raw_lines if ln.original_label.startswith("Proceeds")]
    assert "value_in_blank_column" in [i.code for i in result.open_issues]


def test_a_number_read_into_its_own_column_is_kept() -> None:
    result = _run(PERIODS[1])
    kept = [ln for ln in result.raw_lines if ln.original_label.startswith("Proceeds")]
    assert [ln.original_value_text for ln in kept] == ["35"]
    assert "value_in_blank_column" not in [i.code for i in result.open_issues]


def test_a_repeated_label_with_a_blank_row_elsewhere_does_not_drop_a_correct_line() -> None:
    text = TEXT.replace(
        "Proceeds from dispositions [blank] 35",
        "Other 35 20\nOther [blank] 35",
    )
    lines = [
        {"statement": "CF", "label": "Other", "period": PERIODS[0], "value_text": "35"},
        {"statement": "CF", "label": "Other", "period": PERIODS[1], "value_text": "20"},
        {"statement": "CF", "label": "Other", "period": PERIODS[1], "value_text": "35"},
        {
            "statement": "CF",
            "label": "Capital expenditure",
            "period": PERIODS[0],
            "value_text": "(20)",
        },
        {
            "statement": "CF",
            "label": "Capital expenditure",
            "period": PERIODS[1],
            "value_text": "(11)",
        },
    ]

    class Client:
        def complete(self, *, model: str, system: str, user: str, max_tokens: int | None = None):
            body = json.dumps({"currency": "USD", "scale": "millions", "lines": lines})
            return ModelReply(text=body, usage=Usage(input_tokens=1, output_tokens=1))

    page = DigitizedPage(
        doc_id="doc-x", page=1, text=text, digitizer_name="pdf-text-layer", digitizer_version="1"
    )
    caller = StructuredCaller(
        client=Client(), model="m", budget=TokenBudget(cap=10_000), prompt_version="v1"
    )
    result = extract_statements([page], caller)
    mine = [
        (ln.period_label, ln.original_value_text)
        for ln in result.raw_lines
        if ln.original_label == "Other"
    ]
    assert (PERIODS[0], "35") in mine  # the first row's 35 is kept
    assert (PERIODS[1], "35") in mine  # the blank row's 35 is in its own column
