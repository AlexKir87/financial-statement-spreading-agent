# Pilot report (plan step 13), re-run for steps 17 to 19

## Final run (r15, steps 28 to 30, 2026-10-24)

Live run of all five companies on main after step 30 (about USD 0.28 upper bound, 233,000 tokens). Workbooks: Library `pilot/workbooks/r15/`; summaries: `pilot/results-r15/`.

| | Balance sheet | Cash | RE rollforward | NI P&L = CF | Subtotals | Lines complete |
|---|---|---|---|---|---|---|
| A | pass | 15 | pass | pass | pass | pass |
| B | pass | pass | pass | pass | pass | pass |
| C | pass | pass | pass | pass | pass | pass |
| D | pass | pass | pass | pass | pass | pass |
| E | pass | pass | pass | pass | pass | pass |

B, C, D and E pass all six checks; A's only exception is the restricted cash (15, explained). Plug figures 0, every cell traced, all five workbooks verified. What changed since r13: step 28 names D's unlabeled balance sheet totals from exact sums (D's balance sheet check now runs and passes) and reads dropped lines once more (B); step 29 sets the tolerance to two units (C's rounding of 2); step 30 keeps empty cells visible so A's 35 is read in the right period. B's earlier drop was not reproduced in 18 extractions of the same page, so the cause of that one run is inferred.

## Run after steps 23 to 25 (2026-10-22)

Live runs on main with steps 23 and 24 (about USD 0.32), then with step 25 (about USD 0.30). After step 25 per-share figures, share counts and dividends per share no longer enter any sum. `lines_complete` passes on B; C falls from 3,065 to 1,627, D from 1,205,514 to 965,125. Left over, with causes: D's comprehensive income lines are added into net income; A and E add earnings attributable to noncontrolling interests (A also preferred dividends) that are printed below net income; C's interest expense is not flipped to a cost; the rollforward gaps (A and B 13, E 1.6, D 682,835) and E's balance sheet (7.2) are with the step 26 thread.

## Run on main after steps 19 to 22, and step 23 (2026-10-22)

Fresh live run of all five companies (about USD 0.37). All delivered with exceptions, no plugs. Checks: `bs_balances` passes on 4 of 5 (D not applicable), `ni_pl_equals_cf` on 5 of 5, `cf_cash_reconciles` on 4 of 5 (A fails by 15), `re_rollforward` on 1 of 5 (C; A and B fail by 13, E by 1.6, D by 682,835), `subtotals_match_source` and `lines_complete` on none. The last two failed by exactly double: a printed total ("Total sales", "Total operating costs and expenses") and its parts mapped to the same line and were added. Step 23 leaves such a total out of the sum. Applied offline to the saved mappings (no new model call), the largest gaps fall: B 4,524 to 125, A 4,867 to 648, E 107,359 to 52,329, C 41,115 to 19,981 (its balance sheet now reconciles: "Net property and equipment" was added to cost and depreciation, and a line equal to the sum of its siblings is now left out too); D does not move. What remains: D, E and C print costs as positive numbers and the sum rule adds them to revenue; A's financing section differs by 727; the small rollforward gaps (13, 1.6).

## Cost blocks after step 21 (2026-10-22)

Live re-run of all five companies with the cost-block sections. B's cost block ends at a printed gross profit line, so its generic lines ("Impairment and other charges", "Other unallocated, net", the total) are cost of sales; C's block ends at operating income, so its "Other", "Other operating (income) expense, net" and the total are operating expenses. All 7 lines that were wrong in the step 17 score are now right. Rescoring the 190-line sample against the new run: 172 lines can be matched, 169 are right (98.3%); the 3 misses are balance sheet "Total current liabilities" lines mapped to the grand total (plan step 22). The model is not deterministic, so this checks the fix and does not replace the 183 of 190 acceptance score above. C's two unlabeled restaurant expense lines moved from cost of sales to operating expenses, in line with the owner's label for "Other restaurant expenses".

## Company A after step 20 (long pages read in parts, 2026-10-22)

Live re-run of company A only. Before: A's cash flow page (about 160 entries) was cut at the reply ceiling, one page was dropped (`extraction_invalid_output`) and the statements were reported missing (`statement_missing_in_source`, 5 times). After: both issues are gone and all six checks run. `cf_cash_reconciles` fails by 15 (was not applicable), `re_rollforward` by 1,055, `ni_pl_equals_cf` by 1,068 to 1,468, `bs_balances` by about 115,000. The diagnosis step names the cause of the balance sheet failures: subtotal lines (total current liabilities, total shareholders' equity) are mapped to the same standard lines as the grand totals and are counted twice. That is a mapping problem now visible because the whole page is read; it is a candidate for a new step. Tokens 64,991, cost at most USD 0.078. B to E were not re-run.

