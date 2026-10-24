"""The digitizer interface: a page range in, cells and the digitizer's name and version out.

A route reads one page at a time and raises `PageUnreadable` when it cannot. `digitize_pages`
adds what is the same for every route: the page loop, `low_confidence_read` open issues,
`unreadable_page` issues, and `not_delivered_reason="unreadable_source"` when no page in the
range could be read. Text-layer PDFs and XBRL never come here (ingest reads them).
"""

from dataclasses import dataclass
from typing import Final, Protocol

from app.state import DigitizedPage, NotDeliveredReason, OpenIssue, Usage
from app.tools.model_client import InvalidModelOutput, TokenBudgetExceeded

# Placeholder, like the mapping threshold: set on real scans, not measured yet (plan step 1).
DEFAULT_MIN_CONFIDENCE: Final = 0.7


class PageUnreadable(Exception):
    """The route could not read this page. The message carries no page content."""


class DigitizerRoute(Protocol):
    name: str
    version: str

    @property
    def usage(self) -> Usage:
        """Tokens spent so far by this route (cumulative)."""
        ...

    def read_page(self, doc_id: str, page: int) -> DigitizedPage: ...


@dataclass(frozen=True)
class DigitizeResult:
    pages: tuple[DigitizedPage, ...]
    open_issues: tuple[OpenIssue, ...]
    usage: Usage
    not_delivered_reason: NotDeliveredReason | None = None


def digitize_pages(
    route: DigitizerRoute,
    doc_id: str,
    first_page: int,
    last_page: int,
    *,
    min_confidence: float = DEFAULT_MIN_CONFIDENCE,
) -> DigitizeResult:
    """Read pages `first_page..last_page` (1-based, inclusive) of one scanned document."""
    if first_page < 1 or last_page < first_page:
        raise ValueError("page range must satisfy 1 <= first_page <= last_page")
    before = route.usage
    pages: list[DigitizedPage] = []
    issues: list[OpenIssue] = []
    unreadable = 0
    capped = False
    for number in range(first_page, last_page + 1):
        where = f"{doc_id} p{number}"
        try:
            read = route.read_page(doc_id, number)
        except TokenBudgetExceeded:
            left = last_page - number + 1
            issues.append(
                OpenIssue(
                    code="token_cap_reached",
                    where=where,
                    detail=f"token cap reached; {left} page(s) not digitized",
                )
            )
            capped = True
            break
        except (PageUnreadable, InvalidModelOutput) as exc:
            unreadable += 1
            issues.append(OpenIssue(code="unreadable_page", where=where, detail=str(exc)[:300]))
            continue
        pages.append(read)
        for cell in read.cells:
            if cell.confidence is not None and cell.confidence < min_confidence:
                issues.append(
                    OpenIssue(
                        code="low_confidence_read",
                        where=f"{where} r{cell.row}c{cell.col}",
                        detail=f"confidence {cell.confidence:.2f} below {min_confidence:.2f}",
                    )
                )
    spent = Usage(
        input_tokens=route.usage.input_tokens - before.input_tokens,
        output_tokens=route.usage.output_tokens - before.output_tokens,
    )
    all_unreadable = not capped and not pages and unreadable > 0
    return DigitizeResult(
        pages=tuple(pages),
        open_issues=tuple(issues),
        usage=spent,
        not_delivered_reason="unreadable_source" if all_unreadable else None,
    )
