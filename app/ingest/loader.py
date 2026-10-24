"""Load filings from the Library: hash, detect kind, read the text layer, run the gates' inputs.

Read-only. Paths must resolve inside the Library root. Nothing from a document's text reaches an
open issue: issues carry doc ids, page numbers and pattern names (see `injection.py`).
"""

import hashlib
import html.parser
import io
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import openpyxl
import pypdf

from app.ingest.injection import find_instruction_like
from app.ingest.kinds import UnsupportedFile, detect_kind, pdf_page_texts
from app.ingest.language import UNDETERMINED, detect_language
from app.state import DigitizedPage, DocKind, OpenIssue, SourceDoc

MAX_FILE_BYTES: Final = 200 * 1024 * 1024
MAX_SHEET_ROWS: Final = 20_000
_PAGE_MARKER = re.compile(r"^[ \t]*---[ \t]*[^\W\d_]*[ \t]*(\d+)[ \t]*---", re.MULTILINE)

_READERS: Final[dict[str, tuple[str, str]]] = {
    "text_pdf": ("pdf-text-layer", pypdf.__version__),
    "xlsx": ("xlsx-reader", openpyxl.__version__),
    "xbrl": ("ixbrl-text", "1"),
}


@dataclass(frozen=True)
class IngestResult:
    documents: tuple[SourceDoc, ...] = ()
    digitized: tuple[DigitizedPage, ...] = ()
    open_issues: tuple[OpenIssue, ...] = ()


ROW_BREAK: Final = "\n"
# An empty value cell of a statement row (an inline XBRL fact marked nil), kept visible so a lone
# number in the row is read in its own period column (plan step 30).
BLANK_CELL: Final = "[blank]"
_ROW_TAGS: Final = frozenset({"tr", "p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"})


class _Visible(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: Sequence[tuple[str, str | None]]) -> None:
        if tag == "ix:nonfraction" and any(
            k == "xsi:nil" and (v or "").strip().lower() in {"true", "1"} for k, v in attrs
        ):
            self.parts.append(BLANK_CELL)
        elif tag in {"script", "style"}:
            self._skip += 1
        elif tag in _ROW_TAGS and self.parts and self.parts[-1] != ROW_BREAK:
            self.parts.append(ROW_BREAK)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


_HEADINGS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "equity",
        re.compile(
            r"statements? of (changes in )?((share|stock)holders[\u2019']?s?[\u2019']? )?"
            r"(equity|deficit)|statements? of changes in (share|stock)holders"
        ),
    ),
    ("other", re.compile(r"statements? of (comprehensive|(share|stock)holders|equity|changes)")),
    (
        "balance",
        re.compile(
            r"(balance sheets?($|[ ,(]*(\(|as of)|,)|statements? of financial (position|condition))"
        ),
    ),
    ("cash", re.compile(r"statements? of cash flows?")),
    ("income", re.compile(r"statements? of (operations|income|earnings)")),
)
_HEADING_PREFIX = re.compile(r"^(unaudited )?(condensed )?(consolidated )?")
_WINDOW_END = re.compile(r"^(notes? to\b|index for notes|(see|the) accompanying notes|see notes)")
_NUMBER = re.compile(r"^\(?[$€£]?\s*\(?\d[\d,]*(\.\d+)?\)?%?$")
MAX_STATEMENT_PARTS: Final = 700
MIN_STATEMENT_NUMBERS: Final = 2


def _heading_kind(part: str) -> str | None:
    text = " ".join(part.lower().split())
    if len(text) > 120:
        return None
    text = _HEADING_PREFIX.sub("", text)
    for kind, pattern in _HEADINGS:
        if pattern.match(text):
            return kind
    return None


def is_equity_heading(row: str) -> bool:
    """The row opens the statement of changes in equity (plan step 26)."""
    return _heading_kind(row) == "equity"


def _join(parts: Sequence[str]) -> str:
    """Cells of a row separated by a space, rows by a newline (the extractor checks one row)."""
    lines = " ".join(parts).split(ROW_BREAK)
    return "\n".join(line.strip() for line in lines if line.strip())


def locate_statements(parts: Sequence[str]) -> list[str]:
    """One text per primary statement (income, balance sheet, cash flow) and the statement of
    changes in equity (plan step 26: only its retained earnings column is read) found in an HTML
    filing's visible text, in document order; empty when no statement heading is found.

    A heading starts a window that ends at the next heading, a "Notes to" line or a size cap.
    Of several windows of one kind (the contents list repeats the headings) the one with the
    most numbers wins, and it needs at least `MIN_STATEMENT_NUMBERS`. Comprehensive income
    statements are not pages. Deterministic: no model reads the filing to find them.
    """
    headings = [(i, k) for i, part in enumerate(parts) if (k := _heading_kind(part))]
    best: dict[str, tuple[int, int, int]] = {}  # kind -> (numbers, start, end)
    for n, (start, kind) in enumerate(headings):
        stop = headings[n + 1][0] if n + 1 < len(headings) else len(parts)
        end = min(stop, start + MAX_STATEMENT_PARTS)
        for i in range(start + 1, end):
            if _WINDOW_END.match(" ".join(parts[i].lower().split())):
                end = i
                break
        numbers = sum(1 for part in parts[start + 1 : end] if _NUMBER.match(part))
        if kind == "other" or numbers < MIN_STATEMENT_NUMBERS:
            continue
        if numbers > best.get(kind, (0, 0, 0))[0]:
            best[kind] = (numbers, start, end)
    ordered = sorted(best.values(), key=lambda v: v[1])
    return [_join(parts[start:end]) for _, start, end in ordered]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _doc_id(sha: str) -> str:
    return f"doc-{sha[:12]}"


