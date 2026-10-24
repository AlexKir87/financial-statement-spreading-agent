import json
from collections import Counter
from pathlib import Path

from app.mapping.table import load_mapping_table

GOLD = Path(__file__).resolve().parent.parent / "evals" / "gold" / "mapping_gold.jsonl"


def load() -> list[dict[str, object]]:
    return [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line]


def test_gold_ids_exist_for_their_statement() -> None:
    table = load_mapping_table()
    for row in load():
        assert row["standard_line_id"] in table.line_ids(str(row["statement"])), row


def test_gold_has_two_companies_with_all_three_statements() -> None:
    rows = load()
    assert {r["company"] for r in rows} == {"gold-a", "gold-b"}
    for company in ("gold-a", "gold-b"):
        assert {r["statement"] for r in rows if r["company"] == company} == {"IS", "BS", "CF"}


def test_gold_rows_are_unique_per_statement_position() -> None:
    counts = Counter((r["company"], r["statement"], r["row"]) for r in load())
    assert [k for k, n in counts.items() if n > 1] == []


def test_gold_holds_only_ids_and_digit_free_labels() -> None:
    rows = load()
    assert len(rows) == 120  # owner-confirmed count; a change needs the owner's confirmation
    for row in rows:
        assert set(row) == {"company", "statement", "row", "label", "standard_line_id"}
        assert row["company"] in {"gold-a", "gold-b"}
        assert row["statement"] in {"IS", "BS", "CF"}
        assert isinstance(row["row"], int) and row["row"] > 0
        label = str(row["label"])
        assert label.strip() != ""
        assert not any(ch.isdigit() for ch in label), row
