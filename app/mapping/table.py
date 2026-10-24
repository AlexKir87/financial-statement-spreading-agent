"""Loader for the editable mapping data (`mapping/standard_lines.csv`, `mapping/mapping_rules.csv`).

The standard line ids are the ones the reconciliation rules read (`app/reconcile/rules.py`).
Mapped values keep the sign the source prints: a cost or an outflow is negative, a total is
the plain sum of its components. The table never changes a sign.
"""

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from app.sections import SECTIONS
from app.state import LINE_ID_PATTERN, Statement

DEFAULT_DIR: Final = Path(__file__).resolve().parents[2] / "mapping"
STATEMENTS: Final[tuple[Statement, ...]] = ("IS", "BS", "CF")
EQUITY_COLUMN_LINE: Final = "bs_re_column_movements"
BELOW_NET_INCOME: Final = "below_net_income"  # the printed section, see `app/sections.py`
BELOW_NET_INCOME_LINE: Final = "is_below_net_income"
NON_OPERATING: Final = "non_operating"
NON_OPERATING_LINE: Final = "is_non_operating_items"
INTEREST_EXPENSE_LINE: Final = "is_interest_expense"
_PREFIX: Final[dict[str, str]] = {"IS": "is_", "BS": "bs_", "CF": "cf_"}


class MappingTableError(ValueError):
    """The mapping files are malformed (analyst edits are checked on load, not trusted)."""


def normalize_label(label: str) -> str:
    """Lower case, punctuation to spaces, whitespace collapsed: `Cash, net` == `cash net`."""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", label.lower()).split())


@dataclass(frozen=True)
class StandardLine:
    line_id: str
    statement: Statement
    label: str
    kind: str


@dataclass(frozen=True)
class MappingTable:
    lines: tuple[StandardLine, ...]
    rules: dict[tuple[str, str], str]  # (statement, normalized label) -> line id
    # (statement, section, normalized label) -> line id, for labels whose meaning depends on the
    # printed section ("Other" under investing activities)
    section_rules: dict[tuple[str, str, str], str] = field(default_factory=dict)

    def line_ids(self, statement: str | None = None) -> frozenset[str]:
        """All line ids, or (for one statement) those a model may propose: the equity column line
        is taken by equity statement rows only (plan step 26)."""
        return frozenset(
            ln.line_id
            for ln in self.lines
            if statement is None or (ln.statement == statement and ln.line_id != EQUITY_COLUMN_LINE)
        )

    def lookup(self, statement: str, label: str, section: str | None = None) -> str | None:
        """The line id a rule gives for this label on this statement, or None. A rule for the
        printed section wins over a rule for the label alone. Under `below_net_income` (plan
        step 27) every row is a memo line unless a rule names another memo line (per share); under
        `non_operating` every row is a non-operating item unless a rule names interest expense."""
        norm = normalize_label(label)
        if section is not None and (hit := self.section_rules.get((statement, section, norm))):
            return hit
        hit = self.rules.get((statement, norm))
        if statement == "IS" and section == BELOW_NET_INCOME and not self._is_memo(hit):
            return BELOW_NET_INCOME_LINE
        if statement == "IS" and section == NON_OPERATING and hit != INTEREST_EXPENSE_LINE:
            return NON_OPERATING_LINE
        return hit

    def _is_memo(self, line_id: str | None) -> bool:
        return line_id is not None and any(
            ln.line_id == line_id and ln.kind == "memo" for ln in self.lines
        )


def _rows(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != columns:
            raise MappingTableError(f"{path.name}: columns must be {', '.join(columns)}")
        return [{k: (v or "").strip() for k, v in row.items()} for row in reader]


def load_mapping_table(directory: Path | str = DEFAULT_DIR) -> MappingTable:
    base = Path(directory)
    lines: list[StandardLine] = []
    seen: set[str] = set()
    for row in _rows(base / "standard_lines.csv", ("line_id", "statement", "label", "kind")):
        line_id, statement = row["line_id"], row["statement"]
        if not re.fullmatch(LINE_ID_PATTERN, line_id):
            raise MappingTableError(f"standard_lines.csv: bad line id {line_id!r}")
        if statement not in STATEMENTS or not line_id.startswith(_PREFIX[statement]):
            raise MappingTableError(f"standard_lines.csv: {line_id} does not belong to {statement}")
        if line_id in seen:
            raise MappingTableError(f"standard_lines.csv: duplicate line id {line_id}")
        seen.add(line_id)
        lines.append(StandardLine(line_id, statement, row["label"], row["kind"]))
    rules: dict[tuple[str, str], str] = {}
    for row in _rows(base / "mapping_rules.csv", ("statement", "label", "standard_line_id")):
        statement, line_id = row["statement"], row["standard_line_id"]
        key = (statement, normalize_label(row["label"]))
        if statement not in STATEMENTS or not key[1]:
            raise MappingTableError(f"mapping_rules.csv: bad rule for {row['label']!r}")
        if line_id not in seen or not line_id.startswith(_PREFIX[statement]):
            raise MappingTableError(
                f"mapping_rules.csv: {line_id!r} is not a {statement} standard line id"
            )
        if rules.get(key, line_id) != line_id:
            raise MappingTableError(f"mapping_rules.csv: conflicting rules for {row['label']!r}")
        rules[key] = line_id
    section_rules: dict[tuple[str, str, str], str] = {}
    path = base / "section_rules.csv"
    if path.is_file():
        for row in _rows(path, ("statement", "section", "label", "standard_line_id")):
            statement, section, line_id = row["statement"], row["section"], row["standard_line_id"]
            key3 = (statement, section, normalize_label(row["label"]))
            if (
                statement not in STATEMENTS
                or section not in SECTIONS.get(statement, ())
                or not key3[2]
            ):
                raise MappingTableError(f"section_rules.csv: bad rule for {row['label']!r}")
            if line_id not in seen or not line_id.startswith(_PREFIX[statement]):
                raise MappingTableError(
                    f"section_rules.csv: {line_id!r} is not a {statement} standard line id"
                )
            if section_rules.get(key3, line_id) != line_id:
                raise MappingTableError(
                    f"section_rules.csv: conflicting rules for {row['label']!r} in {section}"
                )
            section_rules[key3] = line_id
    return MappingTable(tuple(lines), rules, section_rules)
