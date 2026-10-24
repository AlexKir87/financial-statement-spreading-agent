#!/usr/bin/env python3
"""Sampled mapping accuracy (plan step 12, the plan "Numbers" B).

Metric: share of sampled lines whose `standard_line_id` equals the owner's gold answer
(exact id match, not value match). Acceptance sample: 40 lines per company (15 IS, 15 BS,
10 CF; all lines when a statement has fewer), drawn by a seeded random pick from the lines
the agent mapped. Pass line: 95% overall and no company below 90%, reported as a count.

Flow (the owner labels blind, so the sample file never shows the agent's answer):
  1. python evals/mapping_accuracy.py sample --mapped mapped.jsonl --seed 20261101 --out sample.json
  2. the owner fills `gold_line_id` on each line of sample.json
  3. python evals/mapping_accuracy.py score --sample sample.json --mapped mapped.jsonl

`mapped.jsonl`: one object per mapped line, `{"company", "key", "statement", "label",
"standard_line_id"}`. `key` is `mapping_key(period, raw_line_id)`; `company` is an
anonymised id and `label` an anonymised label. `mapped_rows` builds the rows from a
finished run's state. Exit code 0 means the pass line was met.
"""

import argparse
import json
import random
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.mapping.table import load_mapping_table  # noqa: E402
from app.state import Statement, mapping_key  # noqa: E402

QUOTA: Final[Mapping[Statement, int]] = {"IS": 15, "BS": 15, "CF": 10}
STATEMENTS: Final[tuple[Statement, ...]] = ("IS", "BS", "CF")
# Pass line as whole percents so the comparison is exact integer arithmetic.
OVERALL_PASS_PERCENT: Final = 95
COMPANY_FLOOR_PERCENT: Final = 90


class AccuracyError(ValueError):
    """The sample or the gold answers cannot be scored; the message says why."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MappedRow(_Model):
    company: str = Field(min_length=1)
    key: str = Field(min_length=1)
    statement: Statement
    label: str
    standard_line_id: str = Field(min_length=1)


class SampleLine(_Model):
    """What the owner sees: the label and where it came from, never the agent's answer."""

    company: str = Field(min_length=1)
    key: str = Field(min_length=1)
    statement: Statement
    label: str
    gold_line_id: str | None = None


class Sample(_Model):
    seed: int
    quota: dict[str, int]
    lines: tuple[SampleLine, ...]


class CompanyScore(_Model):
    company: str
    correct: int
    total: int


class AccuracyReport(_Model):
    seed: int | None
    companies: tuple[CompanyScore, ...]
    correct: int
    total: int
    overall_passed: bool
    failing_companies: tuple[str, ...]
    passed: bool
    wrong: tuple[str, ...]  # "company|key: predicted != gold", for the owner's review

    def summary(self) -> str:
        parts = [f"{c.company} {c.correct} of {c.total}" for c in self.companies]
        verdict = "PASS" if self.passed else "FAIL"
        return (
            f"mapping accuracy {self.correct} of {self.total} "
            f"(pass line {OVERALL_PASS_PERCENT}% overall, {COMPANY_FLOOR_PERCENT}% per company): "
            f"{verdict}; " + "; ".join(parts)
        )


def draw_sample(
    rows: Iterable[MappedRow],
    seed: int,
    quota: Mapping[Statement, int] = QUOTA,
) -> Sample:
    """Seeded pick per company and statement. Same rows and seed give the same sample.

    Candidates are sorted by key first, so the draw does not depend on input order, and
    each (company, statement) pool has its own generator, so adding a company does not
    move another company's pick.
    """
    pools: dict[tuple[str, Statement], dict[str, MappedRow]] = defaultdict(dict)
    for row in rows:
        pool = pools[(row.company, row.statement)]
        if row.key in pool:
            raise AccuracyError(f"duplicate key {row.key!r} for company {row.company!r}")
        pool[row.key] = row
    picked: list[SampleLine] = []
    for company in sorted({c for c, _ in pools}):
        for statement in STATEMENTS:
            candidates = sorted(pools.get((company, statement), {}).values(), key=lambda r: r.key)
            take = min(quota[statement], len(candidates))
            rng = random.Random(f"{seed}|{company}|{statement}")  # noqa: S311 - sampling, not crypto
            picked.extend(
                SampleLine(company=r.company, key=r.key, statement=r.statement, label=r.label)
                for r in sorted(rng.sample(candidates, take), key=lambda r: r.key)
            )
    return Sample(seed=seed, quota={str(k): v for k, v in quota.items()}, lines=tuple(picked))


def check_is_the_recorded_draw(sample: Sample, mapped: Iterable[MappedRow]) -> None:
    """Refuse a sample that is not exactly the seeded draw from the mapped lines.

    A truncated or hand-edited sample file would otherwise score green on a handful of lines.
    """
    expected = draw_sample(mapped, sample.seed)
    want = {(ln.company, ln.key) for ln in expected.lines}
    got = {(ln.company, ln.key) for ln in sample.lines}
    if want != got or len(sample.lines) != len(want):
        raise AccuracyError(
            f"sample is not the draw for seed {sample.seed}: "
            f"{len(got - want)} lines not drawn, {len(want - got)} drawn lines missing"
        )


