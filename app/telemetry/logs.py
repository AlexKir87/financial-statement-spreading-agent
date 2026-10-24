"""Structured JSON logs through ``logging``, masked by an allow-list filter."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import IO

from app.telemetry.masking import mask_event, mask_fields

Clock = Callable[[], datetime]
FIELDS_ATTR = "fields"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class MaskingFilter(logging.Filter):
    """Rewrites each record so only the event name and allow-listed fields remain."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = ""
        record.msg = mask_event(message)
        record.args = None
        raw = getattr(record, FIELDS_ATTR, None)
        setattr(record, FIELDS_ATTR, mask_fields(raw) if isinstance(raw, dict) else {})
        if record.exc_info and record.exc_info[0] is not None:
            # Exception messages can quote document text: keep the type name only.
            setattr(
                record,
                FIELDS_ATTR,
                {**getattr(record, FIELDS_ATTR), "error_type": record.exc_info[0].__name__},
            )
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, clock: Clock = _utc_now) -> None:
        super().__init__()
        self._clock = clock

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self._clock().isoformat(),
            "level": record.levelname,
            "event": mask_event(record.getMessage()),
            **mask_fields(getattr(record, FIELDS_ATTR, {})),
        }
        return json.dumps(payload, sort_keys=True, default=str, allow_nan=False)


def get_logger(
    name: str = "spread", *, stream: IO[str], clock: Clock = _utc_now, level: int = logging.INFO
) -> logging.Logger:
    """Logger that writes masked JSON lines to ``stream``; replaces earlier handlers."""
    logger = logging.getLogger(name)
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter(clock))
    handler.addFilter(MaskingFilter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def log_event(logger: logging.Logger, event: str, **fields: object) -> None:
    logger.info(event, extra={FIELDS_ATTR: fields})
