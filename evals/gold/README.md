# Development gold set (plan step 12)

`mapping_gold.jsonl`: two public quarterly filings (US 10-Q, USD millions) whose statement
lines the owner mapped to `mapping/standard_lines.csv` ids. One object per line:
`company` (`gold-a`, `gold-b`; no names), `statement`, `row` (print order within the
statement), `label` (caption as printed, share counts cut), `standard_line_id` (the owner's
confirmed answer). No amounts and no company names.

- Built from the owner's reviewed sheet (2026-10-21). Rows the owner marked SKIP are left out:
  components of a printed total (one level kept), subtotals with no standard line, and
  opening and closing cash balances.
- The filings and the review sheet stay in the project Library (`gold-set/`), not in git.
- Use: development measure of mapping accuracy (rule table alone, then rule table plus model).
  It is not the acceptance sample; that is the 5 pilot companies, drawn by `evals/mapping_accuracy.py`.
- Do not tune on the pilot companies. Changes to this file need the owner's confirmation.
