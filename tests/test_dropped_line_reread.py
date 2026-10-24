"""Plan step 28: a line dropped because its label is not on the row is read once more.

The live model sometimes writes a label the page does not print on that row ("Sales Products"
for the row `Products`). Code asks again for exactly those lines, once per page, and keeps a
reread line only if it is one of the dropped (period, value) pairs and passes the same
same-row check. Synthetic figures, no network.
"""

import json

from app.extract import extract_statements
from app.state import DigitizedPage, Usage
from app.tools import ModelReply, StructuredCaller, TokenBudget

PAGE = """Consolidated Statements of Earnings
(unaudited; in millions)
Quarters Ended June 28,
2026 June 29,
2025
Sales
Products $ 16 $ 15
Services 3 3
Total sales 19 18
"""
PERIODS = ("Quarters Ended June 28, 2026", "Quarters Ended June 29, 2025")
ROWS = (("Products", "16", "15"), ("Services", "3", "3"), ("Total sales", "19", "18"))


def _reply(first_label: str, extra: list[dict] | None = None) -> str:
    lines = [
        {
            "statement": "IS",
            "label": first_label if label == "Products" else label,
            "period": period,
            "value_text": value,
        }
        for label, a, b in ROWS
        for period, value in zip(PERIODS, (a, b), strict=True)
    ]
    return json.dumps({"currency": "USD", "scale": "millions", "lines": lines + (extra or [])})


class Scripted:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def complete(self, *, model: str, system: str, user: str, max_tokens: int | None = None):
        self.prompts.append(user)
        return ModelReply(text=self.replies.pop(0), usage=Usage(input_tokens=10, output_tokens=10))


def _run(client: Scripted):
    page = DigitizedPage(
        doc_id="doc-x", page=1, text=PAGE, digitizer_name="pdf-text-layer", digitizer_version="1"
    )
    caller = StructuredCaller(
        client=client, model="m", budget=TokenBudget(cap=10_000, used=0), prompt_version="v1"
    )
    return extract_statements([page], caller)


def _reread(label: str) -> str:
    return json.dumps(
        {
            "currency": "USD",
            "scale": "millions",
            "lines": [
                {"statement": "IS", "label": label, "period": p, "value_text": v}
                for p, v in zip(PERIODS, ("16", "15"), strict=True)
            ],
        }
    )


def test_cr_step28_dropped_lines_are_read_again_and_kept():
    client = Scripted(_reply("Sales Products"), _reread("Products"))
    result = _run(client)
    assert len(client.prompts) == 2
    products = [ln for ln in result.raw_lines if ln.original_label == "Products"]
    assert sorted(ln.original_value_text for ln in products) == ["15", "16"]
    assert len(result.raw_lines) == 6
    assert "extracted_line_not_in_source" not in [i.code for i in result.open_issues]
    assert len({ln.raw_line_id for ln in result.raw_lines}) == 6


def test_cr_step28_the_second_read_is_only_asked_for_the_dropped_lines():
    client = Scripted(_reply("Sales Products"), _reread("Products"))
    _run(client)
    again = client.prompts[1]
    assert "Sales Products" in again and "16" in again and "15" in again
    assert "Services" not in again.split("</page>", 1)[1]  # kept lines are not asked for again


def test_cr_step28_a_line_still_wrong_after_the_second_read_stays_dropped_and_flagged():
    client = Scripted(_reply("Sales Products"), _reread("Sales Products"))
    result = _run(client)
    assert len(client.prompts) == 2  # one reread per page, never a loop
    assert not [ln for ln in result.raw_lines if ln.original_label.startswith("Sales")]
    assert [i.code for i in result.open_issues].count("extracted_line_not_in_source") == 2


def test_cr_step28_a_reread_cannot_add_lines_that_were_not_dropped():
    extra = {"statement": "IS", "label": "Services", "period": PERIODS[0], "value_text": "3"}
    reread = json.loads(_reread("Products"))
    reread["lines"].append(extra)
    client = Scripted(_reply("Sales Products"), json.dumps(reread))
    result = _run(client)
    assert len(result.raw_lines) == 6  # the repeated Services row is not added twice


def test_cr_step28_no_second_read_when_nothing_was_dropped():
    client = Scripted(_reply("Products"))
    result = _run(client)
    assert len(client.prompts) == 1
    assert len(result.raw_lines) == 6


def test_cr_step28_an_invalid_second_reply_keeps_the_first_result():
    client = Scripted(_reply("Sales Products"), "not json", "not json")
    result = _run(client)
    assert len(result.raw_lines) == 4
    assert [i.code for i in result.open_issues].count("extracted_line_not_in_source") == 2


def test_cr_step28_a_reread_cannot_duplicate_a_line_already_kept():
    page = PAGE.replace("Total sales 19 18", "Total sales 16 15")
    reply = json.loads(_reply("Sales Products"))
    reply["lines"] = [ln for ln in reply["lines"] if ln["label"] != "Total sales"]
    reply["lines"] += [
        {"statement": "IS", "label": "Total sales", "period": p, "value_text": v}
        for p, v in zip(PERIODS, ("16", "15"), strict=True)
    ]
    again = json.loads(_reread("Total sales"))
    client = Scripted(json.dumps(reply), json.dumps(again))
    pg = DigitizedPage(
        doc_id="doc-x", page=1, text=page, digitizer_name="pdf-text-layer", digitizer_version="1"
    )
    caller = StructuredCaller(
        client=client, model="m", budget=TokenBudget(cap=10_000, used=0), prompt_version="v1"
    )
    result = extract_statements([pg], caller)
    kept = [(ln.original_label, ln.period, ln.original_value_text) for ln in result.raw_lines]
    assert len(kept) == len(set(kept))
