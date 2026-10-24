"""Printed section of a statement row (plan step 19), read from the page's own headings.

A label such as "Other" or "Deferred income taxes" means different things in different parts of
a statement, and the mapper sees labels only. The section is the last heading row printed above
the row: a row with no digits and a short text that names the section. Pure code, no model.
Nothing is guessed: a row above every heading, or a statement with no vocabulary, has no section.
An income statement subtotal (total revenues, gross profit, operating income) closes the section.

Sections: CF `operating`, `investing`, `financing`; IS `revenue`, `costs`, `costs_to_gross_profit`,
`per_share`, `share_count`, `below_net_income`, `non_operating`; BS `assets`, `liabilities`,
`equity`. The two IS cost sections tell a cost block that a printed gross profit line follows
(`costs_to_gross_profit`, the cost of sales block, plan step 21) from one that does not (`costs`).
`per_share` and `share_count` hold the per-share figures and share counts printed under their own
headings (plan step 25). `below_net_income` holds the rows printed after the net income row
(comprehensive income, the share of noncontrolling interests, preferred dividends) until a
per-share heading, and `non_operating` the rows between the operating profit row and the profit
before tax row (plan step 27). `operating_block` holds the rows between gross profit and operating
profit, and the CF `cash_summary` the rows after the net cash from financing row (step 27).
"""

import re
from typing import Final

MAX_HEADING_CHARS: Final = 80
SECTIONS: Final = {
    "CF": ("operating", "investing", "financing", "cash_summary"),
    "IS": (
        "revenue",
        "costs",
        "costs_to_gross_profit",
        "per_share",
        "share_count",
        "below_net_income",
        "non_operating",
        "operating_block",
    ),
    "BS": ("assets", "liabilities", "equity"),
}
# a dash cell at the end of a row stands for a value, so the row is data, not a heading
_PLACEHOLDER: Final = re.compile(r"(?<!\S)[-−–—]+(?:\s+[-−–—]+)*\s*$")
_DIGIT: Final = re.compile(r"\d")

# a cash flow heading is only the activities words (with "cash flows from"), so a wrapped label
# that merely ends in "financing activities" does not open a section
_CF_HEADING: Final = r"^(cash flows?\W*)?(from |used in |provided by )?{} activities\W*$"

# (pattern, section), first match wins; patterns are matched against the lower-cased heading
_HEADINGS: Final[dict[str, tuple[tuple[re.Pattern[str], str], ...]]] = {
    "CF": (
        (re.compile(_CF_HEADING.format("operating")), "operating"),
        (re.compile(_CF_HEADING.format("investing")), "investing"),
        (re.compile(_CF_HEADING.format("financing")), "financing"),
    ),
    "IS": (
        (re.compile(r"^(total )?(net )?(sales|revenues?)( and other income)?:?$"), "revenue"),
        (re.compile(r"^(operating )?(costs?( and expenses)?|expenses)( and other)?:?$"), "costs"),
        (
            re.compile(r"^cost(s)? of (sales|revenues?|goods sold|products? sold)( and .*)?:?$"),
            "costs",
        ),
        (
            re.compile(
                r"^(net )?(earnings|income|loss)( \(loss\))? per (common )?share( data)?:?$"
            ),
            "per_share",
        ),
        (
            re.compile(r"^weighted[- ]average (number of )?(common )?shares( outstanding)?:?$"),
            "share_count",
        ),
    ),
    "BS": (
        (
            re.compile(r"\bliabilities and (stockholders|shareholders|owners)?.{0,3}\s?equity\b"),
            "liabilities",
        ),
        (re.compile(r"\bliabilities\b"), "liabilities"),
        (re.compile(r"\b(stockholders|shareholders|owners)?.{0,3}\s?(equity|deficit)\b"), "equity"),
        (re.compile(r"\bassets\b"), "assets"),
    ),
}


