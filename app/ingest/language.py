"""Language gate: the pilot reads English-language filings only (the spec, Failure modes).

This is a heuristic, not a language identifier: it checks the script first, then how much of the
text is common English or English financial vocabulary. A statement full of numbers still carries
enough labels ("Total assets", "Net income") to clear the bar. Its known limit: a Latin-script
filing in another language that reuses many English words could pass; the analyst sees the
language in the workbook's source list.
"""

import re
import unicodedata
from typing import Final

ENGLISH: Final = "en"
UNDETERMINED: Final = "und"

# Fewer alphabetic words than this and the text cannot be judged; the answer is `und`.
MIN_WORDS = 10
MIN_ENGLISH_SHARE = 0.2
MAX_NON_LATIN_SHARE = 0.3

_ENGLISH_WORDS: Final = frozenset(
    """the of and to in for on at by from with as is are was were be been or not a an this that
    total assets liabilities equity revenue revenues sales cost income expense expenses profit
    loss net tax taxes cash flow flows balance sheet statement statements consolidated current
    non property plant equipment inventory inventories receivables payables trade borrowings
    share capital retained earnings reserves operating investing financing activities interest
    depreciation amortisation amortization gross before after year years note notes thousands
    millions continuing discontinued dividends paid received increase decrease change""".split()
)
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def _script(ch: str) -> str:
    try:
        return unicodedata.name(ch).split(" ", 1)[0]
    except ValueError:
        return "UNKNOWN"


def detect_language(text: str) -> str:
    """`en`, `und` (cannot tell) or `und-<Script>` for a text written mostly in another script."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return UNDETERMINED
    scripts: dict[str, int] = {}
    for ch in letters:
        name = _script(ch)
        scripts[name] = scripts.get(name, 0) + 1
    non_latin = {s: n for s, n in scripts.items() if s != "LATIN"}
    if sum(non_latin.values()) / len(letters) > MAX_NON_LATIN_SHARE:
        top = max(non_latin, key=lambda s: non_latin[s])
        return f"und-{top.capitalize()}"
    words = [w.lower() for w in _WORD.findall(text)]
    if len(words) < MIN_WORDS:
        return UNDETERMINED
    share = sum(1 for w in words if w in _ENGLISH_WORDS) / len(words)
    return ENGLISH if share >= MIN_ENGLISH_SHARE else "und-Latin"


def is_supported_language(language: str) -> bool:
    """`und` passes: nothing can be said yet (a scan before it is digitized)."""
    return language in (ENGLISH, UNDETERMINED)
