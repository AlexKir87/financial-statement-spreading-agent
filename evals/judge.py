"""LLM judge. Grades one output against a plain-language rubric.

Needs the `anthropic` package and COGNITIA_ANTHROPIC_API_KEY (falls back to
ANTHROPIC_API_KEY, which cloud sessions do not pass through). Swap in another provider if the
engagement uses one: keep the signature (rubric, input, output) -> Grade.
Set EVAL_JUDGE_MODEL to the judge model id. Use a different model from the agent's
where possible so the judge does not grade its own habits.
"""

import json
import os

from evals.models import Grade

PROMPT = """You grade an AI agent's answer against a rubric. Be strict.
<rubric>{rubric}</rubric>
<user_input>{case_input}</user_input>
<agent_output>{output}</agent_output>
Reply with only JSON: {{"pass": true or false, "reason": "one sentence"}}"""


def anthropic_judge(rubric: str, case_input: str, output: str) -> Grade:
    import anthropic  # imported here so the rest of the framework works without it

    model = os.environ.get("EVAL_JUDGE_MODEL")
    if not model:
        return Grade(False, "EVAL_JUDGE_MODEL is not set")
    api_key = os.environ.get("COGNITIA_ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return Grade(False, "COGNITIA_ANTHROPIC_API_KEY is not set")
    # Explicit base_url: a cloud session's own ANTHROPIC_BASE_URL must not redirect the judge.
    client = anthropic.Anthropic(api_key=api_key, base_url="https://api.anthropic.com")
    msg = client.messages.create(
        model=model,
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": PROMPT.format(rubric=rubric, case_input=case_input, output=output),
            }
        ],
    )
    text = "".join(getattr(block, "text", "") for block in msg.content)
    try:
        verdict = json.loads(text[text.index("{") : text.rindex("}") + 1])
        return Grade(bool(verdict["pass"]), str(verdict.get("reason", "")))
    except (ValueError, KeyError):
        return Grade(False, f"judge returned unparseable reply: {text[:80]!r}")
