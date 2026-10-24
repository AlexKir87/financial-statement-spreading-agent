"""Plan step 23: a printed "Total ..." line is not added to the parts it sums.
Labels and numbers are invented; no client data."""

import json
import re
from decimal import Decimal

from app.mapping import load_mapping_table, map_lines
from app.repair import build_spread
from app.state import RawLine, Usage, mapping_key
from app.tools import ModelReply, StructuredCaller, TokenBudget

PERIOD = "2026-01-01..2026-06-30"


class _Client:
    def __init__(self) -> None:
        self.calls = 0

    def complete(
        self, *, model: str, system: str, user: str, max_tokens: int | None = None
    ) -> ModelReply:
        self.calls += 1
        keys = re.findall(r"^(k\d+) \|", user, re.M)
        text = json.dumps(
            {
                "mappings": [
                    {
                        "key": k,
                        "standard_line_id": "is_revenue",
                        "confidence": 0.9,
                        "rationale": "x",
                    }
                    for k in keys
                ]
            }
        )
        return ModelReply(text=text, usage=Usage(input_tokens=1, output_tokens=0))


def _caller() -> StructuredCaller:
    return StructuredCaller(
        client=_Client(), model="test/model-1", budget=TokenBudget(cap=10_000), prompt_version="v1"
    )


def _raw(rid: str, label: str, value: str, period: str = PERIOD) -> RawLine:
    return RawLine(
        raw_line_id=rid,
        doc_id="doc1",
        page=1,
        original_label=label,
        original_value_text=value,
        statement="IS",
        period=period,
        scale="millions",
        currency="USD",
    )


def test_cr_step23_total_sales_is_not_added_to_its_parts() -> None:
    lines = [
        _raw("r1", "Product sales", "40"),
        _raw("r2", "Service sales", "10"),
        _raw("r3", "Total revenues", "50"),
    ]
    result = map_lines(lines, load_mapping_table(), _caller())
    assert mapping_key(PERIOD, "r3") not in result.mapping
    assert [(i.code, i.where) for i in result.open_issues] == [("printed_total_not_added", "r3")]


def test_cr_step23_the_parts_add_up_to_the_printed_total() -> None:
    lines = [
        _raw("r1", "Product sales", "40"),
        _raw("r2", "Service sales", "10"),
        _raw("r3", "Total revenues", "50"),
    ]
    result = map_lines(lines, load_mapping_table(), _caller())
    assert build_spread(lines, result.mapping)[PERIOD]["is_revenue"] == Decimal(50)


def test_cr_step23_a_total_alone_on_its_line_is_kept() -> None:
    lines = [_raw("r1", "Total revenues", "50")]
    result = map_lines(lines, load_mapping_table(), _caller())
    assert mapping_key(PERIOD, "r1") in result.mapping
    assert result.open_issues == ()


def test_cr_step23_each_period_is_judged_on_its_own() -> None:
    other = "2025-01-01..2025-06-30"
    lines = [
        _raw("r1", "Product sales", "40"),
        _raw("r2", "Total revenues", "40"),
        _raw("r3", "Total revenues", "35", period=other),
    ]
    result = map_lines(lines, load_mapping_table(), _caller())
    assert mapping_key(PERIOD, "r2") not in result.mapping
    assert mapping_key(other, "r3") in result.mapping


def _bs(rid: str, label: str, value: str) -> RawLine:
    return _raw(rid, label, value).model_copy(update={"statement": "BS", "period": "2026-06-30"})


def _ppe_answer(*keys: str) -> _Client:
    class _Ppe(_Client):
        def complete(self, **kwargs):  # type: ignore[no-untyped-def]
            user = kwargs["user"]
            ks = re.findall(r"^(k\d+) \|", user, re.M)
            text = json.dumps(
                {
                    "mappings": [
                        {
                            "key": k,
                            "standard_line_id": "bs_ppe",
                            "confidence": 0.9,
                            "rationale": "x",
                        }
                        for k in ks
                    ]
                }
            )
            return ModelReply(text=text, usage=Usage(input_tokens=1, output_tokens=0))

    return _Ppe()


def _ppe_caller() -> StructuredCaller:
    return StructuredCaller(
        client=_ppe_answer(),
        model="test/model-1",
        budget=TokenBudget(cap=10_000),
        prompt_version="v1",
    )


def test_cr_step23_net_property_is_not_added_to_cost_and_depreciation() -> None:
    lines = [
        _bs("r1", "Gross plant and equipment", "49,943"),
        _bs("r2", "Accumulated depreciation", "( 21,464 )"),
        _bs("r3", "Net plant and equipment", "28,479"),
    ]
    result = map_lines(lines, load_mapping_table(), _ppe_caller())
    assert mapping_key("2026-06-30", "r3") not in result.mapping
    assert [(i.code, i.where) for i in result.open_issues] == [("printed_total_not_added", "r3")]
    assert build_spread(lines, result.mapping)["2026-06-30"]["bs_ppe"] == Decimal(28479)


def test_cr_step23_two_equal_lines_are_not_a_subtotal() -> None:
    lines = [_bs("r1", "Land", "100"), _bs("r2", "Buildings", "100")]
    result = map_lines(lines, load_mapping_table(), _ppe_caller())
    assert len(result.mapping) == 2 and result.open_issues == ()


def test_cr_step23_lines_that_do_not_add_up_are_all_kept() -> None:
    lines = [_bs("r1", "Land", "100"), _bs("r2", "Buildings", "50"), _bs("r3", "Equipment", "70")]
    result = map_lines(lines, load_mapping_table(), _ppe_caller())
    assert len(result.mapping) == 3 and result.open_issues == ()
