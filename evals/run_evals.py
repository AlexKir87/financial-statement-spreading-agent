#!/usr/bin/env python3
"""Run eval cases against app.agent.run_agent and fail below a pass-rate threshold.

Case format, one JSON object per line in evals/cases.jsonl:
  {"id": "c1", "category": "happy|edge|adversarial|out_of_scope", "input": "...",
   "checks": [ {"type": "contains", "value": "..."}, ... ]}
Check types are documented in evals/graders.py. The old format
  {"expected_contains": [...], "forbidden_contains": [...]} is still accepted.

Usage:
  python evals/run_evals.py --threshold 0.9 --report evals/report.json
  python evals/run_evals.py --repeats 3      # run each case 3 times, majority decides
  python evals/run_evals.py --skip-judge     # skip llm_judge checks (offline runs)
"""

import argparse
import importlib
import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evals.graders import JudgeFn, run_check  # noqa: E402
from evals.models import AgentResult, Grade  # noqa: E402

REQUIRED_CATEGORIES = ("happy", "edge", "adversarial", "out_of_scope")


def load_cases(path: Path) -> list[dict[str, Any]]:
    lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def checks_for(case: dict[str, Any]) -> list[dict[str, Any]]:
    checks = list(case.get("checks", []))
    checks += [{"type": "contains", "value": s} for s in case.get("expected_contains", [])]
    checks += [{"type": "not_contains", "value": s} for s in case.get("forbidden_contains", [])]
    return checks


def to_result(raw: Any) -> AgentResult:
    return raw if isinstance(raw, AgentResult) else AgentResult(output=str(raw))


def run_once(
    case: dict[str, Any], run_agent: Any, judge: JudgeFn | None, skip_judge: bool
) -> tuple[bool, list[str], float]:
    start = time.perf_counter()
    try:
        result = to_result(run_agent(case["input"]))
    except Exception as exc:  # a crash is a failed case, not a crashed run
        return False, [f"exception {type(exc).__name__}: {exc}"], time.perf_counter() - start
    elapsed = time.perf_counter() - start
    problems: list[str] = []
    for spec in checks_for(case):
        if skip_judge and spec["type"] == "llm_judge":
            continue
        try:
            grade: Grade = run_check(spec, case["input"], result, judge)
        except Exception as exc:  # a broken judge or malformed check fails the case, not the run
            grade = Grade(False, f"{type(exc).__name__}: {exc}")
        if not grade.passed:
            problems.append(f"{spec['type']}: {grade.detail}")
    if not checks_for(case):
        problems.append("case has no checks")
    return not problems, problems, elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", default=str(ROOT / "evals" / "cases.jsonl"))
    parser.add_argument("--threshold", type=float, default=0.9)
    parser.add_argument("--min-cases", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--report", default="")
    parser.add_argument("--skip-judge", action="store_true")
    parser.add_argument("--allow-missing-categories", action="store_true")
    args = parser.parse_args()

    cases = load_cases(Path(args.cases))
    if len(cases) < args.min_cases:
        print(f"FAIL: {len(cases)} cases, need at least {args.min_cases} (the spec requires 10)")
        return 1
    present = {c.get("category", "") for c in cases}
    missing = [c for c in REQUIRED_CATEGORIES if c not in present]
    if missing and not args.allow_missing_categories:
        print(f"FAIL: no cases in categories {missing}; the spec requires all of them")
        return 1

    run_agent = importlib.import_module("app.agent").run_agent
    judge: JudgeFn | None = None
    if not args.skip_judge and any(s["type"] == "llm_judge" for c in cases for s in checks_for(c)):
        from evals.judge import anthropic_judge

        judge = anthropic_judge

    by_category: dict[str, list[bool]] = defaultdict(list)
    report: list[dict[str, Any]] = []
    for case in cases:
        runs = [run_once(case, run_agent, judge, args.skip_judge) for _ in range(args.repeats)]
        passes = sum(ok for ok, _, _ in runs)
        passed = passes * 2 > len(runs)  # strict majority
        problems = next((p for ok, p, _ in runs if not ok), [])
        by_category[case.get("category", "uncategorized")].append(passed)
        mean_s = sum(t for _, _, t in runs) / len(runs)
        report.append(
            {
                "id": case["id"],
                "category": case.get("category", ""),
                "passed": passed,
                "passes": f"{passes}/{len(runs)}",
                "seconds": round(mean_s, 3),
                "problems": problems,
            }
        )
        status = "PASS" if passed else "FAIL"
        print(f"{status} {case['id']} ({passes}/{len(runs)}) {'; '.join(problems)}")

    total = sum(r["passed"] for r in report)
    rate = total / len(report)
    for cat, results in sorted(by_category.items()):
        print(f"  {cat}: {sum(results)}/{len(results)}")
    print(f"pass rate {rate:.2%} ({total}/{len(report)}), threshold {args.threshold:.0%}")
    if args.report:
        Path(args.report).write_text(json.dumps({"pass_rate": rate, "cases": report}, indent=2))
    return 0 if rate >= args.threshold else 1


if __name__ == "__main__":
    sys.exit(main())