def score(
    sample: Sample,
    mapped: Iterable[MappedRow],
    valid_line_ids: Mapping[Statement, frozenset[str]] | None = None,
) -> AccuracyReport:
    """Exact id match per sampled line, then the 95% / 90% rule on whole-number arithmetic."""
    predicted = {(r.company, r.key): r for r in mapped}
    seen: set[tuple[str, str]] = set()
    right: dict[str, int] = defaultdict(int)
    count: dict[str, int] = defaultdict(int)
    wrong: list[str] = []
    for line in sample.lines:
        ident = (line.company, line.key)
        if ident in seen:
            raise AccuracyError(f"line sampled twice: {line.company}|{line.key}")
        seen.add(ident)
        if not line.gold_line_id:
            raise AccuracyError(f"no gold answer for {line.company}|{line.key}")
        if valid_line_ids is not None and line.gold_line_id not in valid_line_ids[line.statement]:
            raise AccuracyError(
                f"gold id {line.gold_line_id!r} is not a {line.statement} standard line "
                f"({line.company}|{line.key})"
            )
        row = predicted.get(ident)
        if row is None:
            raise AccuracyError(
                f"sampled line missing from the mapped lines: {ident[0]}|{ident[1]}"
            )
        count[line.company] += 1
        if row.standard_line_id == line.gold_line_id:
            right[line.company] += 1
        else:
            wrong.append(
                f"{line.company}|{line.key}: {row.standard_line_id} != {line.gold_line_id}"
            )
    if not count:
        raise AccuracyError("the sample has no lines")
    companies = tuple(
        CompanyScore(company=c, correct=right[c], total=count[c]) for c in sorted(count)
    )
    correct, total = sum(right.values()), sum(count.values())
    overall_ok = correct * 100 >= OVERALL_PASS_PERCENT * total
    failing = tuple(
        c.company for c in companies if c.correct * 100 < COMPANY_FLOOR_PERCENT * c.total
    )
    return AccuracyReport(
        seed=sample.seed,
        companies=companies,
        correct=correct,
        total=total,
        overall_passed=overall_ok,
        failing_companies=failing,
        passed=overall_ok and not failing,
        wrong=tuple(wrong),
    )


def mapped_rows(
    company: str, raw_lines: Sequence[object], mapping: Mapping[str, object]
) -> list[MappedRow]:
    """Rows for one finished run: `state["raw_lines"]` and `state["mapping"]`.

    Only lines the agent mapped are candidates. `company` must be the anonymised id.
    """
    from app.state import MappedLine, RawLine  # local: keeps the CLI import light

    by_id = {ln.raw_line_id: ln for ln in raw_lines if isinstance(ln, RawLine)}
    rows: list[MappedRow] = []
    for key, mapped in mapping.items():
        if not isinstance(mapped, MappedLine):
            continue
        raw = by_id.get(mapped.raw_line_id)
        if raw is None:
            raise AccuracyError(
                f"mapping {key!r} refers to unknown raw line {mapped.raw_line_id!r}"
            )
        if key != mapping_key(raw.period, raw.raw_line_id):
            raise AccuracyError(f"mapping key {key!r} does not match its raw line")
        rows.append(
            MappedRow(
                company=company,
                key=key,
                statement=raw.statement,
                label=raw.original_label,
                standard_line_id=mapped.standard_line_id,
            )
        )
    return rows


def _read_rows(path: Path) -> list[MappedRow]:
    return [
        MappedRow.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else "")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sample = sub.add_parser("sample", help="draw the seeded sample for the owner to label")
    p_sample.add_argument("--mapped", type=Path, required=True)
    p_sample.add_argument("--seed", type=int, required=True)
    p_sample.add_argument("--out", type=Path, required=True)
    p_score = sub.add_parser("score", help="score a labelled sample against the mapped lines")
    p_score.add_argument("--sample", type=Path, required=True)
    p_score.add_argument("--mapped", type=Path, required=True)
    p_score.add_argument("--report", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "sample":
            sample = draw_sample(_read_rows(args.mapped), args.seed)
            args.out.write_text(sample.model_dump_json(indent=2) + "\n", encoding="utf-8")
            print(f"sample of {len(sample.lines)} lines written to {args.out} (seed {args.seed})")
            return 0
        table = load_mapping_table()
        valid = {s: table.line_ids(s) for s in STATEMENTS}
        sample = Sample.model_validate_json(args.sample.read_text(encoding="utf-8"))
        mapped = _read_rows(args.mapped)
        check_is_the_recorded_draw(sample, mapped)
        report = score(sample, mapped, valid)
    except (AccuracyError, ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(report.summary())
    for entry in report.wrong:
        print(f"  wrong: {entry}")
    if args.report:
        args.report.write_text(json.dumps(report.model_dump(), indent=2) + "\n", encoding="utf-8")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
