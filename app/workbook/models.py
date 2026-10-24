"""Typed input and output of the workbook writer and verifier."""

from datetime import datetime
from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.state import (
    Amount,
    ChangeEntry,
    CheckResult,
    DigitizedPage,
    ExceptionNote,
    LineId,
    MappedLine,
    OpenIssue,
    RawLine,
    RunStatus,
    ScaleInfo,
)
from app.workbook.templates import LayoutTemplate

RUN_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WorkbookError(ValueError):
    """The input cannot be written without breaking a workbook rule. Names fields, not values."""


class LayoutRow(_Model):
    """One row of a statement sheet. `template` is an id from the whitelist, never a formula."""

    line_id: LineId
    label: str = Field(min_length=1)
    template: LayoutTemplate = "value"
    operands: tuple[LineId, ...] = ()

    @model_validator(mode="after")
    def _operands_fit_template(self) -> Self:
        n = len(self.operands)
        if self.template == "value" and n:
            raise ValueError("a value row has no operands")
        if self.template == "sum" and n < 1:
            raise ValueError("a sum row needs operands")
        if self.template == "difference" and n != 2:
            raise ValueError("a difference row needs exactly two operands")
        return self


class WorkbookData(_Model):
    """Everything the writer needs, taken from the graph state by the caller.

    `layout` is code-owned (it comes from the standard line set, not from the model). Lines in
    `spread` that the layout does not name are appended as plain value rows.
    """

    run_id: str = Field(pattern=RUN_ID_PATTERN)
    company_ref: str = Field(min_length=1)
    status: RunStatus
    scale: ScaleInfo
    tolerance: Decimal = Field(default=Decimal(1), ge=0)
    created: datetime  # injected clock; stamped into the file so the bytes are reproducible
    spread: dict[str, dict[str, Amount]]
    raw_lines: tuple[RawLine, ...] = ()
    mapping: dict[str, MappedLine] = Field(default_factory=dict)
    digitized: tuple[DigitizedPage, ...] = ()
    check_results: tuple[CheckResult, ...] = ()
    exceptions: tuple[ExceptionNote, ...] = ()
    open_issues: tuple[OpenIssue, ...] = ()
    change_log: tuple[ChangeEntry, ...] = ()
    layout: tuple[LayoutRow, ...] = ()

    @model_validator(mode="after")
    def _layout_ids_unique(self) -> Self:
        ids = [lr.line_id for lr in self.layout]
        if len(ids) != len(set(ids)):
            raise ValueError("layout names a line id twice")
        return self

    @model_validator(mode="after")
    def _created_has_zone(self) -> Self:
        if self.created.tzinfo is None:
            raise ValueError("'created' must be timezone-aware")
        return self


class VerificationResult(_Model):
    ok: bool
    formulas_checked: int = Field(ge=0)
    problems: tuple[str, ...] = ()
