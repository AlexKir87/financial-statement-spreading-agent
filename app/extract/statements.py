"""`extract_statements`: page text to typed `RawLine`s through one validated model call per page.

The model returns labels and values as printed. Code keeps a line only if its label and value
text are printed on the same row of the page, so a number the model made up or borrowed from
another row cannot become a `RawLine`. Amounts are parsed from the printed text by
`parse_amount`, later, in code.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.extract.amounts import currency_from_symbol, normalise_amount_text, parse_amount
from app.extract.unlabeled import (
    column_of,
    infer_totals,
    parse_rows,
    values_in_blank_columns,
)
from app.ingest.loader import is_equity_heading
from app.periods import canonical_period, looks_like_a_date
from app.prompts import load_prompt
from app.sections import row_sections
from app.state import Cell, DigitizedPage, OpenIssue, RawLine, Usage
from app.tools.model_client import InvalidModelOutput, StructuredCaller, TokenBudgetExceeded
from app.tools.schemas import CURRENCY_NOT_STATED, ExtractedLine, PageExtraction

# A reply is cut off at `MAX_REPLY_TOKENS`, and a cut JSON list is invalid, so a long page is read
# in parts (plan step 20). Each part repeats the page's first rows so the model still sees the
# column headings, the scale and the currency.
MAX_ROWS_PER_CALL = 40
HEADER_ROWS = 12
MIN_ROWS_TO_HALVE = 20  # a shorter part cannot reach the reply ceiling, so bad JSON is not a cut
_CONTINUED = "[continued]"
_CONTINUED_NOTE = (
    f"\n\nThe rows above the line {_CONTINUED} only repeat the page heading for context. "
    f"List lines only from the rows after {_CONTINUED}."
)


@dataclass(frozen=True)
class ExtractionResult:
    raw_lines: tuple[RawLine, ...]
    open_issues: tuple[OpenIssue, ...]
    usage: Usage


def _squash(text: str) -> str:
    return " ".join(text.lower().split())


_DASHES = "-−–—"


def _printed(value_text: str, row: str) -> bool:
    """The value is its own token in the row, signs and brackets included.

    `500` is not found in `1,500`, `500.25`, `(500)` or `-500`; a lone dash only as a cell.
    """
    needle = re.escape(normalise_amount_text(_squash(value_text)))
    if value_text.strip() and all(ch in _DASHES for ch in value_text.strip()):
        pattern = r"(?<!\S)" + needle + r"(?!\S)"
    else:
        pattern = r"(?<![\d,.()" + _DASHES + r"])" + needle + r"(?![\d,)]|\.\d)"
    return re.search(pattern, row) is not None


def _section(label: str, value_text: str, statement: str, text: str) -> str | None:
    """The printed section of the row that carries this label and value; `None` when the row
    sits above every heading or the same label and value are printed under different headings."""
    want = _squash(label)
    rows = text.splitlines()
    sections = row_sections(statement, rows)
    found = {
        sections[i]
        for i, row in enumerate(rows)
        if want in _squash(row) and _printed(value_text, normalise_amount_text(_squash(row)))
    }
    return found.pop() if len(found) == 1 else None


def _on_one_row(label: str, value_text: str, text: str) -> bool:
    """Label and value are printed on the same line of the page, so a number cannot be borrowed
    from another row. A label wrapped over two lines is dropped and flagged, not guessed."""
    want = _squash(label)
    for row in text.splitlines():
        squashed = _squash(row)
        if want in squashed and _printed(value_text, normalise_amount_text(squashed)):
            return True
    return False


def _data(text: str) -> str:
    """Page or label text as prompt data: angle brackets cannot close the delimiter tag."""
    return text.replace("<", " ").replace(">", " ")


def page_text(page: DigitizedPage) -> str:
    """The text layer, or the digitizer's cells joined row by row when there is no text."""
    if page.text:
        return page.text
    rows: dict[int, list[Cell]] = {}
    for cell in page.cells:
        rows.setdefault(cell.row, []).append(cell)
    return "\n".join(
        "  ".join(c.text for c in sorted(cells, key=lambda c: c.col))
        for _, cells in sorted(rows.items())
    )


