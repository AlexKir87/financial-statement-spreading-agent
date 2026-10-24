"""Re-run a finished run after analyst corrections (plan step 11; [[langgraph-time-travel]]).

The stored state is loaded from the checkpoint, the corrections are written into it with
`update_state(..., as_node="map_lines")`, and the graph continues from `run_reconciliations`:
checks, repair if a check still fails, a new workbook version, verification, finalize. Earlier
versions are never touched (the writer only creates the next `_vN.xlsx`).

Idempotence: the applied `Correction`s live in the state's `corrections` channel, so the same
file applied twice changes nothing the second time. If a previous call died after writing the
corrections but before the re-run finished, the next call finishes that re-run first and does
not apply anything twice.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.types import Overwrite

from app.corrections.apply import apply_corrections
from app.corrections.reader import CorrectionsError, read_corrections
from app.graph import resume_run, run_config
from app.mapping import MappingTable
from app.state import OpenIssue


@dataclass(frozen=True)
class CorrectionsRun:
    """The run's state after the call, the nodes that ran, the correction ids applied now and
    the issues for corrections that were refused (also in the new workbook's open issues when
    anything was applied)."""

    state: dict[str, Any]
    nodes: tuple[str, ...]
    applied: tuple[str, ...]
    rejected: tuple[OpenIssue, ...]


def apply_corrections_to_run(
    graph: Any,
    run_id: str,
    *,
    table: MappingTable,
    now: Callable[[], datetime],
    corrections_path: Path | str | None = None,
) -> CorrectionsRun:
    """Apply the corrections in `corrections_path` (default: the run's latest workbook)."""
    config = run_config(run_id)
    snapshot = graph.get_state(config)
    nodes: list[str] = []
    if not snapshot.values:
        raise CorrectionsError("no stored run with that id")
    if snapshot.next:
        if not snapshot.values["corrections"]:
            raise CorrectionsError("the run has not finished; resume it first")
        _, ran = resume_run(graph, run_id)  # a correction re-run that was cut short
        nodes.extend(ran)
        snapshot = graph.get_state(config)
    state = dict(snapshot.values)
    versions = state["workbook_versions"]
    if state["not_delivered_reason"] is not None or not versions:
        raise CorrectionsError("the run delivered no workbook, so there is nothing to correct")
    source = Path(corrections_path) if corrections_path is not None else Path(versions[-1].path)
    read = read_corrections(source, run_id=run_id)
    outcome = apply_corrections(
        corrections=read.corrections,
        applied=state["corrections"],
        raw_lines=state["raw_lines"],
        mapping=state["mapping"],
        table=table,
        now=now,
    )
    rejected = (*read.open_issues, *outcome.open_issues)
    if not outcome.applied:
        return CorrectionsRun(state, tuple(nodes), (), rejected)
    graph.update_state(
        config,
        {
            "mapping": outcome.mapping,
            "corrections": list(outcome.applied),
            "change_log": list(outcome.change_log),
            "open_issues": list(rejected),
            # The last pass's exceptions described the old mapping; the re-run writes its own.
            "exceptions": Overwrite([]),
            "repair_attempts": None,
            "status": None,
        },
        as_node="map_lines",
    )
    final, ran = resume_run(graph, run_id)
    nodes.extend(ran)
    return CorrectionsRun(
        final, tuple(nodes), tuple(c.correction_id for c in outcome.applied), rejected
    )
