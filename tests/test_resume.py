"""Plan step 10: a run killed after `map_lines` resumes from its checkpoint with the same
`thread_id`, and the nodes that finished do not run again (spec, Failure modes: crash mid-run)."""

import shutil
import sqlite3
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

from app.graph import NodeFn
from tests.graph_support import deps_for, resume_graph, run_graph, summary_of
from tests.scripted_model import ScriptedModel

NEEDS_SOFFICE = pytest.mark.skipif(
    shutil.which("soffice") is None, reason="LibreOffice is required to recalculate"
)


class Killed(Exception):
    """Stands in for a process dying between two nodes."""


def counting(runs: Counter[str], kill_at: str | None = None) -> Callable[[str, NodeFn], NodeFn]:
    """Count each node's executions; raise `Killed` the first time `kill_at` is reached."""
    armed = {"kill": kill_at is not None}

    def wrap(name: str, fn: NodeFn) -> NodeFn:
        def run(state: Mapping[str, Any]) -> Mapping[str, Any]:
            runs[name] += 1
            if name == kill_at and armed["kill"]:
                armed["kill"] = False
                raise Killed
            return fn(state)

        return run

    return wrap


@NEEDS_SOFFICE
def test_resume_after_map_lines_does_not_re_run_finished_nodes(tmp_path: Path) -> None:
    model = ScriptedModel()
    runs: Counter[str] = Counter()
    deps = deps_for(tmp_path, client=model)

    with pytest.raises(Killed):
        run_graph(deps, wrap=counting(runs, kill_at="run_reconciliations"))
    assert runs == Counter(
        {
            "ingest": 1,
            "triage": 1,
            "extract_statements": 1,
            "map_lines": 1,
            "run_reconciliations": 1,
        }
    )
    model_calls_before_kill = model.calls
    assert model_calls_before_kill == 3  # three pages read, nothing else yet

    state, nodes = resume_graph(deps, wrap=counting(runs))
    assert nodes == ["run_reconciliations", "write_workbook", "verify_workbook", "finalize"]
    assert runs["ingest"] == runs["triage"] == 1
    assert runs["extract_statements"] == runs["map_lines"] == 1
    assert model.calls == model_calls_before_kill  # no page was read again
    summary = summary_of(state)
    assert summary.status == "delivered"
    assert summary.workbook is not None
    assert Path(summary.workbook.path).name == "run-test_v1.xlsx"


@NEEDS_SOFFICE
def test_resumed_run_gives_the_same_result_as_an_uninterrupted_one(tmp_path: Path) -> None:
    whole, _ = run_graph(deps_for(tmp_path / "whole"))
    deps = deps_for(tmp_path / "killed")
    with pytest.raises(Killed):
        run_graph(deps, wrap=counting(Counter(), kill_at="write_workbook"))
    resumed, _ = resume_graph(deps)
    a, b = summary_of(whole), summary_of(resumed)
    assert a.model_dump(exclude={"workbook"}) == b.model_dump(exclude={"workbook"})


def test_the_checkpoint_is_keyed_by_the_run_id(tmp_path: Path) -> None:
    deps = deps_for(tmp_path)
    with pytest.raises(Killed):
        run_graph(deps, "run-key-check", wrap=counting(Counter(), kill_at="triage"))
    with sqlite3.connect(deps.settings.checkpoint_db) as conn:
        threads = {row[0] for row in conn.execute("select distinct thread_id from checkpoints")}
    assert threads == {"run-key-check"}


@NEEDS_SOFFICE
def test_a_node_that_never_finished_runs_again_on_resume(tmp_path: Path) -> None:
    deps = deps_for(tmp_path)
    with pytest.raises(Killed):
        run_graph(deps, wrap=counting(Counter(), kill_at="ingest"))
    state, nodes = resume_graph(deps)
    assert nodes[0] == "ingest"
    assert state["status"] == "delivered"
