"""Detect what kind of source a file is: xbrl, text_pdf, scan or xlsx (the spec "Tools")."""

import io
import zipfile
from pathlib import Path

from pypdf import PdfReader

from app.state import DocKind

# A page counts as having a text layer when it yields at least this many non-space characters.
MIN_TEXT_CHARS_PER_PAGE = 20
_PDF_MAGIC = b"%PDF-"
_IXBRL_MARKERS = (b"ix:nonfraction", b"ix:nonnumeric", b"ix:header")
_XBRL_ROOT_MARKERS = (b"<xbrli:xbrl", b"<xbrl ", b"<xbrl>", b"<link:linkbase")
_SNIFF_BYTES = 1_000_000


class UnsupportedFile(ValueError):
    """The file is not one of the four supported kinds. The message carries no file content."""


def pdf_page_texts(data: bytes) -> list[str]:
    reader = PdfReader(io.BytesIO(data))
    if reader.is_encrypted:
        raise UnsupportedFile("encrypted pdf")
    return [page.extract_text() or "" for page in reader.pages]


def detect_kind(path: Path, data: bytes) -> DocKind:
    """Decide the kind from the content, with the extension only breaking ties for XML-ish files."""
    if data.startswith(_PDF_MAGIC):
        texts = pdf_page_texts(data)
        with_text = sum(1 for t in texts if len("".join(t.split())) >= MIN_TEXT_CHARS_PER_PAGE)
        # Mostly empty pages means the statements are images: send it to the digitizer.
        return "text_pdf" if texts and with_text * 2 >= len(texts) else "scan"
    if data.startswith(b"PK"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                if "xl/workbook.xml" in zf.namelist():
                    return "xlsx"
        except zipfile.BadZipFile as exc:
            raise UnsupportedFile("corrupt zip container") from exc
        raise UnsupportedFile("zip container is not an xlsx workbook")
    head = data[:_SNIFF_BYTES].lower()
    if path.suffix.lower() in {".htm", ".html", ".xhtml", ".xml", ".xbrl"} and (
        any(m in head for m in _IXBRL_MARKERS) or any(m in head for m in _XBRL_ROOT_MARKERS)
    ):
        return "xbrl"
    raise UnsupportedFile("not a pdf, xlsx or xbrl file")