def _xlsx_sheets(data: bytes) -> list[str]:
    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        sheets = []
        for ws in wb.worksheets:
            rows = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i >= MAX_SHEET_ROWS:
                    break
                rows.append("\t".join("" if v is None else str(v) for v in row).rstrip())
            sheets.append("\n".join(rows))
        return sheets or [""]
    finally:
        wb.close()


def _page_texts(kind: DocKind, data: bytes) -> list[str]:
    """Text per page (per sheet for xlsx, one page for xbrl); empty strings for a scan's pages."""
    if kind in ("text_pdf", "scan"):
        return pdf_page_texts(data)
    if kind == "xlsx":
        return _xlsx_sheets(data)
    parser = _Visible()
    parser.feed(data.decode("utf-8", errors="replace"))
    return locate_statements(parser.parts) or [_join(parser.parts)]


def _build(
    sha: str, kind: DocKind, texts: Sequence[str], numbers: Sequence[int] | None = None
) -> tuple[SourceDoc, list[DigitizedPage], list[OpenIssue]]:
    doc_id = _doc_id(sha)
    numbers = numbers or range(1, len(texts) + 1)
    if kind == "scan":
        # Nothing readable until the digitizer runs; the language gate runs again on its output.
        language, pages = UNDETERMINED, []
    else:
        language = detect_language("\n".join(texts))
        name, version = _READERS[kind]
        pages = [
            DigitizedPage(
                doc_id=doc_id, page=n, text=t, digitizer_name=name, digitizer_version=version
            )
            for n, t in zip(numbers, texts, strict=True)
        ]
    issues = [
        OpenIssue(
            code="instruction_in_document",
            where=f"{doc_id} p.{p.page}",
            detail=f"instruction-like text ({pattern}); treated as data, not followed",
        )
        for p in pages
        for pattern in find_instruction_like(p.text or "")
    ]
    doc = SourceDoc(
        doc_id=doc_id,
        kind=kind,
        sha256=sha,
        language=language,
        page_count=max([*numbers, 1]),
    )
    return doc, pages, issues


def ingest_documents(paths: Iterable[Path | str], library_root: Path | str) -> IngestResult:
    """Load each file; a file that cannot be used becomes an open issue and the rest continue."""
    root = Path(library_root).resolve()
    docs: list[SourceDoc] = []
    pages: list[DigitizedPage] = []
    issues: list[OpenIssue] = []
    seen: set[str] = set()
    for index, raw in enumerate(paths):
        where = f"file[{index}]"
        path = (root / raw).resolve()
        if not path.is_relative_to(root):
            issues.append(OpenIssue(code="path_outside_library", where=where, detail="skipped"))
            continue
        try:
            if not path.is_file():
                raise UnsupportedFile("not a file")
            if path.stat().st_size > MAX_FILE_BYTES:
                raise UnsupportedFile("file too large")
            data = path.read_bytes()
            sha = _sha256(data)
            if sha in seen:
                issues.append(OpenIssue(code="duplicate_document", where=where, detail="skipped"))
                continue
            kind = detect_kind(path, data)
            doc, doc_pages, doc_issues = _build(sha, kind, _page_texts(kind, data))
        except UnsupportedFile as exc:
            issues.append(OpenIssue(code="unsupported_file", where=where, detail=str(exc)))
            continue
        except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
            issues.append(OpenIssue(code="unreadable_file", where=where, detail=type(exc).__name__))
            continue
        seen.add(sha)
        docs.append(doc)
        pages.extend(doc_pages)
        issues.extend(doc_issues)
    return IngestResult(tuple(docs), tuple(pages), tuple(issues))


def split_request(text: str) -> tuple[str, str]:
    """Split a pasted request into the instruction text before the first page marker and the
    filing from that marker on; with no marker the whole text is the request and the filing is
    empty."""
    match = _PAGE_MARKER.search(text)
    if match is None:
        return text, ""
    return text[: match.start()], text[match.start() :]


def ingest_text(filing_text: str) -> IngestResult:
    """An in-memory text-layer document, as `run_agent(text)` supplies (plan step 10).

    Pages start at `--- page N ---` style markers (any word before the number). Text before the
    first marker is not part of the filing and is dropped, so the caller passes the filing only;
    with no marker at all the whole text is page 1.
    """
    parts = _PAGE_MARKER.split(filing_text)
    if len(parts) == 1:
        texts, numbers = [filing_text], [1]
    else:
        numbers = [int(n) for n in parts[1::2]]
        texts = [t.strip() for t in parts[2::2]]
    if numbers[0] < 1 or any(b <= a for a, b in zip(numbers, numbers[1:], strict=False)):
        raise ValueError("page markers must number pages from 1 in increasing order")
    doc, pages, issues = _build(_sha256(filing_text.encode("utf-8")), "text_pdf", texts, numbers)
    return IngestResult((doc,), tuple(pages), tuple(issues))
