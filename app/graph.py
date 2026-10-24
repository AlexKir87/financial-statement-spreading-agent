"""Graph wiring for one spreading run (the spec "Graph"; plan step 10).

Every node is a plain function of the state. The graph adds only the order, the two branches that
end a run early (`triage`, `digitize`, the spread step) and the repair branch. The repair node runs
the capped loop of `app/repair/` once, so the attempt cap and the token cap live in one place and
`recursion_limit` is only a backstop. A SQLite checkpointer keyed by `thread_id` = `run_id` lets a
killed run resume without re-running finished nodes ([[langgraph-checkpointers]]). No `interrupt()`
is used: review happens after the run (spec, Human-approval points).

Clock, run ids and the model client are injected. Nothing here writes an amount: `spread` is
rebuilt from printed values by `build_spread`, and the workbook writer takes numbers only from it.
"""

import logging
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Final

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Overwrite

from app import periods as period_keys
from app.config import Settings
from app.digitize import DigitizerRoute, digitize_pages
from app.extract import extract_statements, page_text
from app.ingest import (
    IngestResult,
    detect_language,
    is_supported_language,
    triage,
)
from app.mapping import MappingTable, map_lines
from app.reconcile import DEFAULT_RULES, ReconciliationReport, run_reconciliations
from app.repair import (
    RepairOutcome,
    RereadResult,
    SpreadError,
    build_spread,
    pick_run_scale,
    repair_failed_checks,
)
from app.state import (
    CHECK_ORDER,
    CheckStatus,
    CheckSummary,
    DigitizedPage,
    OpenIssue,
    RawLine,
    RunStatus,
    RunSummary,
    ScaleInfo,
    SpreadState,
    TokenTotal,
    TraceEntry,
    Usage,
    WorkbookRef,
    initial_state,
    mapping_key,
)
from app.tools.model_client import ModelClient, StructuredCaller, make_caller
from app.workbook import LayoutRow, WorkbookData, verify_version, write_workbook

# Backstop under the counted caps: the longest path is ten nodes and the repair loop is inside
# one node, so this limit is never reached by a healthy run.
RECURSION_LIMIT: Final = 25
STATEMENT_MISSING: Final = "statement_missing_in_source"

# Nodes that stand for a spec tool; a node absent here (triage, finalize) is not a tool call.
NODE_TOOLS: Final[Mapping[str, str]] = {
    "ingest": "load_documents",
    "digitize": "digitize_pages",
    "extract_statements": "extract_statements",
    "map_lines": "map_lines",
    "run_reconciliations": "run_reconciliations",
    "repair": "diagnose_difference",
    "write_workbook": "write_workbook",
    "verify_workbook": "verify_workbook",
}

NodeFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]
Wrap = Callable[[str, NodeFn], NodeFn]


class FinalizeBlocked(RuntimeError):
    """The workbook was not verified, so the run is not finalized (spec, Failure modes)."""


@dataclass(frozen=True)
class Deps:
    """What the nodes need from outside: settings, the model client, the clock and the input.

    `load` returns the ingested filing (called once, by the `ingest` node). `route_factory`
    builds the digitizer route around the caller a node hands it, so a scan's pages count against
    the run's token cap; without it a scan cannot be read.
    """

    settings: Settings
    client: ModelClient
    table: MappingTable
    request_text: str
    load: Callable[[], IngestResult]
    now: Callable[[], datetime]
    logger: logging.Logger | None = None
    route_factory: Callable[[StructuredCaller], DigitizerRoute] | None = None


# --- Pure helpers ----------------------------------------------------------------------


def order_periods(periods: Iterable[str]) -> tuple[list[str], bool]:
    """Periods oldest first, and whether the order had to be assumed.

    If every label is a canonical key (`app/periods.py`) they sort by end date, longer span first.
    Else if every label carries a four-digit year they sort by it (then by label). Otherwise filings
    print the newest column first, so the first-seen order is reversed and the caller records
    `period_order_assumed`.
    """
    seen = list(dict.fromkeys(periods))
    if period_keys.all_canonical(seen):
        return sorted(seen, key=period_keys.sort_key), False
    years = []
    for p in seen:
        found = re.findall(r"(?<!\d)(?:19|20)\d{2}(?!\d)", p)
        if not found:
            return seen[::-1], len(seen) > 1
        years.append(int(found[-1]))
    return [p for _, p in sorted(zip(years, seen, strict=True))], False


