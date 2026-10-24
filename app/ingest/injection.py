"""Instruction-like text in a document is data. It is reported, never passed on or followed.

Matching is a tripwire for the open-issues list, not a defence: the defence is structural (model
nodes get typed outputs and no write-capable tools; the checks and the writer are code). The issue
names the pattern and the page, never the text, so the document's words do not reach logs or
workbooks.
"""

import re
from typing import Final

_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = tuple(
    (name, re.compile(rx, re.IGNORECASE))
    for name, rx in (
        (
            "addresses_automated_system",
            r"\b(note|message|instructions?)\s+(to|for)\s+(the\s+)?(automated|ai|assistant|model|llm|agent)",
        ),
        (
            "override_instructions",
            r"\b(ignore|disregard|forget|override)\b.{0,40}\b(instructions?|rules?|prompt|checks?)",
        ),
        (
            "skip_checks",
            r"\b(skip|bypass|disable|omit)\b.{0,30}\b(reconciliations?|checks?|verification|validation)",
        ),
        (
            "force_balance",
            r"\bset\b.{0,40}\bequal\s+to\b|\b(plug|balancing\s+figure)\b|\bput\s+the\s+difference\b",
        ),
        ("mark_checks_pass", r"\b(mark|report|show|set)\b.{0,30}\b(all|every)\b.{0,15}\bchecks?\b"),
        ("role_change", r"\b(you\s+are\s+now|act\s+as|new\s+instructions?|system\s+prompt)\b"),
        ("conceal", r"\b(do\s+not|don'?t)\s+(tell|mention|report|flag|disclose)\b"),
        (
            "exfiltrate",
            r"\b(reveal|print|send|email|post)\b.{0,40}\b(prompt|api\s*key|credentials?|password|secret)",
        ),
    )
)


def find_instruction_like(text: str) -> list[str]:
    """Names of the patterns that match, in a fixed order, each at most once."""
    return [name for name, rx in _PATTERNS if rx.search(text)]
