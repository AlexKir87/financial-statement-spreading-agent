"""Allow-list masking for log and span fields.

Only fields named in ``ALLOWED_FIELDS`` leave the process, and only if their value is a short
scalar. Page text, line values, labels and anything else is dropped (architecture, section 9).
"""

import math
import re
from collections.abc import Mapping
from decimal import Decimal

MASKED = "[masked]"
# Pseudonymous reference such as "company-a"; a real name fails this and is masked.
COMPANY_REF_PATTERN = re.compile(r"company-[a-z0-9]{1,8}")
# Fixed event names such as "node.end"; free text is not an event name.
EVENT_PATTERN = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")

ALLOWED_FIELDS: frozenset[str] = frozenset(
    {
        "run_id",
        "company_ref",
        "node",
        "status",
        "duration_ms",
        "model_id",
        "prompt_version",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "check_key",
        "period",
        "difference",
        "repair_attempts",
        "exception_code",
        "error_type",
        "workbook_sha256",
        "analyst_id",
    }
)

NUMERIC_FIELDS: frozenset[str] = frozenset(
    {
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "difference",
        "repair_attempts",
    }
)
_IDENT = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
# Per-field patterns for string fields; a value that does not match is masked.
FIELD_PATTERNS: dict[str, re.Pattern[str]] = {
    "company_ref": COMPANY_REF_PATTERN,
    "run_id": re.compile(r"[A-Za-z0-9_-]{1,64}"),
    "node": _IDENT,
    "status": _IDENT,
    "check_key": _IDENT,
    "exception_code": _IDENT,
    "error_type": re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}"),
    "model_id": re.compile(r"[a-z0-9][a-z0-9_./:-]{0,63}"),
    "prompt_version": re.compile(r"[A-Za-z0-9_.-]{1,32}"),
    "period": re.compile(r"[A-Za-z0-9_-]{1,16}"),
    "workbook_sha256": re.compile(r"[0-9a-f]{64}"),
    "analyst_id": re.compile(r"analyst-[a-z0-9]{1,8}"),
}


def mask_value(key: str, value: object) -> object:
    """Return the value if it is safe for this field, else ``MASKED``."""
    if isinstance(value, bool) or value is None:
        return value if key in NUMERIC_FIELDS or key in FIELD_PATTERNS else MASKED
    if isinstance(value, Decimal):
        value = float(value) if value.is_finite() else None
    if isinstance(value, int | float):
        if key not in NUMERIC_FIELDS or (isinstance(value, float) and not math.isfinite(value)):
            return MASKED
        return value
    if isinstance(value, str):
        pattern = FIELD_PATTERNS.get(key)
        if pattern is not None and pattern.fullmatch(value):
            return value
    return MASKED


def mask_fields(fields: Mapping[str, object]) -> dict[str, object]:
    """Drop every field outside the allow-list and mask unsafe values of the rest."""
    return {k: mask_value(k, v) for k, v in fields.items() if k in ALLOWED_FIELDS}


def mask_event(message: str) -> str:
    """Keep a message only if it is a fixed event name; free text may carry page content."""
    return message if EVENT_PATTERN.fullmatch(message) else "masked_message"
