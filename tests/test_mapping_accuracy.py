import json
from pathlib import Path

import pytest

from app.state import MappedLine, RawLine, mapping_key
from evals.mapping_accuracy import (
    AccuracyError,
    MappedRow,
    Sample,
    SampleLine,
    check_is_the_recorded_draw,
    draw_sample,
    main,
    mapped_rows,
    score,
)

IDS = {"IS": "is_revenue", "BS": "bs_cash", "CF": "cf_net_income"}


def rows(company: str, n_is: int = 30, n_bs: int = 30, n_cf: int = 20) -> list[MappedRow]:
    out: list[MappedRow] = []
    for statement, n in (("IS", n_is), ("BS", n_bs), ("CF", n_cf)):
        for i in range(n):
            out.append(
                MappedRow(
                    company=company,
                    key=f"FY2025|{statement.lower()}-{i:03d}",
                    statement=statement,  # type: ignore[arg-type]
                    label=f"Label {statement} {i}",
                    standard_line_id=IDS[statement],
                )
            )
    return out


def split(sample: Sample, company: str) -> dict[str, int]:
    counts: dict[str, int] = {"IS": 0, "BS": 0, "CF": 0}
    for ln in sample.lines:
        if ln.company == company:
            counts[ln.statement] += 1
    return counts


def test_same_seed_same_sample_and_input_order_does_not_matter() -> None:
    data = rows("c01") + rows("c02")
    a = draw_sample(data, seed=7)
    b = draw_sample(list(reversed(data)), seed=7)
    assert a == b
    assert a.seed == 7


def test_different_seed_differs() -> None:
    data = rows("c01")
    assert draw_sample(data, seed=7).lines != draw_sample(data, seed=8).lines


def test_split_is_15_15_10_per_company_and_40_in_total() -> None:
    sample = draw_sample(rows("c01") + rows("c02"), seed=1)
    for company in ("c01", "c02"):
        assert split(sample, company) == {"IS": 15, "BS": 15, "CF": 10}
    assert len(sample.lines) == 80
    assert len({(ln.company, ln.key) for ln in sample.lines}) == 80


def test_statement_with_fewer_lines_gives_all_of_them() -> None:
    sample = draw_sample(rows("c01", n_is=12, n_bs=30, n_cf=0), seed=1)
    assert split(sample, "c01") == {"IS": 12, "BS": 15, "CF": 0}


def test_adding_a_company_does_not_move_another_companys_pick() -> None:
    alone = draw_sample(rows("c01"), seed=3)
    with_other = draw_sample(rows("c01") + rows("c02"), seed=3)
    assert [ln for ln in with_other.lines if ln.company == "c01"] == list(alone.lines)


def test_sample_never_shows_the_agents_answer() -> None:
    sample = draw_sample(rows("c01"), seed=1)
    dumped = sample.model_dump_json()
    assert "standard_line_id" not in dumped
    assert all(ln.gold_line_id is None for ln in sample.lines)


def test_duplicate_key_is_rejected() -> None:
    data = rows("c01", 2, 0, 0)
    with pytest.raises(AccuracyError, match="duplicate"):
        draw_sample([*data, data[0]], seed=1)


def labelled(
    company_correct: dict[str, tuple[int, int]],
) -> tuple[Sample, list[MappedRow]]:
    """Toy case: per company (lines, correct). The agent always answers 'is_revenue'."""
    lines: list[SampleLine] = []
    mapped: list[MappedRow] = []
    for company, (total, correct) in company_correct.items():
        for i in range(total):
            key = f"FY2025|is-{i:03d}"
            lines.append(
                SampleLine(
                    company=company,
                    key=key,
                    statement="IS",
                    label=f"L{i}",
                    gold_line_id="is_revenue" if i < correct else "is_other_income",
                )
            )
            mapped.append(
                MappedRow(
                    company=company,
                    key=key,
                    statement="IS",
                    label=f"L{i}",
                    standard_line_id="is_revenue",
                )
            )
    return Sample(seed=1, quota={}, lines=tuple(lines)), mapped


def test_191_of_200_passes() -> None:
    sample, mapped = labelled(
        {"c01": (40, 40), "c02": (40, 40), "c03": (40, 38), "c04": (40, 37), "c05": (40, 36)}
    )
    report = score(sample, mapped)
    assert (report.correct, report.total) == (191, 200)
    assert report.overall_passed and not report.failing_companies and report.passed
    assert "191 of 200" in report.summary()


def test_95_percent_boundary_is_exact() -> None:
    ok, m_ok = labelled({"c01": (200, 190)})
    assert score(ok, m_ok).overall_passed
    bad, m_bad = labelled({"c01": (200, 189)})
    assert not score(bad, m_bad).overall_passed


def test_company_below_90_fails_even_when_overall_passes() -> None:
    sample, mapped = labelled(
        {"c01": (40, 40), "c02": (40, 40), "c03": (40, 40), "c04": (40, 40), "c05": (40, 35)}
    )
    report = score(sample, mapped)
    assert (report.correct, report.total) == (195, 200)
    assert report.overall_passed
    assert report.failing_companies == ("c05",)
    assert not report.passed


