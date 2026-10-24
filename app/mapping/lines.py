"""`map_lines`: rule table first, then the model constrained to the standard line ids.

The model sees labels only (never amounts) and must answer with ids from the allowed list for
the statement; anything else is rejected, retried once, then recorded as an open issue and the
line stays unmapped. A rule hit never calls the model. Each mapping records its `origin`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from app.extract.amounts import parse_amount
from app.mapping.table import EQUITY_COLUMN_LINE, MappingTable, normalize_label
from app.prompts import load_prompt
from app.state import MappedLine, OpenIssue, RawLine, Usage, mapping_key
from app.tools.model_client import InvalidModelOutput, StructuredCaller, TokenBudgetExceeded
from app.tools.schemas import MappingResponse

# Placeholder, not measured: below this a model mapping is flagged for the analyst. Tune on the
# gold set (step 12), never on the pilot companies.
LOW_CONFIDENCE = 0.7
BATCH_SIZE = 40
_EQUITY = "bs_total_equity"


@dataclass(frozen=True)
class MappingResult:
    mapping: dict[str, MappedLine]
    open_issues: tuple[OpenIssue, ...]
    usage: Usage


def map_lines(
    raw_lines: Sequence[RawLine],
    table: MappingTable,
    caller: StructuredCaller,
    *,
    low_confidence: float = LOW_CONFIDENCE,
    batch_size: int = BATCH_SIZE,
) -> MappingResult:
    mapping: dict[str, MappedLine] = {}
    issues: list[OpenIssue] = []
    usage_before = caller.usage
    # (statement, label, section) -> lines to map
    pending: dict[tuple[str, str, str | None], list[RawLine]] = {}
    handled = 0  # model-bound lines already answered or rejected

    with_own_statement = {ln.period for ln in raw_lines if ln.statement != "SE"}
    unused_periods: set[str] = set()
    for line in raw_lines:
        # Every row of the equity statement's retained earnings column is one movement (step 26),
        # but only in a period that an income statement, balance sheet or cash flow column also
        # has: the page's other blocks (a quarter, the prior year) would only add empty columns.
        if line.statement == "SE" and line.period not in with_own_statement:
            if line.period not in unused_periods:
                unused_periods.add(line.period)
                issues.append(
                    OpenIssue(
                        code="equity_period_not_used",
                        where=line.raw_line_id,
                        detail=f"equity statement block {line.period} matches no statement column",
                    )
                )
            continue
        hit = (
            EQUITY_COLUMN_LINE
            if line.statement == "SE"
            else table.lookup(line.statement, line.original_label, line.section)
        )
        if hit is not None:
            mapping[mapping_key(line.period, line.raw_line_id)] = MappedLine(
                raw_line_id=line.raw_line_id,
                period=line.period,
                standard_line_id=hit,
                confidence=1.0,
                rationale=f"rule: {normalize_label(line.original_label)}",
                origin="rule",
            )
        else:
            pending.setdefault(
                (line.statement, normalize_label(line.original_label), line.section), []
            ).append(line)

    system = load_prompt("map_lines", caller.prompt_version)
    for statement in ("IS", "BS", "CF"):
        groups = [(k, v) for k, v in pending.items() if k[0] == statement]
        for start in range(0, len(groups), batch_size):
            batch = groups[start : start + batch_size]
            keys = {f"k{i}": group for i, group in enumerate(batch, start=1)}
            try:
                response = _ask(caller, system, table, statement, keys)
            except TokenBudgetExceeded:
                left = sum(len(v) for v in pending.values()) - handled
                issues.append(
                    OpenIssue(
                        code="token_cap_reached",
                        where=statement,
                        detail=f"token cap reached; {left} line(s) not mapped",
                    )
                )
                return _result(mapping, issues, caller, usage_before, table, raw_lines)
            except InvalidModelOutput as exc:
                for _, lines in batch:
                    for line in lines:
                        issues.append(
                            OpenIssue(
                                code="mapping_invalid_output",
                                where=line.raw_line_id,
                                detail=str(exc)[:300],
                            )
                        )
                handled += sum(len(v) for _, v in batch)
                continue
            handled += sum(len(v) for _, v in batch)
            for proposal in response.mappings:
                for line in keys[proposal.key][1]:
                    mapping[mapping_key(line.period, line.raw_line_id)] = MappedLine(
                        raw_line_id=line.raw_line_id,
                        period=line.period,
                        standard_line_id=proposal.standard_line_id,
                        confidence=proposal.confidence,
                        rationale=proposal.rationale,
                        origin="model",
                    )
                    if proposal.confidence < low_confidence:
                        issues.append(
                            OpenIssue(
                                code="low_confidence_mapping",
                                where=line.raw_line_id,
                                detail=f"model confidence {proposal.confidence:.2f}",
                            )
                        )
    return _result(mapping, issues, caller, usage_before, table, raw_lines)


def _ask(
    caller: StructuredCaller,
    system: str,
    table: MappingTable,
    statement: str,
    keys: dict[str, tuple[tuple[str, str, str | None], list[RawLine]]],
) -> MappingResponse:
    allowed = sorted(table.line_ids(statement))
    labels = {k: " ".join(group[1][0].original_label.split()) for k, group in keys.items()}
    labels = {k: v.replace("<", " ").replace(">", " ") for k, v in labels.items()}
    listing = "\n".join(
        f"{k} | {statement} | {keys[k][0][2] or 'unknown'} | {label}" for k, label in labels.items()
    )
    user = (
        f"Allowed standard_line_id values for {statement}: {', '.join(allowed)}\n\n"
        f"Items (key | statement | printed section | label):\n<labels>\n{listing}\n</labels>"
    )

    def check(response: MappingResponse) -> None:
        seen: set[str] = set()
        for p in response.mappings:
            if p.key not in keys:
                raise ValueError(f"unknown key {p.key!r}")
            if p.key in seen:
                raise ValueError(f"key {p.key!r} answered twice")
            seen.add(p.key)
            if p.standard_line_id not in allowed:
                raise ValueError(f"{p.standard_line_id!r} is not an allowed {statement} line id")
        missing = sorted(set(keys) - seen)
        if missing:
            raise ValueError(f"no answer for keys: {', '.join(missing)}")

    return caller.call(system=system, user=user, schema=MappingResponse, check=check)


def _one_claim_per_total(
    mapping: dict[str, MappedLine],
    issues: list[OpenIssue],
    table: MappingTable,
    raw_lines: Sequence[RawLine],
) -> None:
    """A total or subtotal line holds one printed figure per period; it is never a sum.

    When two different raw lines claim one (a model put "Total current liabilities" on the total
    liabilities line), the rule hit wins, then the line whose label is the standard line's own
    ("Total equity" over "Total shareholders' equity"), then, on total equity only, the one printed
    last (after the minority interests; never a "... liabilities and equity" line), then the
    higher confidence, then the first read. The others are unmapped and reported, so nothing is
    counted twice and nothing is guessed.
    """
    single = {ln.line_id for ln in table.lines if ln.kind in ("total", "subtotal")}
    by_line = {(ln.period, ln.raw_line_id): ln for ln in raw_lines}
    own_label = {ln.line_id: normalize_label(ln.label) for ln in table.lines}

    position = {(ln.period, ln.raw_line_id): n for n, ln in enumerate(raw_lines)}

    def rank(m: MappedLine) -> tuple[bool, bool, int, float]:
        line = by_line.get((m.period, m.raw_line_id))
        is_own = (
            line is not None
            and normalize_label(line.original_label) == own_label[m.standard_line_id]
        )
        # Equity is printed as "Total shareholders' equity", then minority interests, then the
        # grand total; the last printed one holds everything, so it beats a higher confidence.
        later = 0
        if m.standard_line_id == _EQUITY and line is not None:
            if "liabilities" not in normalize_label(line.original_label):
                later = -position.get((m.period, m.raw_line_id), 0)
        return (m.origin != "rule", not is_own, later, -m.confidence)

    claims: dict[tuple[str, str], list[MappedLine]] = {}
    for mapped in mapping.values():
        if mapped.standard_line_id in single:
            claims.setdefault((mapped.period, mapped.standard_line_id), []).append(mapped)
    for group in claims.values():
        if len(group) < 2:
            continue
        ranked = sorted(group, key=rank)
        winner = by_line.get((ranked[0].period, ranked[0].raw_line_id))
        for loser in ranked[1:]:
            line = by_line.get((loser.period, loser.raw_line_id))
            del mapping[mapping_key(loser.period, loser.raw_line_id)]
            issues[:] = [
                i
                for i in issues
                if not (i.code == "low_confidence_mapping" and i.where == loser.raw_line_id)
            ]
            if (
                winner is not None
                and line is not None
                and line.original_value_text.strip() == winner.original_value_text.strip()
            ):
                continue  # the same figure printed twice
            issues.append(
                OpenIssue(
                    code="duplicate_total_mapping",
                    where=loser.raw_line_id,
                    detail=(
                        f"also mapped to {loser.standard_line_id} in {loser.period}; "
                        "a total is not added twice, so this line is left out"
                    ),
                )
            )


def _printed_total_over_parts(
    mapping: dict[str, MappedLine],
    issues: list[OpenIssue],
    table: MappingTable,
    raw_lines: Sequence[RawLine],
) -> None:
    """A printed "Total ..." line is the sum of lines already counted, so it is not added to them.

    "Total sales" next to "Products" and "Services", or "Total operating costs and expenses"
    next to its parts, all map to detail lines of the standard set (which has no line for such a
    total); added up, the figure is doubled. When a total-labelled line shares a detail line with
    other lines in one period, the total is unmapped and reported; the parts stay. A total that is
    alone on its line is kept: it is all the filing prints for that line.
    """
    detail = {ln.line_id for ln in table.lines if ln.kind == "detail"}
    by_line = {(ln.period, ln.raw_line_id): ln for ln in raw_lines}

    def is_total(m: MappedLine) -> bool:
        line = by_line.get((m.period, m.raw_line_id))
        return line is not None and normalize_label(line.original_label).split(" ")[0] == "total"

    groups: dict[tuple[str, str], list[MappedLine]] = {}
    for mapped in mapping.values():
        if mapped.standard_line_id in detail:
            groups.setdefault((mapped.period, mapped.standard_line_id), []).append(mapped)

    def leave_out(line: MappedLine, why: str) -> None:
        del mapping[mapping_key(line.period, line.raw_line_id)]
        issues[:] = [
            i
            for i in issues
            if not (i.code == "low_confidence_mapping" and i.where == line.raw_line_id)
        ]
        issues.append(
            OpenIssue(
                code="printed_total_not_added",
                where=line.raw_line_id,
                detail=(
                    f"{why} on {line.standard_line_id} in {line.period}; "
                    "its parts are counted, so it is left out of the sum"
                ),
            )
        )

    for group in groups.values():
        totals = [m for m in group if is_total(m)]
        if totals and len(totals) < len(group):
            for total in totals:
                leave_out(total, "a printed total")
            group = [m for m in group if m not in totals]
        subtotal = _sum_of_the_others(group, by_line)
        if subtotal is not None:
            leave_out(subtotal, "a printed subtotal (it equals the other lines added)")


def _sum_of_the_others(
    group: Sequence[MappedLine], by_line: Mapping[tuple[str, str], RawLine]
) -> MappedLine | None:
    """The one line whose printed figure equals the sum of all the others on the same line, or
    None. Needs two or more parts, one scale, and a figure of at least 1 (net property and
    equipment printed under cost and accumulated depreciation). Rounding of one unit per part is
    allowed, as in the printed statements."""
    if len(group) < 3:
        return None
    lines = [by_line.get((m.period, m.raw_line_id)) for m in group]
    if any(ln is None for ln in lines) or len({ln.scale for ln in lines if ln}) != 1:
        return None
    try:
        values = [parse_amount(ln.original_value_text) for ln in lines if ln]
    except ValueError:
        return None
    sums = sum(values)
    hits = [
        m
        for m, v in zip(group, values, strict=True)
        if abs(v) >= 1 and abs(v - (sums - v)) <= len(group) - 1
    ]
    return hits[0] if len(hits) == 1 else None


# What a cash flow line printed after the net cash from financing row can be: the cash summary.
# Anything else there (interest paid, taxes paid, restricted cash) is a supplemental disclosure.
_CASH_SUMMARY_LINES: Final = frozenset(
    {
        "cf_fx_effect",
        "cf_net_change_in_cash",
        "cf_cash_beginning",
        "cf_cash_end",
        "cf_net_cash_operating",
        "cf_net_cash_investing",
        "cf_net_cash_financing",
    }
)
SUPPLEMENTAL_LINE: Final = "cf_supplemental_items"


def summary_detail_to_memo(statement: str, section: str | None, line_id: str) -> str:
    """A cash flow line in the cash summary section that is not part of the summary is a memo
    line, so it is not added into a section total (plan step 27)."""
    if statement == "CF" and section == "cash_summary" and line_id not in _CASH_SUMMARY_LINES:
        return SUPPLEMENTAL_LINE
    return line_id


OPERATING_INCOME_LINE: Final = "is_operating_income_items"


def section_line(statement: str, section: str | None, line_id: str) -> str:
    """The line a mapped row ends on once its printed section is known (plan step 27): a cash
    flow line in the cash summary that is not part of the summary is a memo line, and an
    "other items" line printed between the costs and operating profit is operating income."""
    if statement == "IS" and section == "operating_block" and line_id == "is_other_items":
        return OPERATING_INCOME_LINE
    return summary_detail_to_memo(statement, section, line_id)


def _by_section(mapping: dict[str, MappedLine], raw_lines: Sequence[RawLine]) -> None:
    for line in raw_lines:
        key = mapping_key(line.period, line.raw_line_id)
        mapped = mapping.get(key)
        if mapped is None:
            continue
        new_id = section_line(line.statement, line.section, mapped.standard_line_id)
        if new_id != mapped.standard_line_id:
            mapping[key] = mapped.model_copy(update={"standard_line_id": new_id})


def _result(
    mapping: dict[str, MappedLine],
    issues: list[OpenIssue],
    caller: StructuredCaller,
    before: Usage,
    table: MappingTable,
    raw_lines: Sequence[RawLine],
) -> MappingResult:
    _by_section(mapping, raw_lines)
    _one_claim_per_total(mapping, issues, table, raw_lines)
    _printed_total_over_parts(mapping, issues, table, raw_lines)
    spent = Usage(
        input_tokens=caller.usage.input_tokens - before.input_tokens,
        output_tokens=caller.usage.output_tokens - before.output_tokens,
    )
    return MappingResult(mapping, tuple(issues), spent)
