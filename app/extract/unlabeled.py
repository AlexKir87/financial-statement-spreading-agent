"""Balance sheet rows that print a number with no label (plan step 28).

Some filings print the subtotals and totals of the balance sheet as bare numbers under the rows
they add up (`3,945,587` closing the current assets). The model has no label to copy, so it
leaves them out and `bs_balances` has nothing to compare. Code names such a row only when the
sums prove what it is: a number-only row is a subtotal when it equals the rows since the previous
number-only row in the same printed section, the section total when it equals every labelled row
in that section, and total liabilities and equity when it equals total liabilities plus total
equity. The sums must hold in every period column; a row that fails any of them stays unread and
is reported. Pure code, no model, exact `Decimal` arithmetic, nothing guessed.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from app.extract.amounts import normalise_amount_text, parse_amount
from app.ingest.loader import BLANK_CELL
from app.sections import row_sections
from app.state import RawLine

Vector = tuple[Decimal, ...]

_SECTIONS: Final = ("assets", "liabilities", "equity")
_SECTION_TOTALS: Final = {
    "assets": ("Total current assets", "Total non-current assets", "Total assets"),
    "liabilities": (
        "Total current liabilities",
        "Total non-current liabilities",
        "Total liabilities",
    ),
}
_EQUITY_TOTAL: Final = "Total equity"
_LIABILITIES_AND_EQUITY: Final = "Total liabilities and equity"
_PRINTED_TOTAL: Final = re.compile(r"^total\b", re.IGNORECASE)


@dataclass(frozen=True)
class DataRow:
    """A row that ends in one printed number per period column."""

    index: int
    section: str | None
    label: str  # empty for a number-only row
    tokens: tuple[str, ...]  # the printed numbers, print-layout noise removed
    values: Vector


@dataclass(frozen=True)
class InferredRow:
    row: DataRow
    label: str


@dataclass(frozen=True)
class Unresolved:
    row: DataRow
    reason: str


def _is_amount(token: str) -> bool:
    try:
        parse_amount(token)
    except ValueError:
        return False
    return True


def parse_rows(text: str, columns: int) -> list[DataRow]:
    """The rows of a balance sheet page that end in `columns` printed numbers."""
    rows = text.splitlines()
    sections = row_sections("BS", rows)
    out: list[DataRow] = []
    for index, row in enumerate(rows):
        words = normalise_amount_text(" ".join(row.split())).split(" ")
        run = 0
        while run < len(words) and _is_amount(words[-1 - run]):
            run += 1
        if run < columns or not any(ch.isdigit() for ch in "".join(words[len(words) - run :])):
            continue
        tokens = tuple(words[len(words) - columns :])
        label = " ".join(words[: len(words) - columns])
        if label and not any(ch.isalpha() for ch in label):
            continue  # more numbers than columns: not a row this module can read
        out.append(
            DataRow(
                index=index,
                section=sections[index],
                label=label,
                tokens=tokens,
                values=tuple(parse_amount(t) for t in tokens),
            )
        )
    return out


def column_of(rows: Sequence[DataRow], label: str, value_text: str) -> int | None:
    """The period column a model line was read from: the one position at which the labelled
    row printing this value holds it. `None` when the row or the position is not unique."""
    want = " ".join(label.lower().split())
    try:
        value = parse_amount(value_text)
    except ValueError:
        return None
    found = {
        pos
        for r in rows
        if r.label and want and want in " ".join(r.label.lower().split())
        for pos, v in enumerate(r.values)
        if v == value
    }
    return found.pop() if len(found) == 1 else None


def _add(a: Vector, b: Vector) -> Vector:
    return tuple(x + y for x, y in zip(a, b, strict=True))


def _sum(rows: Sequence[DataRow], columns: int) -> Vector:
    total: Vector = (Decimal(0),) * columns
    for r in rows:
        total = _add(total, r.values)
    return total


def infer_totals(
    rows: Sequence[DataRow], columns: int
) -> tuple[list[InferredRow], list[Unresolved]]:
    """Name each number-only row the sums prove; report the others."""
    named: list[InferredRow] = []
    unresolved: list[Unresolved] = []
    totals: dict[str, Vector] = {}
    for section in _SECTIONS:
        inside = [r for r in rows if r.section == section]
        bare = [r for r in inside if not r.label]
        if not bare:
            continue
        if any(_PRINTED_TOTAL.match(r.label) for r in inside):
            unresolved += [Unresolved(r, "the section also prints a labelled total") for r in bare]
            continue
        details = [r for r in inside if r.label]
        if section == "equity":
            _equity(inside, totals, columns, named, unresolved)
            continue
        names = _SECTION_TOTALS[section]
        if len(bare) > len(names):
            unresolved += [Unresolved(r, "more number-only rows than subtotals") for r in bare]
            continue
        # one name per row: subtotals first, the section total last
        labels = names[:-1][: len(bare) - 1] + (names[-1],)
        block: list[DataRow] = []
        position = 0
        for r in inside:
            if r.label:
                block.append(r)
                continue
            last = position == len(bare) - 1
            expected = _sum(details if last else block, columns)
            if r.values == expected and (last or len(block) >= 2):
                named.append(InferredRow(r, labels[position]))
                if last:
                    totals[section] = r.values
            else:
                unresolved.append(Unresolved(r, "does not equal the sum of the rows above it"))
            block = []
            position += 1
        if any(r.label for r in inside[inside.index(bare[-1]) + 1 :]):
            unresolved.append(Unresolved(bare[-1], "labelled rows follow the last number-only row"))
            named[:] = [n for n in named if n.row is not bare[-1]]
            totals.pop(section, None)
    return named, unresolved


def _equity(
    inside: Sequence[DataRow],
    totals: dict[str, Vector],
    columns: int,
    named: list[InferredRow],
    unresolved: list[Unresolved],
) -> None:
    equity_total: Vector | None = None
    seen: list[DataRow] = []
    for r in inside:
        if r.label:
            seen.append(r)
            continue
        if any(x.label and x.index > r.index for x in inside) and equity_total is None:
            # equity rows follow (non-controlling interests): this is not the total
            unresolved.append(Unresolved(r, "labelled rows follow it"))
        elif equity_total is None and r.values == _sum(seen, columns) and len(seen) >= 2:
            equity_total = r.values
            named.append(InferredRow(r, _EQUITY_TOTAL))
        elif (
            equity_total is not None
            and "liabilities" in totals
            and r.values == _add(totals["liabilities"], equity_total)
        ):
            named.append(InferredRow(r, _LIABILITIES_AND_EQUITY))
        else:
            unresolved.append(Unresolved(r, "does not equal the sum of the rows above it"))


def values_in_blank_columns(
    text: str, lines: Sequence[RawLine]
) -> tuple[list[RawLine], list[RawLine]]:
    """Split `lines` into those kept and those read into a column their row prints empty (step 30).

    A row such as `Proceeds from dispositions [blank] 35` has its number in the second period
    column. The period column of every period heading is learnt from the rows that print all their
    numbers; a line whose period is not the column its value sits in is dropped. A period whose
    column cannot be learnt, or a value printed twice in the row, is left alone."""
    kept: list[RawLine] = []
    dropped: list[RawLine] = []
    for statement in dict.fromkeys(ln.statement for ln in lines):
        mine = [ln for ln in lines if ln.statement == statement]
        periods = {ln.period_label for ln in mine if ln.period_label}
        rows = parse_rows(text, len(periods)) if periods else []
        column: dict[str, set[int]] = {}
        for ln in mine:
            at = column_of(rows, ln.original_label, ln.original_value_text)
            if at is not None and ln.period_label:
                column.setdefault(ln.period_label, set()).add(at)
        learnt = {p: next(iter(c)) for p, c in column.items() if len(c) == 1}
        if len(set(learnt.values())) != len(learnt):
            learnt = {}
        blank_rows = _blank_rows(text, len(periods))
        for ln in mine:
            if _misplaced(ln, blank_rows, learnt) and not _supported(ln, rows, learnt):
                dropped.append(ln)
            else:
                kept.append(ln)
    order = {id(ln): i for i, ln in enumerate(lines)}
    kept.sort(key=lambda ln: order[id(ln)])
    return kept, dropped


def _blank_rows(text: str, columns: int) -> list[tuple[str, list[Decimal | None]]]:
    """(label, cells) of each row that ends in `columns` cells, some of them `[blank]`."""
    out: list[tuple[str, list[Decimal | None]]] = []
    for row in text.splitlines():
        words = normalise_amount_text(" ".join(row.split())).split(" ")
        run = 0
        while run < len(words) and (words[-1 - run] == BLANK_CELL or _is_amount(words[-1 - run])):
            run += 1
        cells = words[len(words) - columns :] if run >= columns else []
        if columns and BLANK_CELL in cells:
            out.append(
                (
                    " ".join(words[: len(words) - columns]),
                    [None if c == BLANK_CELL else parse_amount(c) for c in cells],
                )
            )
    return out


def _misplaced(
    line: RawLine, blank_rows: Sequence[tuple[str, list[Decimal | None]]], learnt: dict[str, int]
) -> bool:
    if line.period_label not in learnt:
        return False
    want = " ".join(line.original_label.lower().split())
    try:
        value = parse_amount(line.original_value_text)
    except ValueError:
        return False
    for label, cells in blank_rows:
        if want and want in " ".join(label.lower().split()):
            where = [i for i, c in enumerate(cells) if c == value]
            if len(where) == 1 and where[0] != learnt[line.period_label]:
                return True
    return False


def _supported(line: RawLine, rows: Sequence[DataRow], learnt: dict[str, int]) -> bool:
    """A row that prints every number puts this line's value in this line's own column, so a
    blank row elsewhere with the same label and value does not make it misplaced."""
    want = " ".join(line.original_label.lower().split())
    try:
        value = parse_amount(line.original_value_text)
    except ValueError:
        return False
    at = learnt[line.period_label] if line.period_label in learnt else None
    return at is not None and any(
        want in " ".join(r.label.lower().split()) and r.values[at] == value for r in rows if r.label
    )
