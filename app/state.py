"""Typed state, schemas and reducers for one spreading run (the spec "State schema").

Amounts are `Decimal` and a float is rejected at validation. Models are frozen: a node
returns new objects, it never edits one in place. The graph state is a `TypedDict` whose
append channels carry explicit reducers ([[pattern-typed-state-reducers]]).
"""

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Final, Literal, TypedDict, TypeVar

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    computed_field,
    model_validator,
)

T = TypeVar("T")

CheckKey = Literal[
    "bs_balances",
    "cf_cash_reconciles",
    "re_rollforward",
    "ni_pl_equals_cf",
    "subtotals_match_source",
    "lines_complete",
]
# Canonical order of the six checks; exceptions and the workbook list them in this order.
CHECK_ORDER: Final[tuple[CheckKey, ...]] = (
    "bs_balances",
    "cf_cash_reconciles",
    "re_rollforward",
    "ni_pl_equals_cf",
    "subtotals_match_source",
    "lines_complete",
)
CheckStatus = Literal["pass", "fail", "not_applicable"]
RunStatus = Literal["delivered", "delivered_with_exceptions", "not_delivered"]
NotDeliveredReason = Literal["out_of_scope", "unsupported_language", "unreadable_source"]
DocKind = Literal["xbrl", "text_pdf", "scan", "xlsx"]
Statement = Literal[
    "IS", "BS", "CF", "SE"
]  # SE: retained earnings column of the equity statement (step 26)
MappingOrigin = Literal["model", "rule", "analyst"]

LINE_ID_PATTERN: Final = r"^(is|bs|cf)_[a-z0-9]+(_[a-z0-9]+)*$"
SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"


def _to_amount(value: object) -> Decimal:
    """Accept `Decimal`, `int` or a numeric string. A float (or bool) is never an amount."""
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError("amounts must be Decimal, int or str, never float")
    if isinstance(value, Decimal):
        out = value
    elif isinstance(value, int | str):
        try:
            out = Decimal(value)
        except ArithmeticError as exc:
            raise ValueError(f"not a decimal amount: {value!r}") from exc
    else:
        raise ValueError(f"amounts must be Decimal, int or str, got {type(value).__name__}")
    if not out.is_finite():
        raise ValueError("amount must be finite")
    return out


def _amount_to_json(value: Decimal) -> int | str:
    """JSON number for whole amounts (as in the spec's RunSummary), exact string otherwise."""
    if value == value.to_integral_value():
        return int(value)
    return format(value, "f")


Amount = Annotated[
    Decimal,
    BeforeValidator(_to_amount),
    PlainSerializer(_amount_to_json, return_type=int | str, when_used="json"),
]


def _check_line_id(value: str) -> str:
    if not re.fullmatch(LINE_ID_PATTERN, value):
        raise ValueError(f"line id must match {LINE_ID_PATTERN}: {value!r}")
    return value


LineId = Annotated[str, AfterValidator(_check_line_id)]


INFERRED_LINE_ID: Final = re.compile(r":u\d+c\d+$")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourceDoc(_Model):
    doc_id: str = Field(min_length=1)
    kind: DocKind
    sha256: str = Field(pattern=SHA256_PATTERN)
    language: str = Field(min_length=2)
    page_count: int = Field(ge=1)


class BBox(_Model):
    x0: float
    y0: float
    x1: float
    y1: float


class Cell(_Model):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    text: str
    bbox: BBox | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)


class DigitizedPage(_Model):
    doc_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    text: str | None = None
    cells: tuple[Cell, ...] = ()
    digitizer_name: str = Field(min_length=1)
    digitizer_version: str = Field(min_length=1)


class RawLine(_Model):
    raw_line_id: str = Field(min_length=1)
    doc_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    original_label: str
    original_value_text: str
    statement: Statement
    period: str = Field(min_length=1)  # canonical key (`app/periods.py`) or the printed label
    period_label: str | None = None  # the column heading as the model read it
    section: str | None = None  # printed section heading above the row (`app/sections.py`)
    scale: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)

    @property
    def label_inferred(self) -> bool:
        """The page prints this number with no label and code named the row from the sums of the
        rows above it (`app/extract/unlabeled.py`, plan step 28). Such a line's id ends in
        `:u<row>c<col>` and `original_label` is that name."""
        return INFERRED_LINE_ID.search(self.raw_line_id) is not None

    @property
    def shown_label(self) -> str:
        """The label for a reader: an inferred one says so."""
        suffix = " (no label printed)" if self.label_inferred else ""
        return self.original_label + suffix


class MappedLine(_Model):
    raw_line_id: str = Field(min_length=1)
    period: str = Field(min_length=1)
    standard_line_id: LineId
    confidence: float = Field(ge=0, le=1)
    rationale: str
    origin: MappingOrigin


class Term(_Model):
    """One cell involved in a check, so an exception can cite what was compared."""

    line_id: str = Field(min_length=1)
    period: str = Field(min_length=1)
    value: Amount


class CheckResult(_Model):
    key: CheckKey
    period: str = Field(min_length=1)
    status: CheckStatus
    expected: Amount | None = None
    actual: Amount | None = None
    difference: Amount | None = None
    terms: tuple[Term, ...] = ()

    @model_validator(mode="after")
    def _figures_match_status(self) -> "CheckResult":
        figures = (self.expected, self.actual, self.difference)
        if self.status == "not_applicable" and any(f is not None for f in figures):
            raise ValueError("a not_applicable check carries no figures")
        if self.status != "not_applicable" and any(f is None for f in figures):
            raise ValueError("a pass or fail check needs expected, actual and difference")
        return self


class ExceptionNote(_Model):
    check: CheckKey
    period: str = Field(min_length=1)
    difference: Amount
    explanation: str
    attempts_made: int = Field(ge=0)


