"""Plan step 22: a printed subtotal never lands on a grand-total line, and one total is never
counted twice. Labels and numbers are invented; no client data."""

import json
from decimal import Decimal

import pytest

from app.mapping import load_mapping_table, map_lines
from app.repair import build_spread
from app.state import RawLine, Usage, mapping_key
from app.tools import ModelReply, StructuredCaller, TokenBudget

PERIOD = "2026-06-30"


class _Client:
    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.calls = 0

    def complete(
        self, *, model: str, system: str, user: str, max_tokens: int | None = None
    ) -> ModelReply:
        self.calls += 1
        return ModelReply(text=self.reply, usage=Usage(input_tokens=1, output_tokens=0))


def _caller(client: _Client) -> StructuredCaller:
    return StructuredCaller(
        client=client, model="test/model-1", budget=TokenBudget(cap=10_000), prompt_version="v1"
    )


def _raw(rid: str, label: str, value: str, period: str = PERIOD) -> RawLine:
    return RawLine(
        raw_line_id=rid,
        doc_id="doc1",
        page=2,
        original_label=label,
        original_value_text=value,
        statement="BS",
        period=period,
        scale="millions",
        currency="USD",
    )


def _answer(*items: tuple[str, str]) -> str:
    return json.dumps(
        {
            "mappings": [
                {"key": k, "standard_line_id": i, "confidence": 0.9, "rationale": "fits"}
                for k, i in items
            ]
        }
    )


@pytest.mark.parametrize(
    ("label", "line_id"),
    [
        ("Total current liabilities", "bs_total_current_liabilities"),
        ("Total non-current liabilities", "bs_total_noncurrent_liabilities"),
        ("Total long-term liabilities", "bs_total_noncurrent_liabilities"),
        ("Total non-current assets", "bs_total_noncurrent_assets"),
    ],
)
def test_cr_step22_a_printed_subtotal_has_its_own_standard_line(label: str, line_id: str) -> None:
    client = _Client()
    result = map_lines([_raw("r1", label, "10")], load_mapping_table(), _caller(client))
    assert client.calls == 0
    assert result.mapping[mapping_key(PERIOD, "r1")].standard_line_id == line_id


def test_cr_step22_the_new_subtotal_lines_are_subtotals() -> None:
    kinds = {ln.line_id: ln.kind for ln in load_mapping_table().lines}
    for line_id in (
        "bs_total_current_liabilities",
        "bs_total_noncurrent_liabilities",
        "bs_total_noncurrent_assets",
    ):
        assert kinds[line_id] == "subtotal"


def test_cr_step22_a_subtotal_the_model_puts_on_a_grand_total_is_not_added_to_it() -> None:
    lines = [
        _raw("r1", "Total liabilities", "150"),
        _raw("r2", "Total obligations due within the year", "100"),
    ]
    client = _Client(_answer(("k1", "bs_total_liabilities")))
    result = map_lines(lines, load_mapping_table(), _caller(client))
    # The rule hit stays; the model's second claim on the same total is dropped and reported.
    assert mapping_key(PERIOD, "r1") in result.mapping
    assert mapping_key(PERIOD, "r2") not in result.mapping
    assert [(i.code, i.where) for i in result.open_issues] == [("duplicate_total_mapping", "r2")]
    spread = build_spread(lines, result.mapping)
    assert spread[PERIOD]["bs_total_liabilities"] == Decimal(150)


def test_cr_step22_the_same_total_printed_twice_with_one_value_is_not_an_issue() -> None:
    lines = [_raw("r1", "Total liabilities", "150"), _raw("r2", "Total liabilities", "150")]
    result = map_lines(lines, load_mapping_table(), _caller(_Client()))
    assert len(result.mapping) == 1
    assert result.open_issues == ()


def test_cr_step22_detail_lines_on_one_line_still_add_up() -> None:
    lines = [_raw("r1", "Accounts payable", "30"), _raw("r2", "Trade payables", "20")]
    result = map_lines(lines, load_mapping_table(), _caller(_Client()))
    assert len(result.mapping) == 2 and result.open_issues == ()
    assert build_spread(lines, result.mapping)[PERIOD]["bs_payables"] == Decimal(50)


def test_cr_step22_each_period_keeps_its_own_total() -> None:
    lines = [
        _raw("r1", "Total liabilities", "150"),
        _raw("r2", "Total liabilities", "140", period="2025-12-31"),
    ]
    result = map_lines(lines, load_mapping_table(), _caller(_Client()))
    assert len(result.mapping) == 2 and result.open_issues == ()


def test_cr_step22_total_equity_beats_equity_before_minority_interest_whatever_the_order() -> None:
    for first, second in (
        ("Total shareholders equity", "Total equity"),
        ("Total equity", "Total shareholders equity"),
    ):
        lines = [_raw("r1", first, "6100"), _raw("r2", second, "6115")]
        result = map_lines(lines, load_mapping_table(), _caller(_Client()))
        kept = [m.raw_line_id for m in result.mapping.values()]
        assert kept == [("r2" if second == "Total equity" else "r1")]
        assert [i.code for i in result.open_issues] == ["duplicate_total_mapping"]
