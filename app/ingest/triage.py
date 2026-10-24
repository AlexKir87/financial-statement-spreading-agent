"""Triage: decide whether the run goes on or ends `not_delivered` before any model is called."""

import re
from dataclasses import dataclass
from typing import Final

from app.ingest.language import is_supported_language
from app.ingest.loader import IngestResult
from app.state import NotDeliveredReason, OpenIssue

# Phase-two work (the spec scope): EBITDA normalisation, QoE-lite, KPIs. Asking for it is a
# request this pilot does not serve, however good the filing is.
_OUT_OF_SCOPE: Final = re.compile(
    r"\bebitda\b|\bqoe\b|quality\s+of\s+earnings|\bkpis?\b|\badd[\s-]?backs?\b", re.IGNORECASE
)


@dataclass(frozen=True)
class TriageResult:
    not_delivered_reason: NotDeliveredReason | None
    open_issues: tuple[OpenIssue, ...] = ()

    @property
    def proceed(self) -> bool:
        return self.not_delivered_reason is None


def triage(request_text: str, ingested: IngestResult) -> TriageResult:
    """First reason that applies wins: scope, then language, then nothing readable at all."""
    if _OUT_OF_SCOPE.search(request_text):
        return TriageResult("out_of_scope")
    unsupported = [d for d in ingested.documents if not is_supported_language(d.language)]
    if unsupported:
        issues = tuple(
            OpenIssue(
                code="unsupported_language",
                where=d.doc_id,
                detail=f"detected {d.language}; English-language filings only",
            )
            for d in unsupported
        )
        return TriageResult("unsupported_language", issues)
    if not ingested.documents:
        return TriageResult("unreadable_source")
    return TriageResult(None)
