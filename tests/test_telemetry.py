import io
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.telemetry import build_tracer_provider, get_logger, log_event, mask_fields, traced_node
from app.telemetry.masking import ALLOWED_FIELDS, MASKED

PAGE_TEXT = "Revenue 1,300 Acme Holdings plc consolidated statement of financial position"


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, tzinfo=UTC)

    def __call__(self) -> datetime:
        current = self.now
        self.now += timedelta(milliseconds=250)
        return current


def _logger(buf: io.StringIO, clock: FakeClock) -> logging.Logger:
    return get_logger("spread-test", stream=buf, clock=clock)


def _lines(buf: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in buf.getvalue().splitlines()]


def test_allowed_fields_pass():
    fields = {"run_id": "run-1", "node": "map_lines", "total_tokens": 120, "difference": 50}
    assert mask_fields(fields) == fields


def test_fields_outside_allow_list_are_dropped():
    out = mask_fields({"page_text": PAGE_TEXT, "line_value": 1300, "label": "Revenue", "node": "x"})
    assert out == {"node": "x"}


def test_page_text_in_allowed_field_is_masked():
    assert mask_fields({"node": PAGE_TEXT})["node"] == MASKED
    assert mask_fields({"status": "x" * 500})["status"] == MASKED


def test_real_company_name_is_masked_but_pseudonym_passes():
    assert mask_fields({"company_ref": "Acme Holdings"})["company_ref"] == MASKED
    assert mask_fields({"company_ref": "company-a"})["company_ref"] == "company-a"


def test_log_call_with_page_text_and_value_is_masked():
    buf, clock = io.StringIO(), FakeClock()
    log = _logger(buf, clock)
    log_event(log, "extract.done", node="extract", page_text=PAGE_TEXT, line_value=1300)
    log.info("could not read: %s", PAGE_TEXT, extra={"fields": {"api_key": "sk-secret"}})
    out = buf.getvalue()
    assert PAGE_TEXT not in out and "1300" not in out and "sk-secret" not in out
    first, second = _lines(buf)
    assert first["event"] == "extract.done" and first["node"] == "extract"
    assert "page_text" not in first and "line_value" not in first
    assert second["event"] == "masked_message"


def test_exception_message_is_not_logged_only_its_type():
    buf, clock = io.StringIO(), FakeClock()
    log = _logger(buf, clock)
    try:
        raise ValueError(PAGE_TEXT)
    except ValueError:
        log.exception("failed", extra={"fields": {"node": "extract"}})
    assert PAGE_TEXT not in buf.getvalue()
    assert _lines(buf)[0]["error_type"] == "ValueError"


def test_log_timestamp_comes_from_injected_clock():
    buf, clock = io.StringIO(), FakeClock()
    log_event(_logger(buf, clock), "run.start", run_id="run-1")
    assert _lines(buf)[0]["ts"] == "2026-01-01T00:00:00+00:00"


def test_span_carries_run_id_and_pseudonym_only():
    exporter = InMemorySpanExporter()
    tracer = build_tracer_provider(exporter).get_tracer("test")
    buf, clock = io.StringIO(), FakeClock()
    state = {
        "run_id": "run-1",
        "company_ref": "company-a",
        "page_text": PAGE_TEXT,
        "company_name": "Acme Holdings",
    }

    def node(s):
        return {"x": 1}

    node_clock = FakeClock()  # separate from the log clock so its ticks are not shared
    wrapped = traced_node(
        "map_lines", node, tracer=tracer, logger=_logger(buf, clock), clock=node_clock
    )
    assert wrapped(state) == {"x": 1}
    (span,) = exporter.get_finished_spans()
    assert span.name == "map_lines"
    assert dict(span.attributes or {}) == {
        "node": "map_lines",
        "run_id": "run-1",
        "company_ref": "company-a",
    }
    assert PAGE_TEXT not in buf.getvalue() and "Acme" not in buf.getvalue()
    start, end = _lines(buf)
    assert (start["event"], end["event"]) == ("node.start", "node.end")
    assert end["duration_ms"] == 250


def test_span_pseudonym_check_masks_a_real_name():
    exporter = InMemorySpanExporter()
    tracer = build_tracer_provider(exporter).get_tracer("test")
    buf, clock = io.StringIO(), FakeClock()
    wrapped = traced_node("n", lambda s: {}, tracer=tracer, logger=_logger(buf, clock), clock=clock)
    wrapped({"run_id": "run-1", "company_ref": "Acme Holdings"})
    (span,) = exporter.get_finished_spans()
    assert (span.attributes or {})["company_ref"] == MASKED
    assert "Acme" not in buf.getvalue()


def test_node_error_is_recorded_without_message_and_reraised():
    exporter = InMemorySpanExporter()
    tracer = build_tracer_provider(exporter).get_tracer("test")
    buf, clock = io.StringIO(), FakeClock()

    def boom(s):
        raise RuntimeError(PAGE_TEXT)

    wrapped = traced_node("n", boom, tracer=tracer, logger=_logger(buf, clock), clock=clock)
    with pytest.raises(RuntimeError):
        wrapped({"run_id": "run-1", "company_ref": "company-a"})
    assert PAGE_TEXT not in buf.getvalue()
    assert _lines(buf)[-1]["error_type"] == "RuntimeError"
    span = exporter.get_finished_spans()[0]
    assert span.status.status_code.name == "ERROR"
    assert span.events == ()
    assert PAGE_TEXT not in str(span.status.description)


def test_allow_list_has_no_text_bearing_names():
    banned = {"text", "page_text", "value", "line_value", "label", "company_name", "prompt"}
    assert not (ALLOWED_FIELDS & banned)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("node", "Acme"),
        ("status", "Revenue"),
        ("analyst_id", "a@b.com"),
        ("run_id", 1300),
        ("difference", float("nan")),
        ("total_tokens", "1300"),
    ],
)
def test_single_token_names_emails_and_wrong_types_are_masked(field, value):
    assert mask_fields({field: value})[field] == MASKED


def test_bad_format_call_does_not_raise_and_does_not_leak():
    buf, clock = io.StringIO(), FakeClock()
    log = _logger(buf, clock)
    log.info("a %s %s", PAGE_TEXT)
    assert PAGE_TEXT not in buf.getvalue()
    assert _lines(buf)[0]["event"] == "masked_message"


def test_formatter_masks_even_without_the_filter():
    from app.telemetry.logs import FIELDS_ATTR, JsonFormatter

    record = logging.LogRecord("x", logging.INFO, "f", 1, PAGE_TEXT, None, None)
    setattr(record, FIELDS_ATTR, {"page_text": PAGE_TEXT, "node": PAGE_TEXT})
    out = JsonFormatter(FakeClock()).format(record)
    assert PAGE_TEXT not in out
