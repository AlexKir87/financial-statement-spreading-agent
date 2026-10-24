"""Apply analyst corrections to the mapping (plan step 11). Pure: no model, no file, no network.

A correction re-maps one raw line to one standard line. It is applied once, by `correction_id`:
an id already in `applied` is skipped (same content) or flagged (different content). Each applied
correction becomes a `ChangeEntry` with the analyst as `who`, and the new mapping carries
`origin="analyst"`, which repair never overrides. A correction that does not fit (unknown line,
unknown or wrong-statement target) is rejected with an open issue and changes nothing. The whole
batch is refused if the corrected mapping can no longer form a spread, so a bad edit cannot
leave the run without a workbook.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from app.mapping import MappingTable
from app.mapping.table import EQUITY_COLUMN_LINE
from app.repair import SpreadError, build_spread, pick_run_scale
from app.state import ChangeEntry, Correction, MappedLine, OpenIssue, RawLine, mapping_key

_STATEMENT_PREFIX = {"IS": "is_", "BS": "bs_", "CF": "cf_", "SE": "bs_"}


@dataclass(frozen=True)
class CorrectionOutcome:
    """`mapping` holds only the changed entries (the state's mapping channel merges by key)."""

    mapping: dict[str, MappedLine]
    applied: tuple[Correction, ...]
    change_log: tuple[ChangeEntry, ...]
    open_issues: tuple[OpenIssue, ...]


def _rejected(correction: Correction, reason: str) -> OpenIssue:
    return OpenIssue(
        code="correction_rejected",
        where=f"correction {correction.correction_id}",
        detail=f"not applied: {reason}",
    )


def apply_corrections(
    *,
    corrections: Sequence[Correction],
    applied: Sequence[Correction],
    raw_lines: Sequence[RawLine],
    mapping: Mapping[str, MappedLine],
    table: MappingTable,
    now: Callable[[], datetime],
) -> CorrectionOutcome:
    """Corrections not yet applied, in order; a later one for the same line wins."""
    done = {c.correction_id: c for c in applied}
    lines = {(ln.period, ln.raw_line_id): ln for ln in raw_lines}
    current = dict(mapping)
    changed: dict[str, MappedLine] = {}
    new: list[Correction] = []
    log: list[ChangeEntry] = []
    issues: list[OpenIssue] = []
    for c in corrections:
        earlier = done.get(c.correction_id)
        if earlier is not None:
            if earlier != c:
                issues.append(_rejected(c, "this correction id was used before with other content"))
            continue
        line = lines.get((c.period, c.raw_line_id))
        if line is None:
            issues.append(_rejected(c, "no such line in this run"))
            continue
        if c.standard_line_id not in table.line_ids():
            issues.append(_rejected(c, "unknown standard line id"))
            continue
        if (line.statement == "SE") != (c.standard_line_id == EQUITY_COLUMN_LINE):
            issues.append(_rejected(c, "only equity statement rows take the equity column line"))
            continue
        if not c.standard_line_id.startswith(_STATEMENT_PREFIX[line.statement]):
            issues.append(_rejected(c, f"the line is on the {line.statement}; target is another"))
            continue
        key = mapping_key(c.period, c.raw_line_id)
        before = current.get(key)
        corrected = MappedLine(
            raw_line_id=c.raw_line_id,
            period=c.period,
            standard_line_id=c.standard_line_id,
            confidence=1.0,
            rationale=f"analyst {c.who}: {c.why}",
            origin="analyst",
        )
        current[key] = changed[key] = corrected
        new.append(c)
        was = before.standard_line_id if before else "unmapped"
        verb = "confirmed" if was == c.standard_line_id else "re-mapped"
        log.append(
            ChangeEntry(
                who=c.who,
                what=(
                    f"{verb} '{' '.join(line.original_label.split())}' ({c.period}, "
                    f"page {line.page}) from {was} to {c.standard_line_id}"
                ),
                when=now(),
                why=c.why,
            )
        )
    if new:
        try:
            build_spread(raw_lines, current, run_scale=pick_run_scale(raw_lines, current))
        except SpreadError as exc:
            return CorrectionOutcome(
                {},
                (),
                (),
                (
                    *issues,
                    OpenIssue(
                        code="correction_rejected",
                        where="corrections",
                        detail=f"none applied: the corrected mapping gives no spread ({exc})",
                    ),
                ),
            )
    return CorrectionOutcome(changed, tuple(new), tuple(log), tuple(issues))