def unique_issues(issues: Iterable[OpenIssue]) -> list[OpenIssue]:
    """Drop repeats and list `statement_missing_in_source` first, as the spec's cases expect."""
    kept = list(dict.fromkeys(issues))
    return sorted(kept, key=lambda i: i.code != STATEMENT_MISSING)


def compute_status(state: Mapping[str, Any]) -> RunStatus:
    if state.get("not_delivered_reason") is not None:
        return "not_delivered"
    failed = any(r.status == "fail" for r in state["check_results"])
    missing = any(i.code == STATEMENT_MISSING for i in state["open_issues"])
    return "delivered_with_exceptions" if failed or state["exceptions"] or missing else "delivered"


def _scale_info(state: Mapping[str, Any]) -> ScaleInfo:
    scale = pick_run_scale(state["raw_lines"], state["mapping"]) or "units"
    currencies = {
        ln.currency
        for ln in state["raw_lines"]
        if mapping_key(ln.period, ln.raw_line_id) in state["mapping"]
    }
    return ScaleInfo(unit=scale, currency=next(iter(currencies)) if len(currencies) == 1 else "XXX")


def layout_from(table: MappingTable) -> tuple[LayoutRow, ...]:
    """Statement rows from the standard line set: labels in file order, every row a printed value
    (subtotals are the filing's own numbers; the six checks compare them, the writer does not
    recompute them)."""
    return tuple(LayoutRow(line_id=ln.line_id, label=ln.label) for ln in table.lines)


def _used(state: Mapping[str, Any]) -> int:
    usage: Usage = state["token_usage"]
    return usage.total_tokens


def _caller(deps: Deps, state: Mapping[str, Any]) -> StructuredCaller:
    """A caller that starts from what earlier nodes spent, so the cap is per run, not per node."""
    return make_caller(deps.settings, deps.client, tokens_used=_used(state), logger=deps.logger)


def _workbook_data(deps: Deps, state: Mapping[str, Any]) -> WorkbookData:
    return WorkbookData(
        run_id=state["run_id"],
        company_ref=state["company_ref"],
        status=compute_status(state),
        scale=_scale_info(state),
        tolerance=deps.settings.rounding_tolerance_units,
        created=deps.now(),
        spread=state["spread"],
        raw_lines=tuple(state["raw_lines"]),
        mapping=state["mapping"],
        digitized=tuple(state["digitized"]),
        check_results=tuple(state["check_results"]),
        exceptions=tuple(state["exceptions"]),
        open_issues=tuple(unique_issues(state["open_issues"])),
        change_log=tuple(state["change_log"]),
        layout=layout_from(deps.table),
    )


# --- Nodes -----------------------------------------------------------------------------


