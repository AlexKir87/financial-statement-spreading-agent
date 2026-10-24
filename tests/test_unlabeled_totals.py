"""Plan step 28: a balance sheet that prints its subtotals with no label is still read.

The model lists labelled rows only. Code names an unlabeled number row from the sums of the rows
above it, and only when the sum holds in every period column; anything else stays unread and is
flagged. Synthetic figures, no network.
"""

import json
from decimal import Decimal

from app.extract import extract_statements
from app.mapping import load_mapping_table, map_lines
from app.repair import build_spread
from app.state import DigitizedPage, Usage
from app.tools import ModelReply, StructuredCaller, TokenBudget

PAGE = """CONSOLIDATED BALANCE SHEETS
(Unaudited; Amounts in thousands)
June 30,
2026 December 31,
2025
ASSETS
Current assets
Cash and cash equivalents $ 100 $ 150
Receivables 50 40
Inventories 30 20
180 210
Property and equipment, net 400 380
Goodwill 20 20
$ 600 $ 610
LIABILITIES AND STOCKHOLDERS' EQUITY
Current liabilities
Accounts payable $ 70 $ 60
Accrued liabilities 30 40
100 100
Long-term debt 200 250
Other non-current liabilities 10 10
310 360
Commitments and contingencies
Stockholders' equity
Common stock 5 5
Retained earnings 285 245
290 250
$ 600 $ 610
"""

PERIODS = ("June 30, 2026", "December 31, 2025")


def _rows() -> list[tuple[str, str, str]]:
    out = []
    for row in PAGE.splitlines():
        parts = row.replace("$ ", "").rsplit(" ", 2)
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit() and parts[0].strip():
            if parts[0].replace(" ", "").isalpha() or any(c.isalpha() for c in parts[0]):
                out.append((parts[0], parts[1], parts[2]))
    return out


class LabelledOnly:
    """Answers with the labelled rows only, as the live model does for unlabeled totals."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, *, model: str, system: str, user: str, max_tokens: int | None = None):
        self.calls += 1
        lines = [
            {"statement": "BS", "label": label, "period": period, "value_text": value}
            for label, a, b in _rows()
            for period, value in zip(PERIODS, (a, b), strict=True)
        ]
        text = json.dumps({"currency": "USD", "scale": "thousands", "lines": lines})
        return ModelReply(text=text, usage=Usage(input_tokens=10, output_tokens=10))


def _extract(page_text: str = PAGE):
    page = DigitizedPage(
        doc_id="doc-x",
        page=1,
        text=page_text,
        digitizer_name="pdf-text-layer",
        digitizer_version="1",
    )
    caller = StructuredCaller(
        client=LabelledOnly(),
        model="m",
        budget=TokenBudget(cap=10_000, used=0),
        prompt_version="v1",
    )
    return extract_statements([page], caller)


def _by_label(result):
    return {(ln.original_label, ln.period): ln for ln in result.raw_lines if ln.label_inferred}


def test_unlabeled_rows_are_named_from_the_sums_above_them():
    inferred = _by_label(_extract())
    assert {label for label, _ in inferred} == {
        "Total current assets",
        "Total assets",
        "Total current liabilities",
        "Total liabilities",
        "Total equity",
        "Total liabilities and equity",
    }
    values = {k: ln.original_value_text for k, ln in inferred.items()}
    assert values[("Total assets", "2026-06-30")] == "600"
    assert values[("Total assets", "2025-12-31")] == "610"
    assert values[("Total liabilities", "2026-06-30")] == "310"
    assert values[("Total equity", "2025-12-31")] == "250"


def test_inferred_rows_are_flagged_and_traceable():
    result = _extract()
    inferred = [ln for ln in result.raw_lines if ln.label_inferred]
    assert len(inferred) == 12  # six rows, two period columns
    assert len({ln.raw_line_id for ln in result.raw_lines}) == len(result.raw_lines)
    assert all(ln.page == 1 and ln.doc_id == "doc-x" for ln in inferred)
    codes = [i.code for i in result.open_issues]
    assert codes.count("unlabeled_total_inferred") == 6


def test_a_row_that_does_not_add_up_is_not_named():
    broken = PAGE.replace("180 210", "181 210")  # first period no longer sums
    result = _extract(broken)
    names = {ln.original_label for ln in result.raw_lines if ln.label_inferred}
    assert "Total current assets" not in names
    assert "unlabeled_row_not_resolved" in [i.code for i in result.open_issues]
    assert not any(ln.original_value_text == "181" for ln in result.raw_lines)


def test_inferred_totals_map_by_rule_without_a_model_call():
    inferred = [ln for ln in _extract().raw_lines if ln.label_inferred]
    client = _NeverCalled()
    caller = StructuredCaller(
        client=client, model="m", budget=TokenBudget(cap=10_000, used=0), prompt_version="v1"
    )
    mapped = map_lines(inferred, load_mapping_table(), caller)
    assert client.calls == 0
    spread = build_spread(inferred, mapped.mapping)
    assert spread["2026-06-30"]["bs_total_assets"] == Decimal(600)
    assert spread["2025-12-31"]["bs_total_liabilities_and_equity"] == Decimal(610)
    assert spread["2026-06-30"]["bs_total_equity"] == Decimal(290)


class _NeverCalled:
    calls = 0

    def complete(self, **_: object) -> ModelReply:
        self.calls += 1
        raise AssertionError("a rule hit must not call the model")


def test_a_bare_equity_row_followed_by_more_equity_rows_is_not_named():
    page = PAGE.replace("290 250\n", "290 250\nNon-controlling interests 5 5\n")
    result = _extract(page)
    names = {ln.original_label for ln in result.raw_lines if ln.label_inferred}
    assert "Total equity" not in names
    assert "unlabeled_row_not_resolved" in [i.code for i in result.open_issues]
