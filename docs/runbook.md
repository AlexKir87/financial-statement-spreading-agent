# Runbook: spreading agent

Who approves what: the owner approves any change to the code, the prompts, the model id, the tolerance and the line set (a reviewed PR), and decides what is sent to the client. A client analyst approves or corrects a spread in the workbook. The agent approves nothing and sends nothing.

## Common situations
| What you see | What it means | What to do |
|---|---|---|
| Run ends `not_delivered` with `unsupported_language` or `unreadable_source` | A language gate or an unreadable scan stopped the run before any number was written | Check the source file; do not retry blindly. Scans need the scan route, which is unmeasured |
| `delivered_with_exceptions` | A check still fails after the repair loop; the exact difference is in `Reconciliations` and `Open_Issues` | Read the explanation. If the cause is in the filing (for example restricted cash), keep it as a stated exception. If it is a wrong mapping, the analyst corrects it in the `Mapping` sheet and the owner runs `run_corrections` |
| `token_cap_reached` in open issues | The run hit `SPREAD_MAX_TOKENS_PER_RUN`; later pages were not read | Raise the cap deliberately and run again; do not skip the issue |
| `libreoffice_unavailable`, `finalize` blocked | `soffice` is missing on the host | Install LibreOffice; the workbook is not delivered unverified |
| `extracted_line_not_in_source`, `value_in_blank_column` | A line the model returned was dropped because it was not printed on that row or sat in an empty column | No number was invented. If a check fails because of it, run again; the model is not deterministic |
| `unlabeled_total_inferred` | A bare number row was named from the sums above it | Informational. `unlabeled_row_not_resolved` means a bare row was left unread |
| `low_confidence_mapping` | The model mapped a label with confidence below 0.7 | Analyst checks that line |
| Provider outage or rate limit | OpenRouter unavailable | Wait, then resume with the same `run_id` (`thread_id`); finished nodes do not re-run |
| Process crash mid-run | | Resume from the last checkpoint with the same `thread_id` |
| Wrong or edited corrections file | The file is from another run or lacks the mapping columns | It is refused; a bad row is skipped with an open issue. Use the file from the run's own latest workbook |

## Roll back
- Workbooks are never overwritten: each run or correction writes `<run_id>_vN.xlsx`. To go back, use the earlier version.
- State can be rewound from the SQLite checkpoints (same `thread_id`).
- A code or prompt change is reverted by reverting its PR. A prompt change needs the eval transcript re-recorded.
- Changing the model id (`SPREAD_MODEL`) is a configuration change: run the five-company live run and the mapping accuracy sample again before relying on it.

## Before using on a new client filing
1. Confirm the data-handling answer for sending pages to OpenRouter exists (retention terms are `unknown`).
2. Put the filing in the Library; never in git.
3. Run, then read `Open_Issues` before the workbook.
4. The owner reviews and sends. The agent cannot.
