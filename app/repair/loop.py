"""The capped repair loop: diagnose one failed check, apply a re-map or re-read, re-run checks.

The loop never writes a figure. It changes `mapping` or swaps in lines re-read from a printed
page, rebuilds `spread` from them (`build_spread`) and lets the reconciliation engine judge the
result. A change is kept only if it leaves the spread strictly better (fewer failing results, or
the same number with a smaller total difference); otherwise it is undone. When the attempt cap
or the token cap is reached, every check still failing becomes an `ExceptionNote` with its exact
difference and what was tried.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.mapping.table import MappingTable
from app.reconcile import DEFAULT_RULES, ReconciliationReport, ReconRules, run_reconciliations
from app.repair.diagnosis import (
    Candidate,
    Diagnosis,
    Rereader,
    RereadResult,
    candidate_lines,
    diagnose,
)
from app.repair.spread import SpreadError, build_spread
from app.state import (
    ChangeEntry,
    CheckResult,
    ExceptionNote,
    MappedLine,
    OpenIssue,
    RawLine,
    Usage,
    mapping_key,
)
from app.telemetry import log_event
from app.tools.model_client import InvalidModelOutput, StructuredCaller, TokenBudgetExceeded

Spread = dict[str, dict[str, Decimal]]


@dataclass(frozen=True)
class RepairOutcome:
    """`raw_lines` and `mapping` are the complete sets after repair (a re-read replaces the
    lines of its page, so the caller overwrites rather than appends). `open_issues` holds only
    what repair added; `report.open_issues` is the final reconciliation's own list."""

    raw_lines: tuple[RawLine, ...]
    mapping: dict[str, MappedLine]
    spread: Spread
    report: ReconciliationReport
    exceptions: tuple[ExceptionNote, ...]
    open_issues: tuple[OpenIssue, ...]
    change_log: tuple[ChangeEntry, ...]
    attempts_made: int
    usage: Usage


def _failing(report: ReconciliationReport) -> list[CheckResult]:
    return [r for r in report.results if r.status == "fail"]


def _score(report: ReconciliationReport) -> tuple[int, Decimal]:
    failing = _failing(report)
    return len(failing), sum((abs(r.difference or Decimal(0)) for r in failing), Decimal(0))


def _plain(value: Decimal) -> str:
    return format(value, "f")


def _analyst_mapped(mapping: Mapping[str, MappedLine], line: RawLine) -> bool:
    """An analyst's correction is final: repair may not re-map or re-read it (plan step 11)."""
    mapped = mapping.get(mapping_key(line.period, line.raw_line_id))
    return mapped is not None and mapped.origin == "analyst"


def _apply_remaps(
    mapping: Mapping[str, MappedLine], diagnosis: Diagnosis, candidates: Sequence[Candidate]
) -> dict[str, MappedLine]:
    by_id = {c.line.raw_line_id: c.line for c in candidates}
    out = dict(mapping)
    for remap in diagnosis.remaps:
        line = by_id[remap.raw_line_id]
        out[mapping_key(line.period, line.raw_line_id)] = MappedLine(
            raw_line_id=line.raw_line_id,
            period=line.period,
            standard_line_id=remap.standard_line_id,
            confidence=remap.confidence,
            rationale=diagnosis.cause,
            origin="model",
        )
    return out


def _apply_reread(
    lines: Sequence[RawLine],
    mapping: Mapping[str, MappedLine],
    doc_id: str,
    page: int,
    result: RereadResult,
) -> tuple[tuple[RawLine, ...], dict[str, MappedLine]]:
    if any(ln.doc_id != doc_id or ln.page != page for ln in result.raw_lines):
        raise ValueError("re-read returned lines from another page")
    new_ids = {ln.raw_line_id for ln in result.raw_lines}
    if len(new_ids) != len(result.raw_lines):
        raise ValueError("re-read returned a raw line id twice")
    if any(m.raw_line_id not in new_ids for m in result.mapping.values()):
        raise ValueError("re-read mapped a line it did not return")
    gone = {ln.raw_line_id for ln in lines if ln.doc_id == doc_id and ln.page == page}
    kept = tuple(ln for ln in lines if ln.raw_line_id not in gone)
    if new_ids & {ln.raw_line_id for ln in kept}:
        raise ValueError("re-read line id clashes with another page")
    merged = {k: m for k, m in mapping.items() if m.raw_line_id not in gone}
    merged.update(result.mapping)
    return (*kept, *result.raw_lines), merged


def _exception_notes(
    report: ReconciliationReport, attempts: int, tried: Sequence[str], stopped: str | None
) -> tuple[ExceptionNote, ...]:
    history = " ".join(tried) if tried else "No repair attempt was made."
    tail = f" Stopped early: {stopped}." if stopped else ""
    notes = []
    for r in _failing(report):
        if r.difference is None:  # a failed result always carries its figures
            continue
        notes.append(
            ExceptionNote(
                check=r.key,
                period=r.period,
                difference=r.difference,
                explanation=(
                    f"{r.key} fails in {r.period}: expected {_plain(r.expected or Decimal(0))}, "
                    f"actual {_plain(r.actual or Decimal(0))}, difference {_plain(r.difference)}. "
                    f"{attempts} repair attempt(s). {history}{tail}"
                ),
                attempts_made=attempts,
            )
        )
    return tuple(notes)