## Final mapping accuracy (plan step 17 closed, 2026-10-22)

Sample: 190 lines (seed 20261021, quota 15/15/10 per company, redrawn on the step 19 re-run). All labels are confirmed: 158 earlier ones by the owner, 32 new ones reviewed on the owner's delegation. Lines whose label is printed more than once in a statement (cash flow "Other", deferred income taxes, "Products"/"Services") are scored per row by printed section. B "Other unallocated, net" is cost of sales (owner, 2026-10-22).

| Company | Correct | Lines | Share |
|---|---|---|---|
| A | 30 | 30 | 100% |
| B | 36 | 40 | 90.0% |
| C | 37 | 40 | 92.5% |
| D | 40 | 40 | 100% |
| E | 40 | 40 | 100% |
| **All** | **183** | **190** | **96.3%** |

The pass line (95% overall, no company below 90%) is met; B is exactly on the floor. All 7 wrong lines are in income statement cost blocks: B "Total operating costs and expenses" ×2, "Impairment and other charges" and "Other unallocated, net" (labelled cost of sales, mapped operating expenses); C "Other" ×2 and "Other operating (income) expense, net" (labelled operating expenses, mapped other items). Whether a cost line is cost of sales or operating expenses depends on whether a gross profit line follows; this is a candidate for a new plan step. The six-check acceptance of step 13 is still not met (see the sections below).

## Latest re-run (steps 18 and 19 built, 2026-10-22)

**Result: all 5 companies deliver with exceptions, mapping accuracy on the scored lines is 96.8%, and the six checks still do not all pass on any company.** Same 5 filings and model as below; code from `main` plus step 19. Cost about USD 0.29 for the five runs (upper bound).

| | A | B | C | D | E |
|---|---|---|---|---|---|
| `bs_balances` | pass | pass | fail | not_applicable | fail |
| `cf_cash_reconciles` | not_applicable | pass | pass | pass | pass |
| `re_rollforward` | not_applicable | fail | pass | fail | fail |
| `ni_pl_equals_cf` | not_applicable | pass | pass | pass | fail |
| `subtotals_match_source` | fail | fail | fail | fail | fail |
| `lines_complete` | fail | fail | fail | fail | fail |

Compared with the step 17 run, `cf_cash_reconciles` now passes on four companies (was one) and `ni_pl_equals_cf` on three (was one).

**Mapping accuracy.** The sample was redrawn on this run (seed 20261021, 190 lines because one cash flow page of A was not extracted). The owner's labels (pre-filled by a model, confirmed by the owner 2026-10-22, with section-dependent labels resolved per row) cover 158 of the 190 lines: **153 right, 96.8%**; A 27/27, B 28/30, C 32/35, D 40/40, E 26/26. That is above the 95% line and no company is under 90% on the scored lines, but it is **not the acceptance score**: 32 lines carry labels not yet confirmed (drafted in `pilot/accuracy/draft-labels-r5.md`; 30 of the 32 drafts already match the mapping, which would give 183 of 190, 96.3%). The five wrong scored lines are cost-block lines: B "Other unallocated, net" and "Impairment and other charges" (labelled cost of sales, mapped operating expenses) and three C lines called "Other" under the SG&A heading (labelled operating expenses, mapped other items).

The same 200 lines scored in the step 17 run were 177 right (88.5%). On the lines that both runs extracted the step 19 run is 175 of 180 (97.2%); 20 of the 200 were not extracted again (extraction output varies between runs).

**What step 19 fixed** (against the 23 wrong lines of the step 17 run): cash flow "Other" and the deferred-tax side now follow the printed section (rules, no model call); opening and closing cash have their own lines `cf_cash_beginning` and `cf_cash_end` (owner decision) and are no longer tagged as starting profit; "Accrued liabilities" and the taxes-payable labels map by rule to other liabilities; B's "Products" and "Services" in the cost block map to cost of sales.

