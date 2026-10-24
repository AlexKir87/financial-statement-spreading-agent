"""Plan step 20: a long page is read in parts, so a reply cut at the ceiling does not lose rows.

A fake model lists every row of the page it is shown and cuts its reply when the page is too long,
as the real ceiling does. Synthetic figures, no network.
"""

import json
import re

from app.extract import extract_statements
from app.extract.statements import MAX_ROWS_PER_CALL, _part_prompt
from app.state import DigitizedPage, Usage
from app.tools import ModelReply, StructuredCaller, TokenBudget

HEADING = [
    "CONSOLIDATED STATEMENTS OF CASH FLOWS",
    "(in millions of USD)",
    "Six Months Ended June 30, 2026",
    "",
]


def long_page(rows: int) -> str:
    body = [f"Cash item {n:03d} {n * 10 + 1:,}" for n in range(rows)]
    return "\n".join([*HEADING, *body])


class CuttingClient:
    """Lists the data rows it is shown; a reply for more than `limit` rows is cut mid-JSON."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.prompts: list[str] = []

    def complete(
        self, *, model: str, system: str, user: str, max_tokens: int | None = None
    ) -> ModelReply:
        self.prompts.append(user)
        page = user.split("<page>\n", 1)[1].split("\n</page>", 1)[0]
        rows = page.splitlines()
        if "[continued]" in rows:
            rows = rows[rows.index("[continued]") + 1 :]
        found = [m for r in rows if (m := re.match(r"(Cash item \d+) ([\d,]+)$", r))]
        lines = [
            {
                "statement": "CF",
                "label": m[1],
                "period": "Six Months Ended June 30, 2026",
                "value_text": m[2],
            }
            for m in found
        ]
        text = json.dumps({"currency": "USD", "scale": "millions", "lines": lines})
        if len(found) > self.limit:
            text = text[: len(text) // 2]
        return ModelReply(text=text, usage=Usage(input_tokens=10, output_tokens=10))


def run(rows: int, limit: int):
    client = CuttingClient(limit)
    caller = StructuredCaller(
        client=client, model="test/model-1", budget=TokenBudget(cap=10**6), prompt_version="v1"
    )
    page = DigitizedPage(
        doc_id="doc1",
        page=1,
        text=long_page(rows),
        digitizer_name="pdf-text-layer",
        digitizer_version="1",
    )
    return extract_statements([page], caller), client


def labels(result) -> list[str]:
    return [ln.original_label for ln in result.raw_lines]


def test_a_short_page_is_one_call_with_the_page_unchanged() -> None:
    result, client = run(10, limit=100)
    assert len(client.prompts) == 1
    assert "[continued]" not in client.prompts[0]
    assert len(result.raw_lines) == 10


def test_a_long_page_is_read_in_parts_and_no_row_is_lost() -> None:
    rows = 3 * MAX_ROWS_PER_CALL
    result, client = run(rows, limit=1000)
    assert len(client.prompts) > 1
    assert labels(result) == [f"Cash item {n:03d}" for n in range(rows)]
    assert not [i for i in result.open_issues if i.code == "extraction_invalid_output"]


def test_a_later_part_repeats_the_heading_rows_as_context() -> None:
    _, client = run(2 * MAX_ROWS_PER_CALL, limit=1000)
    later = client.prompts[1]
    assert "[continued]" in later
    for row in HEADING[:3]:
        assert row in later


def test_a_cut_reply_is_read_again_in_halves() -> None:
    # 40 rows (heading included) fit one call by row count, but this model cuts any reply over 25
    rows = MAX_ROWS_PER_CALL - len(HEADING)
    result, client = run(rows, limit=25)
    assert labels(result) == [f"Cash item {n:03d}" for n in range(rows)]
    assert len(client.prompts) == 4  # the cut call is tried twice, then the two halves


def test_a_part_that_cannot_be_read_is_flagged_not_lost_silently() -> None:
    result, _ = run(MAX_ROWS_PER_CALL - len(HEADING), limit=0)
    assert result.raw_lines == ()
    assert [i.code for i in result.open_issues] == ["extraction_invalid_output"]


def test_line_ids_are_unique_across_parts() -> None:
    result, _ = run(2 * MAX_ROWS_PER_CALL, limit=1000)
    ids = [ln.raw_line_id for ln in result.raw_lines]
    assert len(set(ids)) == len(ids)


def test_a_page_row_cannot_pose_as_the_marker() -> None:
    page = long_page(2 * MAX_ROWS_PER_CALL).replace("Cash item 050 501", "[continued]")
    client = CuttingClient(1000)
    caller = StructuredCaller(
        client=client, model="test/model-1", budget=TokenBudget(cap=10**6), prompt_version="v1"
    )
    doc = DigitizedPage(
        doc_id="doc1", page=1, text=page, digitizer_name="pdf-text-layer", digitizer_version="1"
    )
    extract_statements([doc], caller)
    for prompt in client.prompts[1:]:
        assert prompt.splitlines().count("[continued]") == 1


def test_a_part_does_not_repeat_its_own_rows_as_context() -> None:
    rows = [f"row {n}" for n in range(30)]
    prompt = _part_prompt("\n".join(rows), rows[11:], 11, rows)
    assert prompt.splitlines().count("row 11") == 1


def test_parts_that_state_different_scales_are_flagged() -> None:
    class Disagreeing(CuttingClient):
        def complete(self, **kwargs):  # type: ignore[no-untyped-def]
            reply = super().complete(**kwargs)
            if len(self.prompts) > 1:
                reply = ModelReply(
                    text=reply.text.replace("millions", "thousands"), usage=reply.usage
                )
            return reply

    client = Disagreeing(1000)
    caller = StructuredCaller(
        client=client, model="test/model-1", budget=TokenBudget(cap=10**6), prompt_version="v1"
    )
    doc = DigitizedPage(
        doc_id="doc1",
        page=1,
        text=long_page(2 * MAX_ROWS_PER_CALL),
        digitizer_name="pdf-text-layer",
        digitizer_version="1",
    )
    result = extract_statements([doc], caller)
    assert "scale_differs_between_parts" in [i.code for i in result.open_issues]


def test_the_token_cap_in_a_later_part_stops_cleanly() -> None:
    client = CuttingClient(1000)
    caller = StructuredCaller(
        client=client, model="test/model-1", budget=TokenBudget(cap=40), prompt_version="v1"
    )
    doc = DigitizedPage(
        doc_id="doc1",
        page=1,
        text=long_page(2 * MAX_ROWS_PER_CALL),
        digitizer_name="pdf-text-layer",
        digitizer_version="1",
    )
    result = extract_statements([doc], caller)
    assert "token_cap_reached" in [i.code for i in result.open_issues]
