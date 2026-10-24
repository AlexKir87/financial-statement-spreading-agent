"""Rebuild `spread` from `raw_lines` and `mapping` (the spec: recomputed, never edited).

Every number comes from `parse_amount` on the text printed in the filing, with the source sign
kept, except the income statement cost lines of a period whose printed subtotals prove costs are
printed as positive numbers (see `_read_cost_signs`); those take the sign the checks expect
(a cost is negative) and the flip is reported. Several raw lines on one standard line add up.
Nothing here can create a figure that is not a printed value or a sum of printed values.
"""

from collections import Counter
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Final

from app.extract.amounts import parse_amount
from app.state import MappedLine, RawLine, mapping_key

SCALE_FACTORS: Final[Mapping[str, Decimal]] = {
    "units": Decimal(1),
    "thousands": Decimal(1_000),
    "millions": Decimal(1_000_000),
    "billions": Decimal(1_000_000_000),
}


class SpreadError(ValueError):
    """The mapped lines cannot form one spread (mixed scale or currency, or an unreadable value)."""


def pick_run_scale(raw_lines: Sequence[RawLine], mapping: Mapping[str, MappedLine]) -> str | None:
    """The scale printed on most mapped lines (a tie goes to the one seen first), or None."""
    counts = Counter(
        ln.scale for ln in raw_lines if mapping_key(ln.period, ln.raw_line_id) in mapping
    )
    return counts.most_common(1)[0][0] if counts else None


def build_spread(
    raw_lines: Sequence[RawLine],
    mapping: Mapping[str, MappedLine],
    *,
    run_scale: str | None = None,
    tolerance: Decimal = Decimal(1),
    notes: list[str] | None = None,
) -> dict[str, dict[str, Decimal]]:
    """With `run_scale` set, a line printed in another known scale is converted to it by the
    exact factor (0.5 millions is 500 thousands); with None, mixed scales are refused. Each
    cost line whose sign was flipped is appended to `notes` as `"<period> <line id>"`."""
    spread: dict[str, dict[str, Decimal]] = {}
    units: set[tuple[str, str]] = set()
    if run_scale is not None and run_scale not in SCALE_FACTORS:
        raise SpreadError("run scale is not a known scale")
    for line in raw_lines:
        mapped = mapping.get(mapping_key(line.period, line.raw_line_id))
        if mapped is None:
            continue
        try:
            value = parse_amount(line.original_value_text)
        except ValueError:
            raise SpreadError(f"line {line.raw_line_id} has no readable printed value") from None
        if run_scale is None:
            units.add((line.scale, line.currency))
        else:
            if line.scale not in SCALE_FACTORS:
                raise SpreadError(f"line {line.raw_line_id} has an unknown scale")
            units.add(("", line.currency))
            value = value * SCALE_FACTORS[line.scale] / SCALE_FACTORS[run_scale]
        cells = spread.setdefault(line.period, {})
        cells[mapped.standard_line_id] = cells.get(mapped.standard_line_id, Decimal(0)) + value
    if len(units) > 1:
        raise SpreadError("mapped lines carry more than one scale or currency")
    for period, cells in spread.items():
        for line_id in _read_cost_signs(cells, tolerance):
            if cells[line_id] == 0:
                continue
            cells[line_id] = -cells[line_id]
            if notes is not None:
                notes.append(f"{period} {line_id}")
    return spread


# A cost line against the printed subtotal it feeds: (subtotal, line it comes from, cost line).
# The subtotal equals `from + cost` when the cost is printed negative and `from - cost` when it
# is printed positive.
_COST_IDENTITIES: Final[tuple[tuple[str, str, str], ...]] = (
    ("is_gross_profit", "is_revenue", "is_cost_of_sales"),
    ("is_operating_profit", "is_gross_profit", "is_operating_expenses"),
    ("is_net_income", "is_profit_before_tax", "is_income_tax"),
)


def _read_cost_signs(cells: Mapping[str, Decimal], tolerance: Decimal) -> list[str]:
    """The cost lines of one period that are printed positive, judged by the printed subtotals.

    A cost line is flipped only when its own identity fits the positive form and not the
    negative one. A cost line with no subtotal to test follows the period when every cost line
    that could be tested says positive; with no evidence at all, or mixed evidence, it stays as
    printed. Nothing is guessed from the size or the label of a figure."""
    verdicts: dict[str, bool | None] = {}
    for subtotal, base, cost in _COST_IDENTITIES:
        if cost not in cells:
            continue
        if subtotal not in cells or base not in cells or cells[cost] == 0:
            verdicts[cost] = None
            continue
        as_negative = abs(cells[base] + cells[cost] - cells[subtotal]) <= tolerance
        as_positive = abs(cells[base] - cells[cost] - cells[subtotal]) <= tolerance
        verdicts[cost] = as_positive if as_positive != as_negative else None
    tested = [v for v in verdicts.values() if v is not None]
    period_positive = bool(tested) and all(tested)
    flipped = [
        cost
        for cost, verdict in verdicts.items()
        if verdict or (verdict is None and period_positive)
    ]
    return flipped + _read_below_the_line_signs(cells, tolerance)


def _read_below_the_line_signs(cells: Mapping[str, Decimal], tolerance: Decimal) -> list[str]:
    """Interest expense and non-operating items printed as expenses, judged by profit before tax.

    Profit before tax is operating profit, less interest expense, plus non-operating items. Each of
    the two lines is read as printed or sign-flipped; a line is flipped only when every reading that
    fits the printed subtotal agrees on it (plan step 27). No fitting reading, or none that
    settles the line, leaves it as printed."""
    base, subtotal, interest = "is_operating_profit", "is_profit_before_tax", "is_interest_expense"
    if any(line_id not in cells for line_id in (base, subtotal, interest)):
        return []
    other = cells.get("is_non_operating_items", Decimal(0))
    fits = [
        (si, so)
        for si in (1, -1)
        for so in ((1, -1) if other else (1,))
        if abs(cells[base] + si * cells[interest] + so * other - cells[subtotal]) <= tolerance
    ]
    if not fits:
        return []
    out = []
    if cells[interest] != 0 and all(si == -1 for si, _ in fits):
        out.append(interest)
    if other and all(so == -1 for _, so in fits):
        out.append("is_non_operating_items")
    return out