def _make_nodes(deps: Deps) -> dict[str, NodeFn]:
    def ingest(state: Mapping[str, Any]) -> dict[str, Any]:
        result = deps.load()
        return {
            "documents": list(result.documents),
            "digitized": list(result.digitized),
            "open_issues": list(result.open_issues),
        }

    def triage_node(state: Mapping[str, Any]) -> dict[str, Any]:
        outcome = triage(
            deps.request_text,
            IngestResult(tuple(state["documents"]), tuple(state["digitized"]), ()),
        )
        return {
            "open_issues": list(outcome.open_issues),
            "not_delivered_reason": outcome.not_delivered_reason,
        }

    def digitize(state: Mapping[str, Any]) -> dict[str, Any]:
        scans = [d for d in state["documents"] if d.kind == "scan"]
        pages: list[DigitizedPage] = []
        issues: list[OpenIssue] = []
        usage = Usage()
        if deps.route_factory is None:
            issues.append(
                OpenIssue(
                    code="digitizer_not_configured",
                    where="digitize",
                    detail="a scanned document needs a digitizer route",
                )
            )
        else:
            route = deps.route_factory(_caller(deps, state))
            for doc in scans:
                result = digitize_pages(route, doc.doc_id, 1, doc.page_count)
                pages.extend(result.pages)
                issues.extend(result.open_issues)
                usage = Usage(
                    input_tokens=usage.input_tokens + result.usage.input_tokens,
                    output_tokens=usage.output_tokens + result.usage.output_tokens,
                )
        out: dict[str, Any] = {
            "digitized": pages,
            "open_issues": issues,
            "token_usage": usage,
        }
        readable = [*state["digitized"], *pages]
        if not any(page_text(p).strip() for p in readable):
            out["not_delivered_reason"] = "unreadable_source"
        else:
            # The gate could not judge a scan before it was read; run it on the digitized text.
            language = detect_language("\n".join(page_text(p) for p in pages))
            if pages and not is_supported_language(language):
                out["not_delivered_reason"] = "unsupported_language"
                out["open_issues"] = [
                    *issues,
                    OpenIssue(
                        code="unsupported_language",
                        where=", ".join(d.doc_id for d in scans),
                        detail=f"detected {language}; English-language filings only",
                    ),
                ]
        return out

    def extract_node(state: Mapping[str, Any]) -> dict[str, Any]:
        result = extract_statements(state["digitized"], _caller(deps, state))
        return {
            "raw_lines": list(result.raw_lines),
            "open_issues": list(result.open_issues),
            "token_usage": result.usage,
        }

    def map_node(state: Mapping[str, Any]) -> dict[str, Any]:
        result = map_lines(state["raw_lines"], deps.table, _caller(deps, state))
        return {
            "mapping": result.mapping,
            "open_issues": list(result.open_issues),
            "token_usage": result.usage,
        }

    def reconcile(state: Mapping[str, Any]) -> dict[str, Any]:
        run_scale = pick_run_scale(state["raw_lines"], state["mapping"])
        flipped: list[str] = []
        try:
            spread = build_spread(
                state["raw_lines"],
                state["mapping"],
                run_scale=run_scale,
                tolerance=deps.settings.rounding_tolerance_units,
                notes=flipped,
            )
        except SpreadError as exc:
            return {
                "not_delivered_reason": "unreadable_source",
                "open_issues": [
                    OpenIssue(code="spread_not_built", where="spread", detail=str(exc))
                ],
            }
        if not spread:
            return {
                "not_delivered_reason": "unreadable_source",
                "open_issues": [
                    OpenIssue(
                        code="no_statement_lines",
                        where="spread",
                        detail="no mapped statement lines were found in the source",
                    )
                ],
            }
        periods, assumed = order_periods(spread)
        issues: list[OpenIssue] = []
        if assumed:
            issues.append(
                OpenIssue(
                    code="period_order_assumed",
                    where="spread",
                    detail="period labels carry no year; printed order (newest first) was reversed",
                )
            )
        if any(
            ln.scale != run_scale
            for ln in state["raw_lines"]
            if mapping_key(ln.period, ln.raw_line_id) in state["mapping"]
        ):
            issues.append(
                OpenIssue(
                    code="scale_normalised",
                    where="spread",
                    detail=f"lines printed in another scale were converted to {run_scale}",
                )
            )
        if flipped:
            issues.append(
                OpenIssue(
                    code="cost_sign_normalised",
                    where="spread",
                    detail=(
                        "costs are printed as positive numbers (the printed subtotals prove it); "
                        f"read as negative for the checks: {', '.join(flipped)}"
                    ),
                )
            )
        report = run_reconciliations(
            {p: spread[p] for p in periods if p in spread},
            periods=periods,
            rules=DEFAULT_RULES,
            tolerance=deps.settings.rounding_tolerance_units,
        )
        return {
            "spread": spread,
            "check_results": list(report.results),
            "open_issues": [*issues, *report.open_issues],
        }

    def repair(state: Mapping[str, Any]) -> dict[str, Any]:
        caller = _caller(deps, state)
        periods, _ = order_periods(state["spread"])
        run_scale = pick_run_scale(state["raw_lines"], state["mapping"])

        def reread(doc_id: str, page: int, shared: StructuredCaller) -> RereadResult:
            stored = next(
                (p for p in state["digitized"] if p.doc_id == doc_id and p.page == page), None
            )
            if stored is None:
                raise ValueError("page is not in the digitized pages")
            source = stored
            scan = any(d.doc_id == doc_id and d.kind == "scan" for d in state["documents"])
            if scan and deps.route_factory is not None:
                fresh = digitize_pages(deps.route_factory(shared), doc_id, page, page)
                if fresh.pages:
                    source = fresh.pages[0]
            extracted = extract_statements([source], shared)
            mapped = map_lines(extracted.raw_lines, deps.table, shared)
            return RereadResult(extracted.raw_lines, mapped.mapping)

        outcome: RepairOutcome = repair_failed_checks(
            raw_lines=state["raw_lines"],
            mapping=state["mapping"],
            report=ReconciliationReport(results=tuple(state["check_results"]), open_issues=()),
            periods=periods,
            caller=caller,
            table=deps.table,
            max_attempts=deps.settings.max_repair_attempts,
            now=deps.now,
            reread=reread,
            rules=DEFAULT_RULES,
            tolerance=deps.settings.rounding_tolerance_units,
            run_scale=run_scale,
        )
        known = set(state["open_issues"])
        out: dict[str, Any] = {
            # A re-read replaces its page's lines, so these two channels are overwritten.
            "raw_lines": Overwrite(list(outcome.raw_lines)),
            "mapping": Overwrite(dict(outcome.mapping)),
            "check_results": list(outcome.report.results),
            # The spec's exception carries the size of the difference; the signed figures (actual
            # minus expected, as the engine reports them) stay in its explanation.
            "exceptions": [
                note.model_copy(update={"difference": abs(note.difference)})
                for note in outcome.exceptions
            ],
            "open_issues": [
                *outcome.open_issues,
                *(i for i in outcome.report.open_issues if i not in known),
            ],
            "change_log": list(outcome.change_log),
            "repair_attempts": outcome.attempts_made,
            "token_usage": outcome.usage,
        }
        if outcome.spread:  # empty only when repair could not rebuild a spread at all
            out["spread"] = outcome.spread
        return out

    def write_node(state: Mapping[str, Any]) -> dict[str, Any]:
        version = write_workbook(_workbook_data(deps, state), deps.settings.output_dir)
        return {"workbook_versions": [version]}

    def verify_node(state: Mapping[str, Any]) -> dict[str, Any]:
        version, result = verify_version(state["workbook_versions"][-1])
        out: dict[str, Any] = {"workbook_versions": [version]}
        if not result.ok:
            out["open_issues"] = [
                OpenIssue(
                    code="workbook_verification_failed",
                    where=f"{version.path.rsplit('/', 1)[-1]}",
                    detail="; ".join(result.problems[:5]),
                )
            ]
        return out

    def finalize(state: Mapping[str, Any]) -> dict[str, Any]:
        if state["not_delivered_reason"] is not None:
            return {"status": "not_delivered"}
        versions = state["workbook_versions"]
        if not versions or versions[-1].verified is not True:
            raise FinalizeBlocked("the workbook is not verified, so the run is not finalized")
        return {"status": compute_status(state)}

    return {
        "ingest": ingest,
        "triage": triage_node,
        "digitize": digitize,
        "extract_statements": extract_node,
        "map_lines": map_node,
        "run_reconciliations": reconcile,
        "repair": repair,
        "write_workbook": write_node,
        "verify_workbook": verify_node,
        "finalize": finalize,
    }


