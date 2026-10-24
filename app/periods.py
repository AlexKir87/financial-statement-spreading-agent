"""Canonical period keys, computed in code from a printed column heading (plan step 15).

A balance sheet date is `YYYY-MM-DD`; an income statement or cash flow column is
`START..END`. The start is the end date minus the stated months or weeks, plus one day, so
the same heading on two statements gives the same key and the checks can pair them. A heading
that cannot be read gives `None`; nothing is guessed, and the caller records an open issue.
"""

import calendar
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

_MONTHS: Final = {
    name: i
    for i, names in enumerate(
        [
            ("jan", "january"),
            ("feb", "february"),
            ("mar", "march"),
            ("apr", "april"),
            ("may",),
            ("jun", "june"),
            ("jul", "july"),
            ("aug", "august"),
            ("sep", "sept", "september"),
            ("oct", "october"),
            ("nov", "november"),
            ("dec", "december"),
        ],
        start=1,
    )
    for name in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE = (
    rf"(?P<month>{_MONTH})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?\s*,?\s*"
    r"(?P<year>(?:19|20)\d{2})"
)
_UNITS: Final = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}
_TENS: Final = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50}
_WORD_NUM = rf"(?:(?:{'|'.join(_TENS)})(?:[- ](?:{'|'.join(_UNITS)}))?|{'|'.join(_UNITS)})"
_NUM = rf"(?<![\w-])(?:{_WORD_NUM}|\d{{1,2}})(?![\w])"
_UNIT = r"(?P<unit>months?|quarters?|weeks?)"
_COUNTS = (
    rf"(?P<count>{_NUM})(?:\s*[- ]?\s*(?:months?|quarters?|weeks?))?"
    rf"(?:\s+and\s+(?P<count2>{_NUM}))?"
)
_SPAN = re.compile(
    rf"{_COUNTS}\s*[- ]?\s*{_UNIT}\s+ended\s+{_DATE}",
    re.IGNORECASE,
)
_QUARTER = re.compile(rf"\b(?P<unit>quarters?)\s+ended\s+{_DATE}", re.IGNORECASE)
_YEAR = re.compile(rf"\b(?:fiscal\s+)?years?\s+ended\s+{_DATE}", re.IGNORECASE)
_NUMBER_BEFORE = re.compile(
    rf"(?:\bhalf|\bhundred|(?<!\d)\d{{1,2}}|\b(?:{'|'.join([*_TENS, *_UNITS])}))[\s-]*$",
    re.IGNORECASE,
)
_TENS_BEFORE = re.compile(rf"\b(?:{'|'.join(_TENS)}|hundred)\W*$", re.IGNORECASE)
_UNIT_COUNT = re.compile(rf"{_COUNTS}\s*[- ]?\s*{_UNIT}|year\s+to\s+date", re.IGNORECASE)
_INSTANT = re.compile(rf"^\s*{_DATE}\s*$", re.IGNORECASE)
_PHRASE = re.compile(
    rf"(?:{_COUNTS}\s*[- ]?\s*{_UNIT}|(?P<qtr>\bquarters?)|(?P<year>\b(?:fiscal\s+)?years?))"
    r"\s+ended\s+"
    rf"(?P<month>{_MONTH})\.?\s+(?P<day>\d{{1,2}})",
    re.IGNORECASE,
)
_BARE_YEAR = re.compile(r"^\s*((?:19|20)\d{2})\s*$")
_KEY = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\.\.(\d{4}-\d{2}-\d{2}))?$")


@dataclass(frozen=True)
class Span:
    start: date | None  # None for a balance sheet date
    end: date

    @property
    def key(self) -> str:
        if self.start is None:
            return self.end.isoformat()
        return f"{self.start.isoformat()}..{self.end.isoformat()}"

    @property
    def days(self) -> int:
        return 0 if self.start is None else (self.end - self.start).days + 1


_DASHES_UNICODE = "\u2010\u2011\u2012\u2013\u2014\u2212\ufe58\ufe63\uff0d\u2043"
_SPACES_UNICODE = "\u00a0\u2009\u202f"


def _clean(text: str) -> str:
    """Every dash character as an ASCII hyphen, odd spaces as a space, a soft hyphen dropped,
    and "thirty - nine" or a wrapped "thirty-\nnine" as "thirty-nine"."""
    for d in _DASHES_UNICODE:
        text = text.replace(d, "-")
    for sp in _SPACES_UNICODE:
        text = text.replace(sp, " ")
    text = text.replace("\u00ad", "")
    return re.sub(rf"\b({'|'.join(_TENS)})\s*-\s*(?=\w)", r"\1-", text, flags=re.IGNORECASE)


