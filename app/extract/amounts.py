"""Read a number as printed. Code does this, never the model: `(3,000)` is -3000."""

import re
from decimal import Decimal, InvalidOperation
from typing import Final

_DASHES: Final = {"-", "—", "–", "−"}
_SYMBOLS = re.compile(r"^[£$€]\s*|\s*[A-Z]{3}$")
_OPEN_GAP = re.compile(r"\(\s+")
_CLOSE_GAP = re.compile(r"\s+\)")
_SYMBOL_BEFORE_NUMBER = re.compile(r"(?<![\d,.])[£$€]\s*(?=[\d(\-−–—])")


def normalise_amount_text(text: str) -> str:
    """Print-layout noise only: `( 2,037 )` -> `(2,037)` and `$ 712` -> `712`. Digits, commas,
    brackets and signs are untouched, so a sign can neither appear nor disappear."""
    text = _OPEN_GAP.sub("(", _CLOSE_GAP.sub(")", text))
    return _SYMBOL_BEFORE_NUMBER.sub("", text)


def parse_amount(text: str) -> Decimal:
    """`(3,000)` -> -3000, `1,234.50` -> 1234.50, a lone dash -> 0; else `ValueError`."""
    s = _SYMBOLS.sub("", normalise_amount_text(text.strip())).strip()
    if s in _DASHES:
        return Decimal(0)
    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative, s = True, s[1:-1]
    elif s[:1] in _DASHES and len(s) > 1:
        negative, s = True, s[1:]
    if not re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?|\d+(\.\d+)?", s):
        raise ValueError("not a printed amount")
    try:
        value = Decimal(s.replace(",", ""))
    except InvalidOperation:
        raise ValueError("not a printed amount") from None
    return -value if negative else value


_SYMBOL_CURRENCY: Final = {"$": "USD", "£": "GBP", "€": "EUR"}
_CURRENCY_CODE = re.compile(
    r"\b(USD|CAD|AUD|NZD|HKD|SGD|CNY|RMB|JPY|CHF|EUR|GBP|INR|MXN|BRL|KRW|SEK|NOK|DKK)\b"
)


_OTHER_DOLLAR = re.compile(
    r"(?<![A-Za-z])[A-Za-z]{1,3}\$"
    r"|\b(canadian|australian|new zealand|hong kong|singapore)\b|\bcdn\b",
    re.IGNORECASE,
)


def currency_from_symbol(text: str) -> str | None:
    """The currency a page's symbol names, only when the page prints exactly one of `$` `£` `€`
    and no currency code or other-dollar marker (`C$`, `A$`, "Canadian dollars"). `$` alone is
    read as USD, though a code could say CAD or AUD. A rule for a model that found no currency,
    not a guess: the caller records an open issue."""
    found = {c for sym, c in _SYMBOL_CURRENCY.items() if sym in text}
    if len(found) != 1 or _CURRENCY_CODE.search(text) or _OTHER_DOLLAR.search(text):
        return None
    return found.pop()
