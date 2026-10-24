"""Deterministic workbook writer (the spec "Workbook sheets"; plan step 5).

Formulas come only from `app.workbook.templates`; every other cell is written as a number or as
a plain string, so text read from a filing can never become a formula. A write never replaces a
file: it creates `<run_id>_vN.xlsx` with the next free N and leaves earlier versions untouched.
"""

import hashlib
import os
import re
import tempfile
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Final

import xlsxwriter
from xlsxwriter.format import Format
from xlsxwriter.workbook import Workbook
from xlsxwriter.worksheet import Worksheet

from app.state import CHECK_ORDER, CheckResult, MappedLine, RawLine, WorkbookVersion
from app.workbook.models import LayoutRow, WorkbookData, WorkbookError
from app.workbook.templates import CellRef, SheetName, StatusLabel, render

STATEMENT_SHEETS: Final[dict[str, SheetName]] = {"is": "IS", "bs": "BS", "cf": "CF"}
MAX_CLAIM_ATTEMPTS: Final = 100
MAX_TEXT: Final = 32_000  # Excel's cell limit is 32,767 characters
FIRST_PERIOD_COL: Final = 2
RECON_HEADER_ROW: Final = 2
RECON_FIRST_ROW: Final = RECON_HEADER_ROW + 1
RECON_COLS: Final = ("Check", "Period", "Expected", "Actual", "Difference", "Status", "Terms")
MAPPING_COLS: Final = (
    "Period",
    "Raw line id",
    "Original label",
    "Standard line id",
    "Confidence",
    "Origin",
    "Rationale",
)
# Analyst-editable columns; step 11 reads them back.
CORRECTION_COLS: Final = ("Correction id", "Corrected standard line id", "Who", "Why")
_VERSION_RE: Final = r"^{run_id}_v(\d+)\.xlsx$"


def _sheet_for(line_id: str) -> SheetName:
    try:
        return STATEMENT_SHEETS[line_id.split("_", 1)[0]]
    except KeyError:
        raise WorkbookError(f"line id {line_id} is not an is_, bs_ or cf_ line") from None


def _text(value: str) -> str:
    return value if len(value) <= MAX_TEXT else value[:MAX_TEXT]


def _num(value: Decimal) -> float:
    return float(value)


class _Sheets:
    """Formats and small helpers shared by the sheet builders."""

    def __init__(self, wb: Workbook) -> None:
        self.head: Format = wb.add_format({"bold": True, "bg_color": "#DDE6F0", "border": 1})
        self.amount: Format = wb.add_format({"num_format": "#,##0;[Red]-#,##0"})
        self.total: Format = wb.add_format({"num_format": "#,##0;[Red]-#,##0", "bold": True})
        self.bold: Format = wb.add_format({"bold": True})
        self.input: Format = wb.add_format({"bg_color": "#FFF8DC"})  # analyst-editable cells

    @staticmethod
    def row(
        ws: Worksheet, r: int, values: Sequence[str | int | float | None], fmt: Format | None = None
    ) -> None:
        for c, v in enumerate(values):
            if v is None:
                continue
            if isinstance(v, str):
                ws.write_string(r, c, _text(v), fmt)
            else:
                ws.write_number(r, c, v, fmt)


def _statement_rows(data: WorkbookData, sheet: SheetName) -> list[LayoutRow]:
    """Layout rows for one statement, then spread lines the layout does not name."""
    rows = [lr for lr in data.layout if _sheet_for(lr.line_id) == sheet]
    named = {lr.line_id for lr in rows}
    for lr in rows:
        for op in lr.operands:
            if _sheet_for(op) != sheet:
                raise WorkbookError(f"operand {op} of {lr.line_id} is on another statement")
            if op not in named and not any(op in p for p in data.spread.values()):
                raise WorkbookError(f"operand {op} of {lr.line_id} is not in the layout or spread")
    extra = sorted(
        {lid for p in data.spread.values() for lid in p if _sheet_for(lid) == sheet} - named
    )
    rows += [LayoutRow(line_id=lid, label=lid) for lid in extra]
    return rows