# --- Wiring ----------------------------------------------------------------------------


def _ended(state: Mapping[str, Any]) -> bool:
    return state["not_delivered_reason"] is not None


def _after_triage(state: Mapping[str, Any]) -> str:
    if _ended(state):
        return "finalize"
    return "digitize" if any(d.kind == "scan" for d in state["documents"]) else "extract_statements"


def _after_digitize(state: Mapping[str, Any]) -> str:
    return "finalize" if _ended(state) else "extract_statements"


def _after_reconcile(state: Mapping[str, Any]) -> str:
    if _ended(state):
        return "finalize"
    failed = any(r.status == "fail" for r in state["check_results"])
    return "repair" if failed else "write_workbook"


def build_graph(
    deps: Deps, checkpointer: BaseCheckpointSaver[Any], *, wrap: Wrap | None = None
) -> Any:
    """Compile the graph. `wrap(name, fn)` lets the caller add tracing around each node."""
    builder = StateGraph(SpreadState)
    for name, fn in _make_nodes(deps).items():
        node: Any = wrap(name, fn) if wrap is not None else fn
        builder.add_node(name, node)
    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "triage")
    builder.add_conditional_edges(
        "triage", _after_triage, ["finalize", "digitize", "extract_statements"]
    )
    builder.add_conditional_edges("digitize", _after_digitize, ["finalize", "extract_statements"])
    builder.add_edge("extract_statements", "map_lines")
    builder.add_edge("map_lines", "run_reconciliations")
    builder.add_conditional_edges(
        "run_reconciliations", _after_reconcile, ["finalize", "repair", "write_workbook"]
    )
    builder.add_edge("repair", "write_workbook")
    builder.add_edge("write_workbook", "verify_workbook")
    builder.add_edge("verify_workbook", "finalize")
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer)