class OpenIssue(_Model):
    code: str = Field(min_length=1)
    where: str
    detail: str


class Usage(_Model):
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class ChangeEntry(_Model):
    who: str = Field(min_length=1)  # "system" or an analyst id
    what: str
    when: datetime
    why: str

    @model_validator(mode="after")
    def _when_has_zone(self) -> "ChangeEntry":
        if self.when.tzinfo is None:
            raise ValueError("'when' must be timezone-aware")
        return self


class Correction(_Model):
    correction_id: str = Field(min_length=1)
    period: str = Field(min_length=1)
    raw_line_id: str = Field(min_length=1)
    standard_line_id: LineId
    who: str = Field(min_length=1)
    why: str


class WorkbookVersion(_Model):
    path: str = Field(min_length=1)
    version: int = Field(ge=1)
    sha256: str = Field(pattern=SHA256_PATTERN)
    verified: bool | None = None  # None until `verify_workbook` has run


# --- Reducers -------------------------------------------------------------------------


def append(existing: Sequence[T] | None, incoming: Sequence[T] | None) -> list[T]:
    """Append channel: new items go after the existing ones, nothing is replaced."""
    return [*(existing or ()), *(incoming or ())]


def mapping_key(period: str, raw_line_id: str) -> str:
    return f"{period}|{raw_line_id}"


def merge_mapping(
    existing: Mapping[str, MappedLine] | None, incoming: Mapping[str, MappedLine] | None
) -> dict[str, MappedLine]:
    """Merge by key (`period` + raw line id); a later entry for the same key wins."""
    return {**(existing or {}), **(incoming or {})}


def sum_usage(existing: Usage | None, incoming: Usage | None) -> Usage:
    a, b = existing or Usage(), incoming or Usage()
    return Usage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
    )


def count_attempts(existing: int | None, incoming: int | None) -> int:
    """Repair counter: add the increment; `None` resets it for the next check set."""
    if incoming is None:
        return 0
    return (existing or 0) + incoming


class SpreadState(TypedDict):
    run_id: str
    company_ref: str
    documents: Annotated[list[SourceDoc], append]
    digitized: Annotated[list[DigitizedPage], append]
    raw_lines: Annotated[list[RawLine], append]
    mapping: Annotated[dict[str, MappedLine], merge_mapping]
    # Recomputed from `mapping` + `raw_lines` by one node; never edited directly.
    spread: dict[str, dict[str, Decimal]]
    check_results: list[CheckResult]  # overwritten on each reconciliation pass
    exceptions: Annotated[list[ExceptionNote], append]
    open_issues: Annotated[list[OpenIssue], append]
    repair_attempts: Annotated[int, count_attempts]
    token_usage: Annotated[Usage, sum_usage]
    change_log: Annotated[list[ChangeEntry], append]
    corrections: Annotated[list[Correction], append]
    workbook_versions: Annotated[list[WorkbookVersion], append]
    status: RunStatus | None
    # Set by triage, digitize or the spread step when the run ends without a workbook.
    not_delivered_reason: NotDeliveredReason | None


def initial_state(run_id: str, company_ref: str) -> SpreadState:
    return SpreadState(
        run_id=run_id,
        company_ref=company_ref,
        documents=[],
        digitized=[],
        raw_lines=[],
        mapping={},
        spread={},
        check_results=[],
        exceptions=[],
        open_issues=[],
        repair_attempts=0,
        token_usage=Usage(),
        change_log=[],
        corrections=[],
        workbook_versions=[],
        status=None,
        not_delivered_reason=None,
    )


# --- Output schema (the spec "Output schemas") ------------------------------------------


class WorkbookRef(_Model):
    path: str = Field(min_length=1)
    version: int = Field(ge=1)
    verified: bool


class ScaleInfo(_Model):
    unit: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)


class CheckSummary(_Model):
    status: CheckStatus
    max_abs_diff: Amount | None = None


class TraceEntry(_Model):
    doc_id: str = Field(min_length=1)
    page: int = Field(ge=1)
    original_label: str


class TokenTotal(_Model):
    total_tokens: int = Field(ge=0)


class RunSummary(_Model):
    status: RunStatus
    not_delivered_reason: NotDeliveredReason | None = None
    company_ref: str = Field(min_length=1)
    workbook: WorkbookRef | None = None
    scale: ScaleInfo | None = None
    checks: dict[CheckKey, CheckSummary] = Field(default_factory=dict)
    # A balancing figure can never exist: the field has one legal value.
    plug_figures: Literal[0] = 0
    repair_attempts: int = Field(default=0, ge=0)
    spread: dict[str, dict[LineId, Amount]] = Field(default_factory=dict)
    trace: dict[str, dict[LineId, TraceEntry]] = Field(default_factory=dict)
    exceptions: tuple[ExceptionNote, ...] = ()
    open_issues: tuple[OpenIssue, ...] = ()
    usage: TokenTotal = TokenTotal(total_tokens=0)

    @model_validator(mode="after")
    def _status_is_consistent(self) -> "RunSummary":
        if self.status == "not_delivered":
            if self.not_delivered_reason is None:
                raise ValueError("not_delivered always carries a reason")
            if self.workbook is not None:
                raise ValueError("not_delivered means no workbook")
        else:
            if self.not_delivered_reason is not None:
                raise ValueError("a reason is only allowed when not_delivered")
            if self.workbook is None:
                raise ValueError("a delivered run has a workbook")
            if set(self.checks) != set(CHECK_ORDER):
                raise ValueError("a delivered run reports all six checks")
        if self.status == "delivered":
            if self.exceptions or any(c.status == "fail" for c in self.checks.values()):
                raise ValueError("delivered means no failed check and no exception")
        return self
