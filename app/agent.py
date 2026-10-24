"""Entry point: `run_agent(text)` for the eval runner and `run_filings` for Library files.

Both build the graph with an injected clock, id source and model client, run it on a SQLite
checkpoint keyed by `run_id`, and return the `RunSummary` as JSON. `run_agent` wraps the pasted
filing as an in-memory text-layer document. The model client is OpenRouter unless
`SPREAD_REPLAY_TRANSCRIPT` names a recorded transcript (offline evals, see `app/tools/replay.py`).
"""

import os
import sys
import uuid
import warnings
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver

from app.config import Settings
from app.corrections import CorrectionsRun, apply_corrections_to_run
from app.digitize import PdfPageImages, VisionRoute
from app.graph import (
    NODE_TOOLS,
    Deps,
    NodeFn,
    build_graph,
    build_summary,
    resume_run,
    start_run,
)
from app.ingest import IngestResult, ingest_documents, ingest_text, split_request
from app.mapping import load_mapping_table
from app.telemetry import build_tracer_provider, get_logger, traced_node
from app.tools.model_client import ModelClient, OpenRouterClient, StructuredCaller
from app.tools.replay import ReplayClient

ENV_REPLAY_TRANSCRIPT = "SPREAD_REPLAY_TRANSCRIPT"
DEFAULT_COMPANY_REF = "company-adhoc"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:16]}"


def client_from_env(settings: Settings) -> ModelClient:
    """A recorded transcript if `SPREAD_REPLAY_TRANSCRIPT` is set, else OpenRouter."""
    transcript = os.environ.get(ENV_REPLAY_TRANSCRIPT)
    if transcript:
        warnings.warn(
            f"{ENV_REPLAY_TRANSCRIPT} is set: model replies come from a recording, not a provider",
            RuntimeWarning,
            stacklevel=2,
        )
        return ReplayClient(transcript)
    return OpenRouterClient(settings.openrouter_api_key)


def _tracing() -> Callable[[str, NodeFn], NodeFn]:
    tracer = build_tracer_provider().get_tracer("spread")
    logger = get_logger(stream=sys.stderr)

    def wrap(name: str, fn: NodeFn) -> NodeFn:
        return traced_node(name, fn, tracer=tracer, logger=logger)

    return wrap


def _result(graph_state: dict[str, Any], nodes: Iterable[str]) -> tuple[str, list[str], int]:
    summary = build_summary(graph_state)
    tools = [NODE_TOOLS[n] for n in nodes if n in NODE_TOOLS]
    return summary.model_dump_json(), tools, summary.usage.total_tokens


def run_filings(
    request_text: str,
    load: Callable[[], IngestResult],
    *,
    company_ref: str = DEFAULT_COMPANY_REF,
    settings: Settings | None = None,
    client: ModelClient | None = None,
    now: Callable[[], datetime] = _utc_now,
    new_run_id: Callable[[], str] = _new_run_id,
    page_images: PdfPageImages | None = None,
) -> tuple[str, list[str], int]:
    """Run one company. Returns the `RunSummary` JSON, the tool names called in order and the
    total tokens used."""
    settings = settings or Settings.from_env()
    model_client = client or client_from_env(settings)
    route_factory: Callable[[StructuredCaller], VisionRoute] | None = None
    if page_images is not None:
        images = page_images

        def route_factory(caller: StructuredCaller) -> VisionRoute:
            return VisionRoute(images, caller)

    deps = Deps(
        settings=settings,
        client=model_client,
        table=load_mapping_table(),
        request_text=request_text,
        load=load,
        now=now,
        route_factory=route_factory,
    )
    run_id = new_run_id()
    Path(settings.checkpoint_db).parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
        graph = build_graph(deps, saver, wrap=_tracing())
        state, nodes = start_run(graph, run_id, company_ref)
    return _result(state, nodes)


def _no_input() -> IngestResult:
    raise RuntimeError("a correction re-run starts after ingest and never loads documents")


def run_corrections(
    run_id: str,
    corrections_path: Path | str | None = None,
    *,
    settings: Settings | None = None,
    client: ModelClient | None = None,
    now: Callable[[], datetime] = _utc_now,
) -> CorrectionsRun:
    """Apply the analyst corrections in a run's workbook (default: its latest version) and
    re-run the checks from the stored checkpoint. Writes the next workbook version only if a
    correction was applied; build the summary with `build_summary(result.state)`."""
    settings = settings or Settings.from_env()
    deps = Deps(
        settings=settings,
        client=client or client_from_env(settings),
        table=load_mapping_table(),
        request_text="",
        load=_no_input,
        now=now,
    )
    with SqliteSaver.from_conn_string(str(settings.checkpoint_db)) as saver:
        graph = build_graph(deps, saver, wrap=_tracing())
        return apply_corrections_to_run(
            graph, run_id, table=deps.table, now=now, corrections_path=corrections_path
        )


def run_agent(text: str) -> Any:
    """The eval runner's entry: a pasted request with the filing in `--- page N ---` blocks."""
    from evals.models import AgentResult, ToolCall

    request, filing = split_request(text)
    if not filing:  # no page markers: the whole text is the filing, as one page
        request, filing = text, text
    output, tools, tokens = run_filings(
        request, lambda: ingest_text(filing), company_ref=DEFAULT_COMPANY_REF
    )
    return AgentResult(
        output=output,
        tool_calls=[ToolCall(name=t) for t in tools],
        usage={"total_tokens": tokens},
    )


def run_library(
    request_text: str,
    paths: Iterable[Path | str],
    library_root: Path | str,
    *,
    company_ref: str,
    settings: Settings | None = None,
) -> tuple[str, list[str], int]:
    """Run one company from Library files; scanned PDFs go through the vision digitizer."""
    root = Path(library_root).resolve()
    listed = list(paths)
    result = ingest_documents(listed, root)
    images = PdfPageImages({d.doc_id: root / p for d, p in _scan_files(result, listed, root)})
    return run_filings(
        request_text,
        lambda: result,
        company_ref=company_ref,
        settings=settings,
        page_images=images,
    )


def _scan_files(
    result: IngestResult, listed: list[Path | str], root: Path
) -> list[tuple[Any, Path | str]]:
    """Pair each scanned document with its file by hash, so a doc id finds its PDF again."""
    import hashlib

    by_hash = {}
    for p in listed:
        path = (root / p).resolve()
        if path.is_file() and path.is_relative_to(root):
            by_hash[hashlib.sha256(path.read_bytes()).hexdigest()] = p
    return [
        (d, by_hash[d.sha256]) for d in result.documents if d.kind == "scan" and d.sha256 in by_hash
    ]


__all__ = ["resume_run", "run_agent", "run_corrections", "run_filings", "run_library"]