def _write_statement(
    ws: Worksheet, sheet: SheetName, data: WorkbookData, fmt: _Sheets, periods: list[str]
) -> dict[str, int]:
    rows = _statement_rows(data, sheet)
    _Sheets.row(ws, 0, ["Line id", "Label", *periods], fmt.head)
    index = {lr.line_id: i + 1 for i, lr in enumerate(rows)}
    for lr in rows:
        r = index[lr.line_id]
        ws.write_string(r, 0, lr.line_id)
        ws.write_string(r, 1, _text(lr.label))
        for j, period in enumerate(periods):
            c = FIRST_PERIOD_COL + j
            values = data.spread.get(period, {})
            if lr.template == "value":
                if lr.line_id in values:
                    ws.write_number(r, c, _num(values[lr.line_id]), fmt.amount)
                continue
            missing = [op for op in lr.operands if op not in values]
            if missing:
                raise WorkbookError(
                    f"{lr.line_id} in {period}: operands missing: {', '.join(missing)}"
                )
            refs = tuple(CellRef(sheet=sheet, row=index[op], col=c) for op in lr.operands)
            nums = [values[op] for op in lr.operands]
            cached = sum(nums, Decimal(0)) if lr.template == "sum" else nums[0] - nums[1]
            if lr.line_id in values and values[lr.line_id] != cached:
                raise WorkbookError(
                    f"{lr.line_id} in {period}: spread value disagrees with its formula"
                )
            ws.write_formula(r, c, render(lr.template, refs, on=sheet), fmt.total, _num(cached))
    ws.freeze_panes(1, FIRST_PERIOD_COL)
    ws.set_column(0, 0, 28)
    ws.set_column(1, 1, 40)
    ws.set_column(FIRST_PERIOD_COL, FIRST_PERIOD_COL + max(len(periods), 1), 14)
    return index


def _trace_rows(data: WorkbookData) -> list[tuple[str, str, RawLine, MappedLine]]:
    raw = {(r.period, r.raw_line_id): r for r in data.raw_lines}
    by_target: dict[tuple[str, str], list[tuple[RawLine, MappedLine]]] = {}
    for m in data.mapping.values():
        r = raw.get((m.period, m.raw_line_id))
        if r is not None:
            by_target.setdefault((m.period, m.standard_line_id), []).append((r, m))
    untraced = [
        f"{lid} in {period}"
        for period, values in data.spread.items()
        for lid in values
        if (period, lid) not in by_target
    ]
    if untraced:
        raise WorkbookError("numbers without a source trace: " + "; ".join(sorted(untraced)))
    return [
        (period, lid, r, m)
        for period, values in data.spread.items()
        for lid in values
        for r, m in by_target[(period, lid)]
    ]


def _write_trace(ws: Worksheet, data: WorkbookData, fmt: _Sheets) -> None:
    digitizer = {(d.doc_id, d.page): d for d in data.digitized}
    cols = [
        "Period",
        "Line id",
        "Document",
        "Page",
        "Original label",
        "Original value text",
        "Digitizer",
        "Digitizer version",
        "Mapping origin",
    ]
    _Sheets.row(ws, 0, cols, fmt.head)
    for i, (period, lid, r, m) in enumerate(_trace_rows(data), start=1):
        d = digitizer.get((r.doc_id, r.page))
        _Sheets.row(
            ws,
            i,
            [
                period,
                lid,
                r.doc_id,
                r.page,
                r.shown_label,
                r.original_value_text,
                d.digitizer_name if d else "text layer",
                d.digitizer_version if d else "n/a",
                m.origin,
            ],
        )
    ws.freeze_panes(1, 0)
    ws.set_column(0, 3, 14)
    ws.set_column(4, 5, 36)


def _ordered_checks(data: WorkbookData) -> list[CheckResult]:
    order = {k: i for i, k in enumerate(CHECK_ORDER)}
    period_pos = {p: i for i, p in enumerate(data.spread)}
    indexed = enumerate(data.check_results)
    ranked = sorted(
        indexed, key=lambda t: (order[t[1].key], period_pos.get(t[1].period, len(period_pos)), t[0])
    )
    return [r for _, r in ranked]