def repair_failed_checks(
    *,
    raw_lines: Sequence[RawLine],
    mapping: Mapping[str, MappedLine],
    report: ReconciliationReport,
    periods: Sequence[str],
    caller: StructuredCaller,
    table: MappingTable,
    max_attempts: int,
    now: Callable[[], datetime],
    reread: Rereader | None = None,
    rules: ReconRules = DEFAULT_RULES,
    tolerance: Decimal = Decimal(1),
    run_scale: str | None = None,
) -> RepairOutcome:
    """Repair up to `max_attempts` times (`SPREAD_MAX_REPAIR_ATTEMPTS`), one failed check per
    attempt, first in canonical order. An attempt is one diagnosis call, valid or not. `caller`
    carries the run's token budget; `reread` (optional) reads one page again through that same
    caller. `periods` is oldest first, as for `run_reconciliations`. `run_scale` converts lines
    printed in another scale when the spread is rebuilt (see `build_spread`)."""
    lines = tuple(raw_lines)
    current = dict(mapping)
    issues: list[OpenIssue] = []
    try:
        spread = build_spread(lines, current, run_scale=run_scale, tolerance=tolerance)
    except SpreadError as exc:  # nothing to repair against; the failures stay as they are
        issues.append(OpenIssue(code="repair_not_applied", where="spread", detail=str(exc)[:300]))
        spread = {}
        max_attempts = 0
    usage_before = caller.usage
    changes: list[ChangeEntry] = []
    tried: list[str] = []
    stopped: str | None = None
    attempts = 0

    def stop_for_token_cap() -> str:
        issues.append(
            OpenIssue(
                code="token_cap_reached",
                where="repair",
                detail="token cap reached; repair stopped",
            )
        )
        return "token cap reached"

    while attempts < max_attempts:
        failing = _failing(report)
        if not failing:
            break
        target = failing[0]
        where = f"{target.key} {target.period}"
        if caller.budget.used >= caller.budget.cap:
            stopped = stop_for_token_cap()
            break
        attempts += 1
        if caller.logger is not None:
            log_event(
                caller.logger,
                "repair.attempt",
                node="diagnose",
                check_key=target.key,
                period=target.period,
                repair_attempts=attempts,
            )
        candidates = [
            c
            for c in candidate_lines(target, lines, current)
            if not _analyst_mapped(current, c.line)
        ]
        try:
            diagnosis = diagnose(
                caller, target, candidates, table, reread_available=reread is not None
            )
        except InvalidModelOutput as exc:
            issues.append(
                OpenIssue(code="diagnosis_invalid_output", where=where, detail=str(exc)[:300])
            )
            tried.append(f"{attempts}. The diagnosis was rejected as invalid.")
            continue
        except TokenBudgetExceeded:
            stopped = stop_for_token_cap()
            tried.append(f"{attempts}. The token cap was reached before a valid diagnosis.")
            break

        if diagnosis.action == "none":
            tried.append(f"{attempts}. No fix proposed (diagnosis: {diagnosis.cause})")
            continue

        try:
            if diagnosis.action == "remap":
                trial_lines, trial_map = lines, _apply_remaps(current, diagnosis, candidates)
                what = "re-mapped " + ", ".join(
                    f"{r.raw_line_id} to {r.standard_line_id}" for r in diagnosis.remaps
                )
            else:
                target_page = diagnosis.reread
                if reread is None or target_page is None:  # `diagnose` already refused this
                    raise ValueError("re-read is not available")
                if any(
                    _analyst_mapped(current, ln)
                    for ln in lines
                    if ln.doc_id == target_page.doc_id and ln.page == target_page.page
                ):
                    raise ValueError("the page holds a line an analyst corrected")
                result = reread(target_page.doc_id, target_page.page, caller)
                trial_lines, trial_map = _apply_reread(
                    lines, current, target_page.doc_id, target_page.page, result
                )
                what = f"re-read {target_page.doc_id} page {target_page.page}"
            trial_spread = build_spread(
                trial_lines, trial_map, run_scale=run_scale, tolerance=tolerance
            )
            trial_report = run_reconciliations(
                trial_spread, periods=periods, rules=rules, tolerance=tolerance
            )
        except TokenBudgetExceeded:
            stopped = stop_for_token_cap()
            tried.append(f"{attempts}. The token cap was reached during the re-read.")
            break
        except (InvalidModelOutput, SpreadError, ValueError) as exc:
            issues.append(OpenIssue(code="repair_not_applied", where=where, detail=str(exc)[:300]))
            tried.append(f"{attempts}. The proposed fix could not be applied.")
            continue

        if _score(trial_report) < _score(report):
            lines, current, spread, report = trial_lines, trial_map, trial_spread, trial_report
            if diagnosis.action == "remap":
                issues.append(
                    OpenIssue(
                        code="repair_remap_applied",
                        where=where,
                        detail=f"model re-map kept for analyst review: {what}",
                    )
                )
            changes.append(ChangeEntry(who="system", what=what, when=now(), why=diagnosis.cause))
            tried.append(f"{attempts}. {what.capitalize()} (diagnosis: {diagnosis.cause})")
        else:
            issues.append(
                OpenIssue(
                    code="repair_reverted",
                    where=where,
                    detail=f"{what} did not reduce the failing checks and was undone",
                )
            )
            tried.append(f"{attempts}. {what.capitalize()}, undone because it did not help.")

    spent = Usage(
        input_tokens=caller.usage.input_tokens - usage_before.input_tokens,
        output_tokens=caller.usage.output_tokens - usage_before.output_tokens,
    )
    return RepairOutcome(
        raw_lines=lines,
        mapping=current,
        spread=spread,
        report=report,
        exceptions=_exception_notes(report, attempts, tried, stopped),
        open_issues=tuple(issues),
        change_log=tuple(changes),
        attempts_made=attempts,
        usage=spent,
    )