def _as_date(month: str, day: str, year: str) -> date | None:
    try:
        return date(int(year), _MONTHS[month.lower()], int(day))
    except (ValueError, KeyError):
        return None


def _months_before(end: date, months: int) -> date:
    """`end` moved back by `months`; a month-end stays a month-end."""
    total = end.year * 12 + (end.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    if end.day == calendar.monthrange(end.year, end.month)[1]:
        return date(year, month, last)
    return date(year, month, min(end.day, last))


def _duration(end: date, count: int, unit: str) -> Span:
    unit = unit.lower().rstrip("s")
    if unit == "week":
        return Span(end - timedelta(days=7 * count) + timedelta(days=1), end)
    months = count * 3 if unit == "quarter" else count
    return Span(_months_before(end, months) + timedelta(days=1), end)


def _count(raw: str | None) -> int | None:
    """A number printed as digits or words ("39", "thirty-nine"); `None` if it is neither."""
    if raw is None:
        return None
    word = raw.lower().strip()
    if word.isdigit():
        return int(word) if 1 <= int(word) <= 60 else None
    parts = re.split(r"[- ]+", word)
    if len(parts) == 1 and parts[0] in _UNITS:
        return _UNITS[parts[0]]
    if parts[0] in _TENS and len(parts) <= 2:
        extra = _UNITS.get(parts[1]) if len(parts) == 2 else 0
        return None if extra is None else _TENS[parts[0]] + extra
    return None


def _named(text: str, m: re.Match[str]) -> bool:
    """False when the number before the match is part of a longer one ("thirty" before "nine",
    "five" before "years", "half" before "year")."""
    return not _NUMBER_BEFORE.search(text[: m.start()])


def parse_period(label: str) -> Span | None:
    """The span a full heading names, or `None`. A bare year or `FY2023` is not enough, and
    a heading that names more than one period (a joined spanning header) is not read."""
    label = _clean(label)
    found = [m for m in _SPAN.finditer(label)]
    found += [m for m in _QUARTER.finditer(label) if _named(label, m)]
    found += [m for m in _YEAR.finditer(label) if _named(label, m)]
    if len(found) > 1:
        return None
    if found:
        m = found[0]
        end = _as_date(m["month"], m["day"], m["year"])
        if end is None:
            return None
        if m.re is _SPAN:
            count = _count(m["count"])
            if m["count2"] is not None or count is None:
                return None
            if _TENS_BEFORE.search(label[: m.start("count")]):
                return None
            return _duration(end, count, m["unit"])
        return _duration(end, 1, "quarter") if m.re is _QUARTER else _duration(end, 12, "month")
    if inst := _INSTANT.match(label):
        end = _as_date(inst["month"], inst["day"], inst["year"])
        return None if end is None else Span(None, end)
    return None


def _phrases(page_text: str) -> set[tuple[int, str, str, str]]:
    """Every "<n> months ended <month> <day>" phrase of a page as (count, unit, month, day).
    "three and nine months ended" is two phrases; "quarter ended" is one quarter; "year ended"
    is twelve months. A page where some "ended <month>" is not covered by a phrase we can read
    is ambiguous: the result then holds a (0, ...) marker so nothing is rescued."""
    page_text = _clean(page_text)
    found: set[tuple[int, str, str, str]] = set()
    matches = list(_PHRASE.finditer(page_text))
    ambiguous = {(0, "", "", "")}
    # every "ended" and every "<n> months" or "year to date" must sit in a phrase we read
    if len(matches) < len(re.findall(r"\bended\b", page_text, re.IGNORECASE)):
        return ambiguous
    if len(list(_UNIT_COUNT.finditer(page_text))) > sum(1 for m in matches if m["unit"]):
        return ambiguous
    for m in matches:
        month, day = m["month"].lower(), m["day"]
        if m["year"]:
            if not _named(page_text, m):
                return ambiguous
            found.add((12, "month", month, day))
        elif m["qtr"]:
            found.add((1, "quarter", month, day))
        else:
            unit = m["unit"].lower().rstrip("s")
            for raw in (m["count"], m["count2"]):
                if raw is not None:
                    count = _count(raw)
                    if count is None or _TENS_BEFORE.search(page_text[: m.start("count")]):
                        return ambiguous
                    found.add((count, unit, month, day))
    return found


def _page_phrase(page_text: str) -> tuple[int, str, str, str] | None:
    """The one phrase of a page, or `None` when it has none or several (an income statement
    that prints a quarter and a year-to-date block is ambiguous)."""
    found = _phrases(page_text)
    if len(found) != 1:
        return None
    phrase = next(iter(found))
    return None if phrase[0] == 0 else phrase


def _end_in_year(page_text: str, phrase: tuple[int, str, str, str], year: int) -> date | None:
    """The period end for a bare year: the phrase's month and day in that year. For a phrase
    counting weeks (a 52-week calendar ends on different days in different years) a full date of
    that year printed within 80 characters of the phrase and within a week of it is used
    instead; two such dates disagree: `None`."""
    own = _as_date(phrase[2], phrase[3], str(year))
    start = _PHRASE.search(page_text)
    if own is None or start is None or phrase[1] != "week":
        return own
    near = {
        found
        for m in re.finditer(_DATE, page_text[start.start() : start.start() + 80], re.IGNORECASE)
        if (found := _as_date(m["month"], m["day"], m["year"])) is not None
        and found.year == year
        and abs((found - own).days) <= 7
    }
    if len(near) > 1:
        return None
    return next(iter(near)) if near else own


def canonical_period(label: str, page_text: str = "", statement: str | None = None) -> str | None:
    """The key for a printed heading.

    A bare year ("2026") takes the page's own "<n> months ended <month> <day>" phrase, because
    the column heading often prints the phrase once above the years: a duration on an income
    statement or cash flow, a plain date on a balance sheet. A plain date on an income statement
    or cash flow line ("June 27, 2026") is a duration whose length is that phrase. All of these
    need exactly one such phrase on the page; otherwise `None`.
    """
    label, page_text = _clean(label), _clean(page_text)
    span = parse_period(label)
    if span is not None and not (span.start is None and statement in ("IS", "CF", "SE")):
        # a heading the model wrote out in full must name a year the page prints
        if page_text and not re.search(rf"\b{span.end.year}\b", page_text):
            return None
        return span.key
    phrase = _page_phrase(page_text)
    if phrase is None:
        return None
    if span is not None:
        return _duration(span.end, phrase[0], phrase[1]).key
    bare = _BARE_YEAR.match(label)
    if not bare:
        return None
    end = _end_in_year(page_text, phrase, int(bare[1]))
    if end is None:
        return None
    return Span(None, end).key if statement == "BS" else _duration(end, phrase[0], phrase[1]).key


def looks_like_a_date(label: str) -> bool:
    """A heading that names a month or says "ended", or is a bare year: one that should have
    resolved. `FY2023` or `Current` are labels, not dates; they stay as printed without an issue."""
    return bool(
        _BARE_YEAR.match(label) or re.search(rf"\b(?:{_MONTH})\b|\bended\b", label, re.IGNORECASE)
    )


def parse_key(key: str) -> Span | None:
    """The span of a canonical key; `None` for any other period label."""
    m = _KEY.match(key)
    if not m:
        return None
    try:
        end = date.fromisoformat(m[2] or m[1])
        start = date.fromisoformat(m[1]) if m[2] else None
    except ValueError:
        return None
    return Span(start, end)


def sort_key(key: str) -> tuple[date, int]:
    """Oldest end date first; on the same date the longer span first. Keys must parse."""
    span = parse_key(key)
    if span is None:
        raise ValueError(f"not a canonical period key: {key!r}")
    return span.end, -span.days


def all_canonical(keys: Sequence[str]) -> bool:
    return bool(keys) and all(parse_key(k) is not None for k in keys)


# How far a prior balance sheet date may sit from the day before a duration starts: filings on
# a 52/53-week calendar do not end exactly on the calendar date the months suggest.
PRIOR_BALANCE_WINDOW_DAYS: Final = 7


def prior_balance_date(duration: Span, instants: list[date]) -> date | None:
    """The balance sheet date that closes the period before `duration`, if one was printed."""
    if duration.start is None:
        return None
    target = duration.start - timedelta(days=1)
    near = [d for d in instants if abs((d - target).days) <= PRIOR_BALANCE_WINDOW_DAYS]
    return min(near, key=lambda d: abs((d - target).days)) if near else None