def _write_reconciliations(ws: Worksheet, data: WorkbookData, fmt: _Sheets) -> list[int]:
    """Returns the Excel row index of each written check (for the Summary counts)."""
    ws.write_string(0, 0, "Tolerance (units of scale)", fmt.bold)
    ws.write_number(0, 1, _num(data.tolerance), fmt.input)
    tol = CellRef(sheet="Reconciliations", row=0, col=1, abs_=True)
    _Sheets.row(ws, RECON_HEADER_ROW, list(RECON_COLS), fmt.head)
    rows: list[int] = []
    for n, r in enumerate(_ordered_checks(data)):
        row = RECON_FIRST_ROW + n
        rows.append(row)
        _Sheets.row(ws, row, [r.key, r.period])
        if r.status == "not_applicable":
            ws.write_string(row, 5, "N/A")
        else:
            exp, act = r.expected, r.actual
            assert exp is not None and act is not None  # noqa: S101 - CheckResult guarantees it
            ws.write_number(row, 2, _num(exp), fmt.amount)
            ws.write_number(row, 3, _num(act), fmt.amount)
            e = CellRef(sheet="Reconciliations", row=row, col=2)
            a = CellRef(sheet="Reconciliations", row=row, col=3)
            d = CellRef(sheet="Reconciliations", row=row, col=4)
            cached_diff = act - exp
            ws.write_formula(
                row,
                4,
                render("difference", (a, e), on="Reconciliations"),
                fmt.amount,
                _num(cached_diff),
            )
            ws.write_formula(
                row,
                5,
                render("ok_check", (d, tol), on="Reconciliations"),
                None,
                "OK" if r.status == "pass" else "CHECK",
            )
        terms = "; ".join(f"{t.line_id} {t.period} = {t.value}" for t in r.terms)
        if terms:
            ws.write_string(row, 6, _text(terms))
    ws.set_column(0, 1, 22)
    ws.set_column(2, 5, 14)
    ws.set_column(6, 6, 60)
    return rows


def _write_summary(
    ws: Worksheet, data: WorkbookData, fmt: _Sheets, recon_rows: list[int], version: int
) -> None:
    facts: list[tuple[str, str | int | float]] = [
        ("Run id", data.run_id),
        ("Company", data.company_ref),
        ("Status", data.status),
        ("Workbook version", version),
        ("Currency", data.scale.currency),
        ("Scale", data.scale.unit),
        ("Tolerance (units of scale)", _num(data.tolerance)),
        ("Plug figures", 0),  # the model has one legal value; there is no way to write another
    ]
    for i, (k, v) in enumerate(facts):
        ws.write_string(i, 0, k, fmt.bold)
        if isinstance(v, str):
            ws.write_string(i, 1, _text(v))
        else:
            ws.write_number(i, 1, v)
    r = len(facts) + 1
    labels: tuple[StatusLabel, ...] = ("OK", "CHECK", "N/A")
    for n, label in enumerate(labels):
        ws.write_string(r + n, 0, f"Checks {label}", fmt.bold)
        if not recon_rows:
            ws.write_number(r + n, 1, 0)
            continue
        first = CellRef(sheet="Reconciliations", row=recon_rows[0], col=5, abs_=True)
        last = CellRef(sheet="Reconciliations", row=recon_rows[-1], col=5, abs_=True)
        cached = sum(1 for c in data.check_results if _label(c) == label)
        ws.write_formula(
            r + n, 1, render("count_status", (first, last), on="Summary", label=label), None, cached
        )
    r += len(labels) + 1
    ws.write_string(r, 0, "Exceptions", fmt.bold)
    r += 1
    _Sheets.row(ws, r, ["Check", "Period", "Difference", "Attempts", "Explanation"], fmt.head)
    for e in data.exceptions:
        r += 1
        _Sheets.row(ws, r, [e.check, e.period])
        ws.write_number(r, 2, _num(e.difference), fmt.amount)
        ws.write_number(r, 3, e.attempts_made)
        ws.write_string(r, 4, _text(e.explanation))
    ws.set_column(0, 0, 28)
    ws.set_column(1, 3, 18)
    ws.set_column(4, 4, 70)


def _label(result: CheckResult) -> StatusLabel:
    if result.status == "not_applicable":
        return "N/A"
    return "OK" if result.status == "pass" else "CHECK"


def _write_simple(
    ws: Worksheet, fmt: _Sheets, header: list[str], rows: list[list[str | int | float | None]]
) -> None:
    _Sheets.row(ws, 0, header, fmt.head)
    for i, values in enumerate(rows, start=1):
        _Sheets.row(ws, i, values)
    ws.freeze_panes(1, 0)
    ws.set_column(0, len(header) - 1, 24)


