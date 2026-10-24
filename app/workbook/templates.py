"""The fixed whitelist of formula templates (the spec "Rules that bind the tools").

A formula is only ever built here, from cell references and a template id. No caller can hand
over a formula string: `CellRef` fields are validated and the template ids are a closed set
([[excel-output-with-live-formulas]]).
"""

import re
from typing import Final, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field
from xlsxwriter.utility import xl_rowcol_to_cell

SheetName = Literal[
    "Summary",
    "IS",
    "BS",
    "CF",
    "Source_Trace",
    "Reconciliations",
    "Open_Issues",
    "Change_Log",
    "Mapping",
]
SHEET_ORDER: Final[tuple[SheetName, ...]] = get_args(SheetName)

TemplateId = Literal["sum", "difference", "ok_check", "count_status"]
LayoutTemplate = Literal["value", "sum", "difference"]
StatusLabel = Literal["OK", "CHECK", "N/A"]

_QUOTED_SHEET: Final = re.compile(r"^[A-Za-z_]+$")


class CellRef(BaseModel):
    """A zero-based cell on one of the known sheets."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    sheet: SheetName
    row: int = Field(ge=0, le=1_048_575)
    col: int = Field(ge=0, le=16_383)
    abs_: bool = False

    def a1(self, *, from_sheet: SheetName | None = None) -> str:
        cell: str = xl_rowcol_to_cell(self.row, self.col, self.abs_, self.abs_)
        if from_sheet == self.sheet:
            return cell
        assert _QUOTED_SHEET.match(self.sheet)  # noqa: S101 - closed Literal, belt and braces
        return f"{self.sheet}!{cell}"


def render(
    template: TemplateId,
    refs: tuple[CellRef, ...],
    *,
    on: SheetName,
    label: StatusLabel | None = None,
) -> str:
    """Return the formula text for `template`. `on` is the sheet the formula is written on."""
    cells = [r.a1(from_sheet=on) for r in refs]
    if template == "sum":
        if not cells:
            raise ValueError("sum needs at least one cell")
        return "=SUM(" + ",".join(cells) + ")"
    if template == "difference":
        if len(cells) != 2:
            raise ValueError("difference needs exactly two cells")
        return f"={cells[0]}-{cells[1]}"
    if template == "ok_check":
        if len(cells) != 2:
            raise ValueError("ok_check needs the difference cell and the tolerance cell")
        return f'=IF(ABS({cells[0]})<={cells[1]},"OK","CHECK")'
    if template == "count_status":
        if len(cells) != 2 or label is None:
            raise ValueError("count_status needs the first and last status cell and a label")
        return f'=COUNTIF({cells[0]}:{cells[1]},"{label}")'
    raise ValueError(f"unknown template: {template!r}")