# An income statement subtotal closes the section above it: what follows is neither revenue nor
# the cost block, so it has no section.
_IS_CLOSING: Final = re.compile(
    r"^(total( net| operating)?( sales| revenues?)?|revenues?, net|gross (profit|margin)"
    r"|(income|loss) from operations|operating (income|profit|loss)"
    r"|total (operating )?(costs?|expenses)( and expenses)?|(income|earnings|loss) before)\b"
)


# the printed net income row: the label ends at "net income", "net earnings" or "net loss", so a
# row that names a group ("including noncontrolling interests", "attributable to ...") is not it
_NET_INCOME_ROW: Final = re.compile(
    r"^net (income|earnings|loss)( \((income|earnings|loss)\))?(?=\s*[$\d(\-−–—])"
)

_OPERATING_PROFIT_ROW: Final = re.compile(
    r"^((income|loss|earnings)(\s*/?\s*\((income|loss|earnings)\))? from operations"
    r"|operating (income|profit|loss))\b"
)
# a row that ends the non-operating block: profit before tax, a tax row, or net income itself
_NON_OPERATING_END: Final = re.compile(
    r"^(.*\bbefore\b.*\btax|pre-?tax\b|(provision for |benefit from )?(income )?tax"
    r"|net (income|earnings|loss)\b)"
)

# a printed total of operating costs: whatever sits between it and operating profit is operating
_COSTS_TOTAL_ROW: Final = re.compile(r"^total (operating )?(costs?( and expenses)?|expenses)\b")
_NET_FINANCING_ROW: Final = re.compile(r"^net cash\b.*\bfinancing activities")

_GROSS_PROFIT: Final = re.compile(r"^gross (profit|margin)\b")


def _split_cost_blocks(rows: list[str], sections: list[str | None]) -> None:
    """Rename a `costs` run to `costs_to_gross_profit` when gross profit ends it or comes next."""
    i = 0
    while i < len(sections):
        if sections[i] != "costs":
            i += 1
            continue
        end = i
        while end + 1 < len(sections) and sections[end + 1] == "costs":
            end += 1
        after = next((_squash(r) for r in rows[end + 1 :] if r.strip()), "")
        if _GROSS_PROFIT.match(after) or _GROSS_PROFIT.match(_squash(rows[end])):
            for k in range(i, end + 1):
                sections[k] = "costs_to_gross_profit"
        i = end + 1


def _squash(text: str) -> str:
    return " ".join(text.lower().split())


def heading_section(statement: str, row: str) -> str | None:
    """The section a heading row opens, or `None` when the row is not a heading."""
    text = _squash(row)
    if not text or _DIGIT.search(text) or _PLACEHOLDER.search(text):
        return None
    if len(text) > MAX_HEADING_CHARS:
        return None
    for pattern, section in _HEADINGS.get(statement, ()):
        if pattern.search(text):
            return section
    return None


def row_sections(statement: str, rows: list[str]) -> list[str | None]:
    """For each row, the section of the last heading at or above it."""
    current: str | None = None
    out: list[str | None] = []
    for row in rows:
        current = heading_section(statement, row) or current
        squashed = _squash(row)
        if statement == "IS" and current == "non_operating" and _NON_OPERATING_END.match(squashed):
            current = None  # a subtotal or tax row is not a non-operating item
        if (
            statement == "IS"
            and current == "operating_block"
            and (
                _OPERATING_PROFIT_ROW.match(squashed)
                or _NON_OPERATING_END.match(squashed)
                or _GROSS_PROFIT.match(squashed)
            )
        ):
            current = None
        out.append(current)
        if statement == "CF" and _NET_FINANCING_ROW.match(squashed):
            current = "cash_summary"
        if statement == "IS" and current != "below_net_income":
            if _IS_CLOSING.match(squashed):
                current = None
            if _GROSS_PROFIT.match(squashed) or _COSTS_TOTAL_ROW.match(squashed):
                current = "operating_block"
            if _OPERATING_PROFIT_ROW.match(squashed):
                current = "non_operating"
            if _NET_INCOME_ROW.match(squashed):
                current = "below_net_income"
    if statement == "IS":
        _split_cost_blocks(rows, out)
    return out