def _build(path: Path, data: WorkbookData, version: int) -> None:
    wb = xlsxwriter.Workbook(str(path))
    try:
        wb.set_properties({"title": "Spread", "created": data.created})
        fmt = _Sheets(wb)
        sheets = {
            name: wb.add_worksheet(name)
            for name in (
                "Summary",
                "IS",
                "BS",
                "CF",
                "Source_Trace",
                "Reconciliations",
                "Open_Issues",
                "Change_Log",
                "Mapping",
            )
        }
        periods = list(data.spread)
        for sheet in STATEMENT_SHEETS.values():
            _write_statement(sheets[sheet], sheet, data, fmt, periods)
        _write_trace(sheets["Source_Trace"], data, fmt)
        recon_rows = _write_reconciliations(sheets["Reconciliations"], data, fmt)
        _write_summary(sheets["Summary"], data, fmt, recon_rows, version)
        _write_simple(
            sheets["Open_Issues"],
            fmt,
            ["Code", "Where", "Detail"],
            [[i.code, i.where, i.detail] for i in data.open_issues],
        )
        _write_simple(
            sheets["Change_Log"],
            fmt,
            ["Who", "What", "When", "Why"],
            [[c.who, c.what, c.when.isoformat(), c.why] for c in data.change_log],
        )
        mapping_rows: list[list[str | int | float | None]] = []
        raw = {(r.period, r.raw_line_id): r for r in data.raw_lines}
        for m in data.mapping.values():
            r = raw.get((m.period, m.raw_line_id))
            mapping_rows.append(
                [
                    m.period,
                    m.raw_line_id,
                    r.shown_label if r else "",
                    m.standard_line_id,
                    m.confidence,
                    m.origin,
                    m.rationale,
                ]
            )
        ms = sheets["Mapping"]
        _write_simple(ms, fmt, [*MAPPING_COLS, *CORRECTION_COLS], mapping_rows)
        ms.set_column(
            len(MAPPING_COLS), len(MAPPING_COLS) + len(CORRECTION_COLS) - 1, 24, fmt.input
        )
    finally:
        wb.close()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _existing_versions(output_dir: Path, run_id: str) -> dict[int, Path]:
    pattern = re.compile(_VERSION_RE.format(run_id=re.escape(run_id)))
    found: dict[int, Path] = {}
    for p in output_dir.iterdir():
        m = pattern.match(p.name)
        if m:
            found[int(m.group(1))] = p
    return found


def write_workbook(data: WorkbookData, output_dir: Path) -> WorkbookVersion:
    """Write `<run_id>_vN.xlsx` into `output_dir` and return its `WorkbookVersion`.

    Never overwrites: the file is built under a temporary name and hard-linked to the next free
    version name (`os.link` fails if the name exists, so two writers cannot clobber each other).
    If the bytes equal the latest version, that version is returned and nothing new is written,
    so a re-run of the node after a crash is idempotent.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".xlsx", dir=output_dir)
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        for _ in range(MAX_CLAIM_ATTEMPTS):
            versions = _existing_versions(output_dir, data.run_id)
            version = max(versions, default=0) + 1
            _build(tmp, data, version)
            if versions and _same_content_as_latest(versions, data):
                latest = max(versions)
                return WorkbookVersion(
                    path=str(versions[latest]), version=latest, sha256=_sha256(versions[latest])
                )
            final = output_dir / f"{data.run_id}_v{version}.xlsx"
            try:
                os.link(tmp, final)
            except FileExistsError:
                continue  # another writer took this number; take the next one
            except OSError as exc:
                raise WorkbookError(f"cannot create {final.name}: {type(exc).__name__}") from None
            final.chmod(0o644)
            return WorkbookVersion(path=str(final), version=version, sha256=_sha256(final))
        raise WorkbookError("could not claim a free version number")
    finally:
        tmp.unlink(missing_ok=True)


def _same_content_as_latest(versions: dict[int, Path], data: WorkbookData) -> bool:
    """True if rebuilding the latest version from `data` gives the bytes already on disk.

    The Summary sheet carries the version number, so the new build (numbered latest + 1) is never
    byte-equal to the latest file; compare against a rebuild numbered like the latest.
    """
    latest = max(versions)
    path = versions[latest]
    fd, name = tempfile.mkstemp(prefix=".tmp-", suffix=".xlsx", dir=path.parent)
    os.close(fd)
    probe = Path(name)
    try:
        _build(probe, data, latest)
        return _sha256(probe) == _sha256(path)
    finally:
        probe.unlink(missing_ok=True)
