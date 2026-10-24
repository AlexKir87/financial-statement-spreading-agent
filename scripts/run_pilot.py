"""Pilot runner (plan step 13): spread each company's filing and write one RunSummary per company.

    python scripts/run_pilot.py --library /mnt/project-files --results pilot/results \\
        company-A=pilot/filings/a.htm company-B=pilot/filings/b.htm

Needs the settings in the spec "Environment variables" plus `SPREAD_MODEL`. Summaries go to the
results folder in the Library (client data, never git). Cost is an upper bound: total tokens at
the higher of the two prices given on the command line (USD per million tokens); the real bill is
the provider's.
"""

import argparse
import json
import time
from decimal import Decimal
from pathlib import Path

from app.agent import run_library
from app.config import Settings

REQUEST = "Spread the income statement, balance sheet and cash flow statement into the workbook."


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--library", required=True)
    ap.add_argument("--results", required=True, help="folder under the library root")
    ap.add_argument("--price-in", required=True, type=Decimal, help="USD per million input tokens")
    ap.add_argument("--price-out", required=True, type=Decimal, help="USD per million output")
    ap.add_argument("companies", nargs="+", help="company-ref=path/under/library")
    args = ap.parse_args()
    root = Path(args.library).resolve()
    out_dir = root / args.results
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env()
    for item in args.companies:
        ref, _, path = item.partition("=")
        started = time.monotonic()
        output, tools, tokens = run_library(
            REQUEST, [path], root, company_ref=ref, settings=settings
        )
        seconds = round(time.monotonic() - started, 1)
        summary = json.loads(output)
        usage = summary["usage"]
        # the summary carries total tokens only, so every token is priced at the higher rate
        cost = Decimal(usage["total_tokens"]) * max(args.price_in, args.price_out) / 1_000_000
        summary["_pilot"] = {
            "seconds": seconds,
            "tokens": tokens,
            "cost_usd_upper_bound": str(cost.quantize(Decimal("0.0001"))),
            "tools": tools,
        }
        (out_dir / f"{ref}.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(ref, summary["status"], f"{seconds}s", f"{tokens} tokens", f"USD<={cost:.4f}")


if __name__ == "__main__":
    main()
