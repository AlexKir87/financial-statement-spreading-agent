"""A deterministic stand-in for the model, for tests and for recording the eval transcript.

It reads the same prompts the real nodes send and answers by rule: extraction reads the page's
header and rows, mapping uses the standard-line labels, diagnosis never finds a fix. It is not a
model and proves nothing about live accuracy; it exists so the graph runs end to end offline.
"""

import json
import re
from collections.abc import Callable, Sequence

from app.state import Usage
from app.tools.model_client import ImagePart, ModelReply

_HEADER = re.compile(
    r"(income statement|balance sheet|cash flow)[^(\n]*\(([A-Z]{3})\s+(\w+)\)\s+(.*)", re.I
)
_VALUE = r"\(?-?[\d,]+(?:\.\d+)?\)?"
_ROW = re.compile(rf"^(?P<label>.*?[A-Za-z)])\s+(?P<values>(?:{_VALUE}\s*)+)$")
_STATEMENT = {"income statement": "IS", "balance sheet": "BS", "cash flow": "CF"}
_SCALE = {"thousands": "thousands", "millions": "millions", "billions": "billions"}


def extraction_reply(user: str) -> str:
    page = user.split("<page>", 1)[1].split("</page>", 1)[0]
    rows = page.strip().splitlines()
    header = None
    start = 0
    for start, row in enumerate(rows):  # noqa: B007 - `start` is read after the loop
        header = _HEADER.search(row)
        if header:
            break
    if header is None:
        return json.dumps({"currency": "XXX", "scale": "not_stated", "lines": []})
    statement = _STATEMENT[header.group(1).lower()]
    periods = header.group(4).split()
    lines = []
    for body in rows[start + 1 :]:
        match = _ROW.match(body.strip())
        if not match:
            continue
        values = re.findall(_VALUE, match.group("values"))
        if len(values) != len(periods):
            continue
        for period, value in zip(periods, values, strict=True):
            lines.append(
                {
                    "statement": statement,
                    "label": match.group("label").strip(),
                    "period": period,
                    "value_text": value,
                }
            )
    return json.dumps(
        {"currency": header.group(2), "scale": _SCALE.get(header.group(3), "units"), "lines": lines}
    )


# Labels the starter rule table does not know, as a model would answer them.
_MODEL_MAP = {
    "proceeds from borrowings": "cf_debt_proceeds_repayments",
    "net cash from financing activities": "cf_net_cash_financing",
}


def mapping_reply(user: str) -> str:
    allowed = user.split("Allowed standard_line_id values for", 1)[1].split("\n", 1)[0]
    ids = [i.strip() for i in allowed.split(":", 1)[1].split(",")]
    statement_prefix = ids[0].split("_", 1)[0]
    other = f"{statement_prefix}_other_{'items' if statement_prefix == 'is' else 'operating'}"
    fallback = other if other in ids else ids[0]
    out = []
    for item in re.findall(r"^(k\d+) \| \w+ \| [\w-]+ \| (.*)$", user, re.M):
        key, label = item
        line_id = _MODEL_MAP.get(label.strip().lower(), fallback)
        out.append(
            {"key": key, "standard_line_id": line_id, "confidence": 0.9, "rationale": "by label"}
        )
    return json.dumps({"mappings": out})


def diagnosis_reply(user: str) -> str:
    return json.dumps(
        {"cause": "The printed figures disagree and no line explains it", "action": "none"}
    )


def respond(system: str, user: str) -> str:
    if "<page>" in user:
        return extraction_reply(user)
    if "<labels>" in user and "Allowed standard_line_id values" in user:
        return mapping_reply(user)
    return diagnosis_reply(user)


class ScriptedModel:
    """A `ModelClient` answering by rule. `calls` counts requests; `fail_on` can inject a fault."""

    def __init__(self, on_call: Callable[[str, str], None] | None = None) -> None:
        self.calls = 0
        self._on_call = on_call

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int | None = None,
        images: Sequence[ImagePart] = (),
    ) -> ModelReply:
        self.calls += 1
        if self._on_call is not None:
            self._on_call(system, user)
        text = respond(system, user)
        return ModelReply(
            text=text,
            usage=Usage(input_tokens=len(system + user) // 4, output_tokens=len(text) // 4),
        )