**Still open**
1. **Long pages lose their reply.** A's cash flow page (about 160 entries) came back cut off (`extraction_invalid_output`, invalid JSON at the reply ceiling of 8,192 tokens), so its cash flow checks are `not_applicable`; an earlier A run lost its income statement page the same way. Proposed as the next step: extract such a page in two halves, or raise the reply ceiling.
2. **Cost-block lines** (the five wrong scored lines): whether a line in the costs section is cost of sales or operating expenses depends on whether a gross profit follows the block. Not built.
3. `subtotals_match_source` and `lines_complete` fail on all five and have not been diagnosed.
4. `bs_balances` fails on C and E and is not applicable on D; not diagnosed.

---

(The step 17 run follows, unchanged.)


## Re-run after steps 14 to 16 (plan step 17, 2026-10-21)

**Result: the pilot still does not meet acceptance, but all 5 companies now deliver.** 5 of 5 delivered a workbook with exceptions (before: 3 of 5), 0 of 5 pass all six checks, and D passes `cf_cash_reconciles` and `ni_pl_equals_cf`. Sampled mapping accuracy is **preliminary and below the pass line**: 91 of 97 confirmed lines right (93.8%; A 88%, B 100%, C 77%, D 96%, E 100%). Same 5 filings, same model (`deepseek/deepseek-v4.1-flash-1017`), current `main` (statement locator, amount-text normalisation, currency from a lone symbol, canonical period keys). Nothing was tuned on these 5 companies.

| | A | B | C | D | E |
|---|---|---|---|---|---|
| Status | delivered_with_exceptions | delivered_with_exceptions | delivered_with_exceptions | delivered_with_exceptions | delivered_with_exceptions |
| `bs_balances` | fail | pass | fail | not_applicable | pass |
| `cf_cash_reconciles` | not_applicable | not_applicable | not_applicable | pass | fail |
| `re_rollforward` | not_applicable | not_applicable | not_applicable | fail | fail |
| `ni_pl_equals_cf` | not_applicable | not_applicable | not_applicable | pass | fail |
| `subtotals_match_source` | fail | fail | fail | fail | fail |
| `lines_complete` | fail | fail | fail | fail | fail |
| Exceptions | 30 | 24 | 40 | 24 | 35 |
| Mapped values in the workbook / traced | 88 / 88 | 85 / 85 | 98 / 98 | 90 / 90 | 108 / 108 |
| Currency, unit | USD, millions | USD, millions | USD, millions | USD, thousands | USD, millions |
| Tokens | 62,072 | 30,612 | 44,476 | 40,431 | 60,988 |
| Cost, upper bound (USD) | 0.075 | 0.037 | 0.053 | 0.049 | 0.073 |

Total about USD 0.29 and 238k tokens (upper bound at the peak-hour rate). Plug figures 0 in all 5; every workbook value has a trace entry (0 untraced); all 5 workbooks passed the formula-error scan.

**What the re-run shows**
1. **Currency is fixed** (all 5 read as USD) and **B and C now deliver**: the dropped-line problem is much smaller.
2. **Period pairing works where the headings are full dates, and not where two columns share one label.** A and C print a three-month and a six-month column under the same bare year ("2026", "2025"), and C also prints "Quarters Ended June 30, 2026" (plural, not read). The page names two periods, so the code refuses to guess (by design), the two columns collide on one key, and the duration checks go `not_applicable` or compare mixed figures. B prints plain dates ("June 28, 2026") over the same kind of pair. Reading these needs column position, not heading text: a new plan step (proposed below), not built here.
3. **The remaining failures are mapping, not periods.** Largest sources in the 6 wrong sampled lines: opening cash tagged as the cash-flow starting profit (C, D), "Deferred income taxes" put on the asset side and "Other taxes" and "Accrued liabilities" as payables (A, C). The sampled lines where the owner's label was itself a judgement call (`low` in the draft sheet) are 4 of the 6.
4. **`subtotals_match_source` and `lines_complete` fail on all 5**, as before; the cause was not diagnosed in this step.

**Accuracy sample.** Drawn again on the re-run's mapped lines (seed 20261021, 15/15/10 per company, 200 lines) because the period keys changed. The owner's confirmed labels (pre-filled by a model, confirmed by the owner 2026-10-21) cover 97 of the 200 lines by label text; 103 lines carry labels not yet confirmed (86 distinct, drafted in `pilot/accuracy/draft-labels-r2.md`). The count above uses only the 97 confirmed lines and is **not** the acceptance score; the pass line (95% overall, no company under 90%) is scored on all 200 once the remaining labels are confirmed.

