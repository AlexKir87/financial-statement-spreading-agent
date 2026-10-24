"""The `diagnose_difference` call: one failed check in, a proposal to re-map or re-read out.

The reply schema has no amount field and `cause` may not contain a digit, so the model cannot
hand back a number. Which lines it may touch is decided here from the failed check's cells.
"""

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.mapping.table import MappingTable
from app.prompts import load_prompt
from app.state import CheckResult, LineId, MappedLine, RawLine, Statement, mapping_key
from app.tools.model_client import StructuredCaller

MAX_CANDIDATES = 150
_PREFIX_STATEMENT: dict[str, Statement] = {"is": "IS", "bs": "BS", "cf": "CF"}


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Remap(_Out):
    raw_line_id: str = Field(min_length=1, max_length=120)
    standard_line_id: LineId
    confidence: float = Field(ge=0, le=1)


class Reread(_Out):
    doc_id: str = Field(min_length=1, max_length=120)
    page: int = Field(ge=1)


class Diagnosis(_Out):
    cause: str = Field(min_length=1, max_length=300)
    action: Literal["remap", "reread", "none"]
    remaps: tuple[Remap, ...] = Field(default=(), max_length=10)
    reread: Reread | None = None

    @model_validator(mode="after")
    def _shape_matches_action(self) -> "Diagnosis":
        if re.search(r"\d", self.cause):
            raise ValueError("cause must not contain digits or amounts")
        if self.action == "remap" and not self.remaps:
            raise ValueError("action remap needs at least one remap")
        if self.action != "remap" and self.remaps:
            raise ValueError("remaps are only allowed with action remap")
        if (self.action == "reread") != (self.reread is not None):
            raise ValueError("reread is required with action reread and allowed only then")
        return self


@dataclass(frozen=True)
class RereadResult:
    """A page read again: its lines (amounts parsed from the printed text, as in extraction)
    and their mappings, keyed by `mapping_key`."""

    raw_lines: tuple[RawLine, ...]
    mapping: dict[str, MappedLine]


Rereader = Callable[[str, int, StructuredCaller], RereadResult]


@dataclass(frozen=True)
class Candidate:
    line: RawLine
    mapped_to: str | None


def candidate_lines(
    failed: CheckResult, raw_lines: Sequence[RawLine], mapping: Mapping[str, MappedLine]
) -> list[Candidate]:
    """Lines of the statements and periods the failed check compared, those already mapped to a
    compared cell first. Nothing outside them can be re-mapped or re-read."""
    periods = {t.period for t in failed.terms} | {failed.period}
    statements = {_PREFIX_STATEMENT[t.line_id.split("_", 1)[0]] for t in failed.terms}
    compared = {t.line_id for t in failed.terms}
    found = []
    for line in raw_lines:
        if line.period not in periods or line.statement not in statements:
            continue
        mapped = mapping.get(mapping_key(line.period, line.raw_line_id))
        found.append(Candidate(line, mapped.standard_line_id if mapped else None))
    found.sort(key=lambda c: c.mapped_to not in compared)  # stable: filing order is kept
    return found[:MAX_CANDIDATES]


def _data(text: str) -> str:
    return " ".join(text.replace("<", " ").replace(">", " ").split())


def diagnose(
    caller: StructuredCaller,
    failed: CheckResult,
    candidates: Sequence[Candidate],
    table: MappingTable,
    *,
    reread_available: bool,
) -> Diagnosis:
    """One counted model call. A reply that fails the schema or the checks below is retried
    once with the reason, then raises `InvalidModelOutput`."""
    by_id = {c.line.raw_line_id: c for c in candidates}
    pages = {(c.line.doc_id, c.line.page) for c in candidates}
    statements = sorted({c.line.statement for c in candidates})
    allowed = "\n".join(f"{s}: {', '.join(sorted(table.line_ids(s)))}" for s in statements)
    cells = "\n".join(f"- {t.line_id} | {t.period} | {t.value}" for t in failed.terms) or "- none"
    listing = "\n".join(
        f"{c.line.raw_line_id} | {c.line.doc_id} | page {c.line.page} | {c.line.statement} | "
        f"{c.line.period} | {_data(c.line.original_label)} | {_data(c.line.original_value_text)} | "
        f"{c.mapped_to or 'unmapped'}"
        for c in candidates
    )
    user = (
        f"Failed check: {failed.key}, period {failed.period}. "
        f"Expected {failed.expected}, actual {failed.actual}, difference {failed.difference}.\n"
        f"Cells compared (line id | period | value):\n{cells}\n\n"
        f"Re-read is {'available' if reread_available else 'not available'}.\n"
        f"Allowed standard_line_id values:\n{allowed}\n\n"
        "Lines you may touch (raw_line_id | doc_id | page | statement | period | label | "
        f"printed value | current standard_line_id):\n<lines>\n{listing}\n</lines>"
    )

    def check(d: Diagnosis) -> None:
        seen: set[str] = set()
        for r in d.remaps:
            cand = by_id.get(r.raw_line_id)
            if cand is None:
                raise ValueError(f"{r.raw_line_id!r} is not one of the listed lines")
            if r.raw_line_id in seen:
                raise ValueError(f"{r.raw_line_id!r} proposed twice")
            seen.add(r.raw_line_id)
            if r.standard_line_id not in table.line_ids(cand.line.statement):
                raise ValueError(
                    f"{r.standard_line_id!r} is not an allowed {cand.line.statement} line id"
                )
            if r.standard_line_id == cand.mapped_to:
                raise ValueError(f"{r.raw_line_id!r} is already mapped to {r.standard_line_id!r}")
        if d.reread is not None:
            if not reread_available:
                raise ValueError("re-read is not available")
            if (d.reread.doc_id, d.reread.page) not in pages:
                raise ValueError("that page is not one of the listed lines' pages")

    system = load_prompt("diagnose_difference", caller.prompt_version)
    return caller.call(system=system, user=user, schema=Diagnosis, check=check)
