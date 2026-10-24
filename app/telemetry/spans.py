"""Node wrapper: one OpenTelemetry span and two log lines per node call.

Spans carry the run id, company pseudonym and node name only. LangGraph has no documented OTel
route in the wiki, so emission is hand-written around each node ([[langsmith-observability]]).
"""

import logging
from collections.abc import Callable, Mapping
from functools import wraps
from typing import Any

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter
from opentelemetry.trace import Status, StatusCode, Tracer

from app.telemetry.logs import Clock, _utc_now, log_event
from app.telemetry.masking import mask_fields

NodeFn = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def build_tracer_provider(exporter: SpanExporter | None = None) -> TracerProvider:
    """Provider that exports to our own collector (pass an OTLP exporter); none means no export."""
    provider = TracerProvider()
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider


def traced_node(
    name: str,
    fn: NodeFn,
    *,
    tracer: Tracer,
    logger: logging.Logger,
    clock: Clock = _utc_now,
) -> NodeFn:
    """Wrap a graph node so it emits a masked span and start/end/error log lines."""

    @wraps(fn)
    def wrapper(state: Mapping[str, Any]) -> Mapping[str, Any]:
        ids = mask_fields({"run_id": state.get("run_id"), "company_ref": state.get("company_ref")})
        started = clock()
        with tracer.start_as_current_span(
            name, record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attributes({"node": name, **{k: str(v) for k, v in ids.items()}})
            log_event(logger, "node.start", node=name, **ids)
            try:
                result = fn(state)
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR))
                log_event(logger, "node.error", node=name, error_type=type(exc).__name__, **ids)
                raise
            elapsed_ms = int((clock() - started).total_seconds() * 1000)
            log_event(logger, "node.end", node=name, status="ok", duration_ms=elapsed_ms, **ids)
            return result

    return wrapper
