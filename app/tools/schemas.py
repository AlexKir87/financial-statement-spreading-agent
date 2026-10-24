"""Typed outputs the model-facing nodes ask for (the spec "Tools").

No field here carries an amount. Extraction returns the value *as printed* (`value_text`) and
code checks that text appears on the page before it becomes a `RawLine`; mapping returns a line
id, a confidence and a reason. The model has no way to return a number that the code would use.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.state import LineId, Statement

Scale = Literal["units", "thousands", "millions", "billions", "not_stated"]
CURRENCY_NOT_STATED = "XXX"  # ISO 4217 code for "no currency"


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExtractedLine(_Out):
    statement: Statement
    label: str = Field(min_length=1, max_length=200)
    period: str = Field(min_length=1, max_length=40)
    value_text: str = Field(min_length=1, max_length=40)


class PageExtraction(_Out):
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    scale: Scale
    lines: list[ExtractedLine] = Field(max_length=400)


class MappingProposal(_Out):
    key: str = Field(min_length=1, max_length=20)
    standard_line_id: LineId
    confidence: float = Field(ge=0, le=1)
    rationale: str = Field(max_length=300)


class MappingResponse(_Out):
    mappings: list[MappingProposal] = Field(max_length=200)


class ReadCell(_Out):
    row: int = Field(ge=0, le=5000)
    col: int = Field(ge=0, le=60)
    text: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(ge=0, le=1)


class PageRead(_Out):
    cells: list[ReadCell] = Field(max_length=3000)
