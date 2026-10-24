# Evals

Run: `python evals/run_evals.py --threshold 0.9 --report evals/report.json`

Cases live in `cases.jsonl` (see `cases.example.jsonl` for every check type). Each case has a `category` (`happy`, `edge`, `adversarial`, `out_of_scope`; all four are required) and a list of `checks`.

| Check | Use it for |
| :--- | :--- |
| `contains`, `not_contains`, `regex`, `not_regex` | Simple text expectations and forbidden content |
| `json_field` | Extracted fields and numbers, with an optional numeric `tolerance`. The agent's output must be JSON |
| `tool_calls`, `tool_arg` | Which tools were called, in what order, with what arguments, and which were never allowed |
| `max_tokens` | Cost ceiling per case (needs `usage.total_tokens`) |
| `llm_judge` | Qualities text matching cannot see, such as refusing correctly. Needs `EVAL_JUDGE_MODEL` and `COGNITIA_ANTHROPIC_API_KEY` |

To check tool calls or usage, `run_agent` returns an `AgentResult` (see `evals/models.py`) instead of a string. Map the framework's message history to `tool_calls`.

Practical rules
- Prefer exact checks (`json_field`, `tool_calls`) over `llm_judge` wherever the answer can be checked exactly. Judge only what you must.
- Use `--repeats 3` for agents whose output varies; a case passes on a strict majority.
- Every production incident becomes a new case. Never edit a case to make a failing run pass; the fix-task hook blocks that.
- Keep a hand-checked sample of judge verdicts to make sure the judge agrees with you.