**Next (proposed, needs the owner's approval)**: a step that reads a three-month and a six-month column pair from column order, and "Quarters Ended"; then a mapping step for the 6 wrong patterns above (cash at beginning of period, deferred taxes side, accrued and other taxes).

---

(The first run's report follows, unchanged.)

## First run (plan step 13): 5 public 10-Qs, live model

**Result: the pilot does not meet acceptance.** 0 of 5 companies pass all six checks, 2 of 5 produced no workbook, and the sampled mapping accuracy is not measured yet (it needs the owner's blind labels). Nothing was tuned on these 5 companies; this is the first and only measured run of the full pipeline on real filings.

Companies are anonymised as A to E; the key to the filings is in the Library (`pilot/`), not in git. Each is the latest 10-Q fetched from EDGAR on 2026-10-21 (not the two gold-set filings). Model: `deepseek/deepseek-v4.1-flash-1017` via OpenRouter (`SPREAD_MODEL`), token cap 1,500,000 per run, runner `scripts/run_pilot.py`. Workbooks, per-company `RunSummary` JSON, the mapped lines and the accuracy sample are in the Library under `pilot/`.

## Per company

| | A | B | C | D | E |
|---|---|---|---|---|---|
| Status | delivered_with_exceptions | not_delivered (`unreadable_source`) | not_delivered (`unreadable_source`) | delivered_with_exceptions | delivered_with_exceptions |
| `bs_balances` | fail | no run | no run | not_applicable | pass |
| `cf_cash_reconciles` | not_applicable | no run | no run | not_applicable | not_applicable |
| `re_rollforward` | not_applicable | no run | no run | not_applicable | not_applicable |
| `ni_pl_equals_cf` | not_applicable | no run | no run | pass | not_applicable |
| `subtotals_match_source` | fail | no run | no run | fail | fail |
| `lines_complete` | fail | no run | no run | fail | fail |
| Exceptions (count, largest size) | 23, 115,663 | 0 | 0 | 24, 9,750,627 | 27, 110,522 |
| Repair attempts | 2 | 0 | 0 | 2 | 2 |
| Mapped lines in the workbook | 66 | 0 | 0 | 78 | 85 |
| Tokens | 68,597 | 18,224 | 27,588 | 21,977 | 61,555 |
| Cost, upper bound (USD) | 0.082 | 0.022 | 0.033 | 0.026 | 0.074 |
| Elapsed (s) | 409 | 74 | 92 | 73 | 183 |
| Workbook formula-error scan | clean | none written | none written | clean | clean |

Cost is an upper bound: total tokens priced at the higher of the model's two list prices on OpenRouter (USD 0.30 and 1.20 per million, the peak-hour rate). The real bill is the provider's. Total about USD 0.24 and 181k tokens for the five runs.

## Against the plan's proof

| Plan proof | Measured |
|---|---|
| 5 of 5 workbooks verified (formula-error scan clean) | **3 of 5.** The 3 workbooks written verified clean; B and C wrote none |
| Every number traced to its source (numbers without a trace = 0) | 0 untraced in the 3 workbooks (229 values, 229 trace entries); B and C have no numbers |
| Plug figures 0 | 0 in all 5 |
| All six checks pass or carry an explained exception, 5 of 5 | **Not met.** Exceptions exist but 3 checks are `not_applicable` on most companies because the statements' period names do not pair up (observation 2) or the balance sheet totals are missing, and B and C have none |
| Sampled mapping accuracy as a count against 95% overall and 90% per company | **Not measured.** Sample of 200 lines drawn (seed 20261021, quota 15/15/10 per company) and waiting for the owner's labels in `pilot/accuracy/sample.json`; then `python evals/mapping_accuracy.py score` |

## What the runs show (observations, not yet diagnosed to a cause)

1. **Many lines are dropped at extraction.** 43 to 107 `extracted_line_not_in_source` per company: the code drops a line whose label and value are not on one printed row. The cause is not yet known (model output format, row layout of the HTML text, or both). It is the largest source of missing lines.
2. **Period labels do not line up across statements.** In A the income statement periods are "Six months ended June 30 2026" and "Three months ended ...", the balance sheet's are "June 30 2026" and "December 31 2025", and the cash flow's are the bare years "2026" and "2025". The checks compare statements by period name, so the cross-statement checks (cash reconciles, retained-earnings rollforward, net income equals cash flow) find no pair and are `not_applicable`, and a 10-Q's quarter and year-to-date columns are never tied to the balance sheet date. The income statement keeps only 2 to 3 lines per period in A, which is the drop in observation 1.
3. **B and C ended `not_delivered`:** after the drops no spread could be built (`spread_not_built`).
4. **Currency is `XXX` for D and E** (`currency_not_stated`): the statement headings print the unit but not the currency code.
5. **Exception sizes are large** (up to 9.75 million in D's thousands) and the cause is not settled. The stored diagnoses point at mapping, not rounding: in D a cost of sales is added instead of subtracted, in E an ending cash balance is tagged as the net change in cash, in A subtotals carry the same ids as the totals printed under them. None is a business explanation, so none counts as an "explained exception" in the acceptance sense.

## Diagnosis on the gold set (not on the pilot companies)

Checked on the two gold-set filings only.
1. **Dropped lines are an amount-text mismatch.** The flattened HTML prints a negative as `( 2,037 )` with inner spaces; the model returns `(2,037)`. The label is on the row but the value is not found there. On gold filing 1, 29 of 185 extracted lines were dropped; the first 12, all inspected, were bracketed negatives. Gold filing 2's `$` handling was not run live.
2. **Locator defect (fixed in this PR).** Headings starting "Unaudited" were not matched, so gold filing 2 found 0 statements and fell back to one 124k-character page. Fixed with an optional "unaudited " prefix and two tests; both gold filings now give 3 pages. The pilot results above were produced before this fix, but all five pilot filings already located 3 statements, so they are unaffected.
3. **Period names:** income statement "Three Months Ended June 27, 2026", balance sheet "June 27, 2026", cash flow a bare "2026" (the pilot shows the same pattern). Needs a code step that turns each column into start date, end date and duration.
4. **Currency** comes back `XXX` when only `$` is printed.

Fixes are proposed as plan steps 14 to 17 in the engagement plan and wait for the owner's approval; none is built here.

## What was done before the runs

- A full 10-Q is one HTML page of about 80,000 tokens and the first live run returned an empty reply. Step 13 therefore adds a deterministic statement locator to ingest (`app/ingest/loader.py`, `locate_statements`, owner-approved 2026-10-21): it finds the income statement, balance sheet and cash flow statement, drops the contents list, notes, comprehensive income and equity statements, and keeps each table row as a line. It found all three statements in all five filings.
- A first run of all five (before table rows were kept as lines) was discarded: A and B delivered with exceptions, C and D did not deliver. Its results were not used. Only the second run is reported here.

## What is needed to get to acceptance

1. The owner labels `pilot/accuracy/sample.json` (blind), so the accuracy count can be scored.
2. Approve the proposed steps 14 to 17 in the engagement plan (fixes diagnosed above on the gold set, never tuned on these 5 companies), then re-run the pilot.
3. Decide whether 10-Q year-to-date and quarter columns are in scope (spec, period model) or the pilot should use 10-Ks.

## Run after step 27 (r13) and the final run before it (r12)

Differences are in the statement's units. r12 is main after steps 25 and 26; r13 adds step 27.

| | Balance sheet | Cash | RE rollforward | NI P&L = CF | Subtotals | Lines complete |
|---|---|---|---|---|---|---|
| A r12 | pass | 15 | pass | pass | 648 | 727 |
| A r13 | pass | 15 | pass | pass | pass | 35 |
| B r12 | pass | pass | pass | pass | 125 | pass |
| B r13 | pass | pass | pass | pass | 33,061 | 31,614 |
| C r12 | pass | pass | pass | pass | 1,623 | 1,627 |
| C r13 | pass | pass | pass | pass | 2 | pass |
| D r12 | n/a | pass | pass | pass | 22,829 | 965,125 |
| D r13 | n/a | pass | pass | pass | pass | pass |
| E r12 | pass | pass | pass | pass | 624 | 961 |
| E r13 | pass | pass | pass | pass | pass | pass |

Step 26 made the retained earnings rollforward pass on all five. Step 27 removed the income statement gaps on A, C, D and E. B r13 is not a regression of the code: the extraction dropped B's four "Products" revenue rows on that run (flagged `extracted_line_not_in_source`), and the offline replay of the r12 states passes every B check. A's 35 is its investing section on that run. D's balance sheet is "not applicable" because its subtotal rows print with no label, so the totals were never read.