def _parts(rows: list[str]) -> list[list[str]]:
    """The page's rows in parts of at most `MAX_ROWS_PER_CALL`, split at row breaks."""
    count = -(-len(rows) // MAX_ROWS_PER_CALL)
    size = -(-len(rows) // count)
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _part_prompt(text: str, rows: list[str], first_row: int, all_rows: list[str]) -> str:
    """The user message for one part. A page read whole is sent as printed; a later part repeats
    the heading rows as context."""
    if len(rows) == len(all_rows):
        return f"<page>\n{_data(text)}\n</page>"
    if first_row == 0:
        return f"<page>\n{_data(chr(10).join(rows))}\n</page>"
    body = [*all_rows[: min(HEADER_ROWS, first_row)], _CONTINUED, *rows]
    return f"<page>\n{_data(chr(10).join(body))}\n</page>{_CONTINUED_NOTE}"


def _readable(line: ExtractedLine, text: str) -> bool:
    """The value parses and label and value are printed on one row of the page."""
    try:
        parse_amount(line.value_text)
    except ValueError:
        return False
    return _on_one_row(line.label, line.value_text, text)


def _raw_line(
    page: DigitizedPage,
    n: int,
    line: ExtractedLine,
    keys: dict[tuple[str, str], str],
    text: str,
    scale: str,
    currency: str,
) -> RawLine:
    return RawLine(
        raw_line_id=f"{page.doc_id}:p{page.page}:l{n}",
        doc_id=page.doc_id,
        page=page.page,
        original_label=line.label,
        original_value_text=line.value_text,
        statement=line.statement,
        period=keys[(line.period, line.statement)],
        period_label=line.period,
        section=_section(line.label, line.value_text, line.statement, text),
        scale=scale,
        currency=currency,
    )


def _reread(
    text: str,
    dropped: list[tuple[int, ExtractedLine]],
    kept: list[RawLine],
    caller: StructuredCaller,
    system: str,
) -> tuple[list[tuple[int, ExtractedLine]], list[tuple[int, ExtractedLine]]]:
    """One more read for the lines whose label was not on the row (plan step 28): the model is
    shown the page and the dropped (period, label, value) triples and writes each label as the
    row prints it. A reread line is kept only if it is one of the dropped (period, value) pairs
    and passes the same check as a first read; nothing else it says is used. Returns what is
    still dropped and what was recovered, the recovered ones under their original line number."""
    asked = "\n".join(f"{ln.period} | {ln.label} | {ln.value_text}" for _, ln in dropped)
    user = f"<page>\n{_data(text)}\n</page>\n\n<dropped>\n{_data(asked)}\n</dropped>"
    try:
        reply = caller.call(system=system, user=user, schema=PageExtraction)
    except (InvalidModelOutput, TokenBudgetExceeded):
        return (
            dropped,
            [],
        )  # the lines stay dropped and flagged; the cap is reported by the next call
    pending = dict(dropped)
    waiting: dict[tuple[str, str], list[int]] = {}
    for n, ln in dropped:
        waiting.setdefault((ln.period, _squash(normalise_amount_text(ln.value_text))), []).append(n)
    have = {
        (r.statement, r.period_label, _squash(r.original_label), _squash(r.original_value_text))
        for r in kept
    }
    recovered: list[tuple[int, ExtractedLine]] = []
    for line in reply.lines:
        value = _squash(normalise_amount_text(line.value_text))
        ids = waiting.get((line.period, value))
        if not ids or line.statement != pending[ids[0]].statement or not _readable(line, text):
            continue
        same = (line.statement, line.period, _squash(line.label), _squash(line.value_text))
        if same in have:
            continue  # the row is already a kept line; keeping it again would count it twice
        have.add(same)
        recovered.append((ids.pop(0), line))
    done = {n for n, _ in recovered}
    return [(n, ln) for n, ln in dropped if n not in done], recovered


def _name_unlabeled_rows(
    page: DigitizedPage,
    text: str,
    page_lines: list[RawLine],
    scale: str,
    currency: str,
) -> tuple[list[RawLine], list[OpenIssue]]:
    """Balance sheet rows printed as bare numbers, named from the sums above them (step 28)."""
    where = f"{page.doc_id} p{page.page}"
    bs = [ln for ln in page_lines if ln.statement == "BS" and ln.period_label]
    periods = {ln.period_label: ln.period for ln in bs if ln.period_label}
    if len(periods) < 1:
        return [], []
    rows = parse_rows(text, len(periods))
    column: dict[int, set[str]] = {}
    for ln in bs:
        at = column_of(rows, ln.original_label, ln.original_value_text)
        if at is not None and ln.period_label:
            column.setdefault(at, set()).add(ln.period_label)
    if sorted(column) != list(range(len(periods))) or any(len(v) != 1 for v in column.values()):
        if any(not r.label for r in rows):
            return [], [
                OpenIssue(
                    code="unlabeled_row_not_resolved",
                    where=where,
                    detail="number-only rows not read: the period columns could not be matched",
                )
            ]
        return [], []
    names, unread = infer_totals(rows, len(periods))
    out: list[RawLine] = []
    issues = [
        OpenIssue(
            code="unlabeled_row_not_resolved",
            where=where,
            detail=f"row {u.row.index + 1} printed with no label, not read: {u.reason}",
        )
        for u in unread
    ]
    for item in names:
        for at, token in enumerate(item.row.tokens):
            label = next(iter(column[at]))
            out.append(
                RawLine(
                    raw_line_id=f"{page.doc_id}:p{page.page}:u{item.row.index + 1}c{at + 1}",
                    doc_id=page.doc_id,
                    page=page.page,
                    original_label=item.label,
                    original_value_text=token,
                    statement="BS",
                    period=periods[label],
                    period_label=label,
                    section=item.row.section,
                    scale=scale,
                    currency=currency,
                )
            )
        issues.append(
            OpenIssue(
                code="unlabeled_total_inferred",
                where=where,
                detail=(
                    f"row {item.row.index + 1} prints numbers with no label; read as "
                    f"'{item.label}' because it equals the sum of the rows above it in every period"
                ),
            )
        )
    return out, issues


@dataclass(frozen=True)
class _PageRead:
    currency: str
    scale: str
    conflicts: tuple[str, ...]
    lines: tuple[ExtractedLine, ...]


def _read_page(text: str, caller: StructuredCaller, system: str) -> _PageRead:
    """The page's lines, read part by part. A part whose reply is not valid JSON (usually cut off
    at the reply ceiling) is read again in two halves; a part of fewer than `MIN_ROWS_TO_HALVE`
    rows raises `InvalidModelOutput`. Currency and scale are the first part that states them."""
    # a page row cannot pose as the marker that tells the model where the real rows start
    all_rows = [row.replace(_CONTINUED, "(continued)") for row in text.splitlines()]
    replies: list[PageExtraction] = []

    def read(rows: list[str], first_row: int) -> None:
        try:
            replies.append(
                caller.call(
                    system=system,
                    user=_part_prompt(text, rows, first_row, all_rows),
                    schema=PageExtraction,
                )
            )
        except InvalidModelOutput:
            if len(rows) < MIN_ROWS_TO_HALVE:
                raise
            half = len(rows) // 2
            read(rows[:half], first_row)
            read(rows[half:], first_row + half)

    done = 0
    for part in _parts(all_rows):
        read(part, done)
        done += len(part)
    currency = next(
        (r.currency for r in replies if r.currency != CURRENCY_NOT_STATED), CURRENCY_NOT_STATED
    )
    scale = next((r.scale for r in replies if r.scale != "not_stated"), "not_stated")
    conflicts = tuple(
        name
        for name, stated in (
            ("currency", {r.currency for r in replies} - {CURRENCY_NOT_STATED}),
            ("scale", {r.scale for r in replies} - {"not_stated"}),
        )
        if len(stated) > 1
    )
    return _PageRead(currency, scale, conflicts, tuple(ln for r in replies for ln in r.lines))


def extract_statements(
    pages: Sequence[DigitizedPage], caller: StructuredCaller
) -> ExtractionResult:
    """One model call per page that has text. Stops cleanly when the token cap is spent."""
    system = load_prompt("extract_statements", caller.prompt_version)
    equity_system = load_prompt("extract_equity", caller.prompt_version)
    reread_system = load_prompt("reread_dropped", caller.prompt_version)
    lines: list[RawLine] = []
    issues: list[OpenIssue] = []
    usage_before = caller.usage
    for index, page in enumerate(pages):
        where = f"{page.doc_id} p{page.page}"
        text = page_text(page)
        if not text.strip():
            continue
        equity = any(is_equity_heading(row) for row in text.splitlines()[:3])
        try:
            result = _read_page(text, caller, equity_system if equity else system)
        except TokenBudgetExceeded:
            left = sum(1 for p in pages[index:] if page_text(p).strip())
            issues.append(
                OpenIssue(
                    code="token_cap_reached",
                    where=where,
                    detail=f"token cap reached; {left} page(s) not extracted",
                )
            )
            break
        except InvalidModelOutput as exc:
            issues.append(
                OpenIssue(code="extraction_invalid_output", where=where, detail=str(exc)[:300])
            )
            continue
        for name in result.conflicts:
            issues.append(
                OpenIssue(
                    code=f"{name}_differs_between_parts",
                    where=where,
                    detail=f"parts of a long page state different {name}s; the first is kept",
                )
            )
        if result.scale == "not_stated":
            issues.append(
                OpenIssue(
                    code="scale_assumed", where=where, detail="page states no scale; units assumed"
                )
            )
        currency = result.currency
        if currency == CURRENCY_NOT_STATED and (symbol := currency_from_symbol(text)):
            currency = symbol
            issues.append(
                OpenIssue(
                    code="currency_assumed_from_symbol",
                    where=where,
                    detail=f"page prints only a currency symbol; read as {symbol}",
                )
            )
        elif currency == CURRENCY_NOT_STATED:
            issues.append(
                OpenIssue(code="currency_not_stated", where=where, detail="page states no currency")
            )
        scale = "units" if result.scale == "not_stated" else result.scale
        keys: dict[tuple[str, str], str] = {}
        for label, statement in dict.fromkeys((ln.period, ln.statement) for ln in result.lines):
            key = canonical_period(label, text, statement)
            if key is None and looks_like_a_date(label):
                issues.append(
                    OpenIssue(
                        code="period_not_canonical",
                        where=where,
                        detail=f"a {statement} column heading is not a readable date; kept",
                    )
                )
            keys[(label, statement)] = key or label
        page_lines: list[RawLine] = []
        dropped: list[tuple[int, ExtractedLine]] = []
        for n, line in enumerate(result.lines, start=1):
            if (line.statement == "SE") != equity:
                issues.append(
                    OpenIssue(
                        code="extracted_line_not_in_source",
                        where=where,
                        detail=f"line {n} dropped: {line.statement} line on the wrong kind of page",
                    )
                )
                continue
            if not _readable(line, text):
                dropped.append((n, line))
                continue
            page_lines.append(_raw_line(page, n, line, keys, text, scale, currency))
        if dropped and not equity:
            dropped, recovered = _reread(text, dropped, page_lines, caller, reread_system)
            for n, line in recovered:
                page_lines.append(_raw_line(page, n, line, keys, text, scale, currency))
        for n, _ in dropped:
            issues.append(
                OpenIssue(
                    code="extracted_line_not_in_source",
                    where=where,
                    detail=f"line {n} dropped: label and value not printed on one row",
                )
            )
        page_lines, misplaced = values_in_blank_columns(text, page_lines)
        issues.extend(
            OpenIssue(
                code="value_in_blank_column",
                where=where,
                detail=f"'{ln.original_label}' dropped: read into a column the page prints empty",
            )
            for ln in misplaced
        )
        if not equity:
            named, unread = _name_unlabeled_rows(page, text, page_lines, scale, currency)
            page_lines.extend(named)
            issues.extend(unread)
        lines.extend(page_lines)
    spent = Usage(
        input_tokens=caller.usage.input_tokens - usage_before.input_tokens,
        output_tokens=caller.usage.output_tokens - usage_before.output_tokens,
    )
    return ExtractionResult(tuple(lines), tuple(issues), spent)