def test_company_at_exactly_90_percent_is_not_failing() -> None:
    sample, mapped = labelled({"c01": (40, 40), "c02": (40, 36)})
    assert score(sample, mapped).failing_companies == ()


def test_wrong_lines_are_listed() -> None:
    sample, mapped = labelled({"c01": (4, 3)})
    assert score(sample, mapped).wrong == ("c01|FY2025|is-003: is_revenue != is_other_income",)


def test_unlabelled_missing_or_empty_input_is_an_error_not_a_score() -> None:
    sample, mapped = labelled({"c01": (2, 2)})
    blank = Sample(
        seed=1, quota={}, lines=(sample.lines[0].model_copy(update={"gold_line_id": None}),)
    )
    with pytest.raises(AccuracyError, match="no gold answer"):
        score(blank, mapped)
    with pytest.raises(AccuracyError, match="missing from the mapped"):
        score(sample, mapped[:1])
    with pytest.raises(AccuracyError, match="no lines"):
        score(Sample(seed=1, quota={}, lines=()), mapped)


def test_gold_id_must_be_a_standard_line_of_that_statement() -> None:
    sample, mapped = labelled({"c01": (1, 1)})
    valid = {"IS": frozenset({"is_revenue"}), "BS": frozenset({"bs_cash"}), "CF": frozenset()}
    assert score(sample, mapped, valid).passed
    bad = Sample(
        seed=1, quota={}, lines=(sample.lines[0].model_copy(update={"gold_line_id": "bs_cash"}),)
    )
    with pytest.raises(AccuracyError, match="not a IS standard line"):
        score(bad, mapped, valid)


def test_mapped_rows_come_from_run_state() -> None:
    raw = RawLine(
        raw_line_id="r1",
        doc_id="d1",
        page=1,
        original_label="Net sales",
        original_value_text="100",
        statement="IS",
        period="FY2025",
        scale="units",
        currency="USD",
    )
    mapped = MappedLine(
        raw_line_id="r1",
        period="FY2025",
        standard_line_id="is_revenue",
        confidence=0.9,
        rationale="rule",
        origin="rule",
    )
    out = mapped_rows("c01", [raw], {mapping_key("FY2025", "r1"): mapped})
    assert out == [
        MappedRow(
            company="c01",
            key="FY2025|r1",
            statement="IS",
            label="Net sales",
            standard_line_id="is_revenue",
        )
    ]
    with pytest.raises(AccuracyError, match="does not match"):
        mapped_rows("c01", [raw], {"wrong-key": mapped})


def test_cli_sample_then_score(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mapped_file = tmp_path / "mapped.jsonl"
    data = rows("c01") + rows("c02")
    mapped_file.write_text("\n".join(r.model_dump_json() for r in data), encoding="utf-8")
    sample_file = tmp_path / "sample.json"
    assert (
        main(["sample", "--mapped", str(mapped_file), "--seed", "5", "--out", str(sample_file)])
        == 0
    )

    # Unlabelled sample: a clear error, exit 2.
    assert main(["score", "--sample", str(sample_file), "--mapped", str(mapped_file)]) == 2

    doc = json.loads(sample_file.read_text(encoding="utf-8"))
    assert doc["seed"] == 5
    ids = {"IS": "is_revenue", "BS": "bs_cash", "CF": "cf_net_income"}
    for ln in doc["lines"]:
        ln["gold_line_id"] = ids[ln["statement"]]
    sample_file.write_text(json.dumps(doc), encoding="utf-8")
    report_file = tmp_path / "report.json"
    code = main(
        [
            "score",
            "--sample",
            str(sample_file),
            "--mapped",
            str(mapped_file),
            "--report",
            str(report_file),
        ]
    )
    assert code == 0
    assert "80 of 80" in capsys.readouterr().out
    assert json.loads(report_file.read_text(encoding="utf-8"))["passed"] is True


def test_a_truncated_or_edited_sample_is_not_the_recorded_draw() -> None:
    data = rows("c01") + rows("c02")
    sample = draw_sample(data, seed=4)
    check_is_the_recorded_draw(sample, data)
    cut = sample.model_copy(update={"lines": sample.lines[:1]})
    with pytest.raises(AccuracyError, match="not the draw for seed 4"):
        check_is_the_recorded_draw(cut, data)
    with pytest.raises(AccuracyError, match="not the draw"):
        check_is_the_recorded_draw(sample.model_copy(update={"seed": 5}), data)


def test_mapped_rows_unknown_raw_line_is_an_accuracy_error() -> None:
    mapped = MappedLine(
        raw_line_id="gone",
        period="FY2025",
        standard_line_id="is_revenue",
        confidence=0.9,
        rationale="rule",
        origin="rule",
    )
    with pytest.raises(AccuracyError, match="unknown raw line"):
        mapped_rows("c01", [], {mapping_key("FY2025", "gone"): mapped})