def run_config(run_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id}, "recursion_limit": RECURSION_LIMIT}


def _drive(graph: Any, graph_input: Any, run_id: str) -> tuple[dict[str, Any], list[str]]:
    config = run_config(run_id)
    nodes: list[str] = []
    for update in graph.stream(graph_input, config, stream_mode="updates"):
        for name, change in update.items():
            if name == "repair" and isinstance(change, dict):
                nodes.extend(["repair"] * int(change.get("repair_attempts") or 0))
            else:
                nodes.append(name)
    return dict(graph.get_state(config).values), nodes


def start_run(graph: Any, run_id: str, company_ref: str) -> tuple[dict[str, Any], list[str]]:
    """Run from the start. Returns the final state and the nodes that ran, in order."""
    return _drive(graph, initial_state(run_id, company_ref), run_id)


def resume_run(graph: Any, run_id: str) -> tuple[dict[str, Any], list[str]]:
    """Continue a stopped run from its last checkpoint; finished nodes do not run again."""
    return _drive(graph, None, run_id)


# --- Output ----------------------------------------------------------------------------


def _check_summaries(state: Mapping[str, Any]) -> dict[Any, CheckSummary]:
    out: dict[Any, CheckSummary] = {}
    for key in CHECK_ORDER:
        results = [r for r in state["check_results"] if r.key == key]
        live = [r for r in results if r.status != "not_applicable"]
        if not live:
            out[key] = CheckSummary(status="not_applicable")
            continue
        status: CheckStatus = "fail" if any(r.status == "fail" for r in live) else "pass"
        largest = max((abs(r.difference or Decimal(0)) for r in live), default=Decimal(0))
        out[key] = CheckSummary(status=status, max_abs_diff=largest)
    return out


def _trace(
    raw_lines: Sequence[RawLine], mapping: Mapping[str, Any], spread: Mapping[str, Any]
) -> dict[str, dict[str, TraceEntry]]:
    trace: dict[str, dict[str, TraceEntry]] = {}
    for line in raw_lines:
        mapped = mapping.get(mapping_key(line.period, line.raw_line_id))
        if mapped is None or mapped.standard_line_id not in spread.get(line.period, {}):
            continue
        cell = trace.setdefault(line.period, {})
        cell.setdefault(
            mapped.standard_line_id,
            TraceEntry(doc_id=line.doc_id, page=line.page, original_label=line.shown_label),
        )
    return trace


def build_summary(state: Mapping[str, Any]) -> RunSummary:
    """The spec's `RunSummary` from a finished run's state."""
    usage = TokenTotal(total_tokens=_used(state))
    issues = tuple(unique_issues(state["open_issues"]))
    reason = state["not_delivered_reason"]
    if reason is not None:
        return RunSummary(
            status="not_delivered",
            not_delivered_reason=reason,
            company_ref=state["company_ref"],
            open_issues=issues,
            usage=usage,
        )
    version = state["workbook_versions"][-1]
    scale = _scale_info(state)
    spread = state["spread"]
    return RunSummary(
        status=state["status"] or compute_status(state),
        company_ref=state["company_ref"],
        workbook=WorkbookRef(
            path=version.path, version=version.version, verified=version.verified is True
        ),
        scale=scale,
        checks=_check_summaries(state),
        repair_attempts=state["repair_attempts"],
        spread={p: dict(cells) for p, cells in spread.items()},
        trace=_trace(state["raw_lines"], state["mapping"], spread),
        exceptions=tuple(state["exceptions"]),
        open_issues=issues,
        usage=usage,
    )
