# Financial statement spreading agent (portfolio project)

An AI agent that reads a company's public filing and writes an Excel workbook with the Income Statement, Balance Sheet and Cash Flow Statement in a standard line-item set, all periods side by side. Every number is traced to its source, and six reconciliation checks run as **code, not model opinion**. The agent never plugs a balancing figure.

> Simulated engagement: the "client", its request and the pilot scope are fictional. The pilot ran on public SEC filings. The offline demo and the example workbook use a **scripted, rule-based stand-in for the model** on a tiny synthetic filing: they exercise the real graph, reconciliation and workbook code, not the quality of a live model. The live pilot figures below come from `HANDOVER.md`; the pilot workbooks are not published.

## What an analyst gets

A workbook with nine sheets: `Summary`, `IS`, `BS`, `CF`, `Source_Trace` (document, page, original line name and value for every number), `Reconciliations`, `Open_Issues`, `Change_Log` and `Mapping` (where an analyst enters corrections). Totals are live Excel formulas. Corrections re-run the checks and write a new workbook version (versions are never overwritten).

The six checks: balance sheet balances; cash flow reconciles to the change in cash; retained earnings roll forward; net income on the P&L equals net income in the cash flow; subtotals match the source; detail lines add up to printed totals (tolerance: two units of the reported scale). A check that still fails after a capped repair loop becomes an exception with the exact difference, never a plug.

Example: [`examples/sample_workbook_synthetic.xlsx`](examples/sample_workbook_synthetic.xlsx) (12 checks: 9 OK, 3 not applicable for the first period, 0 plugs).

## Try it in two minutes (no API key)

```bash
pip install -r requirements.txt -r requirements-dev.txt
bash scripts/demo.sh        # replays scripted model replies; needs LibreOffice (soffice)
pytest -q
```

`demo.sh` runs the 11 eval cases (happy, edge, adversarial, out of scope) offline and writes workbooks to `demo-output/`. Real model output differs: the model is not deterministic.

## Pilot result (live run, five US 10-Q filings, 2026-10-24)

| | Result |
|---|---|
| Companies delivered | 5 of 5; 4 pass all six checks, 1 has one stated exception (cash differs by 15: restricted cash printed only as a closing figure) |
| Plug figures | 0 |
| Traced cells | all (122 to 150 per workbook) |
| Mapping accuracy | 96.3% (183 of 190 sampled lines), no company below 90%; **not rescored** after the latest line-set changes |
| Cost per company | about USD 0.06 on average (estimate, upper bound USD 0.28 for all five) |
| Human review time | **not measured** |

Details: [`HANDOVER.md`](HANDOVER.md), [`docs/pilot-report.md`](docs/pilot-report.md). The five pilot workbooks are not in this repository.

## Design choices that matter in finance

- Checks are code; the model never grades itself.
- Human in the loop: the agent approves nothing and sends nothing; analyst corrections go through the `Mapping` sheet.
- Fail closed: no LibreOffice recalculation means no delivery; missing statements and unreadable scans are reported, not guessed.
- Auditable: source trace for every value, change log, versioned workbooks, structured logs and OpenTelemetry traces.
- Stack: LangGraph, OpenRouter (provider chosen by the simulated client), XlsxWriter/openpyxl, SQLite checkpoints. 607 tests.

## Limitations

- Pilot scope only: no EBITDA normalisation, QoE-lite or KPIs.
- English-language filings only; scan-reading accuracy is unmeasured; no fully local model option.
- Data-handling terms for the model provider (retention, routing) are `unknown` and must be settled before real client documents are processed.

## Repository map

`app/` graph, ingest, extraction, mapping, reconciliation, repair loop, workbook, corrections, telemetry · `evals/` eval framework, gold set, scripted transcript · `tests/` · `docs/architecture.md` · `docs/runbook.md` · `docs/pilot-report.md` · `HANDOVER.md`.
