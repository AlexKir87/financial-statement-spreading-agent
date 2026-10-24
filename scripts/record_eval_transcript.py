#!/usr/bin/env python3
"""Record the model transcript the offline evals replay (`evals/transcripts/spread.jsonl`).

Runs every eval case through the real graph with the rule-based `ScriptedModel` and writes each
request and its reply, keyed by a hash of the request. This is not a live model recording: it
fixes the plumbing under test. Re-run it after any change to a prompt or response schema (a
replay miss says so), then run the evals with `SPREAD_REPLAY_TRANSCRIPT` pointing at the file.

  python scripts/record_eval_transcript.py [--out evals/transcripts/spread.jsonl]
"""

import argparse
import json
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agent import run_agent  # noqa: E402
from app.tools.model_client import ImagePart, ModelReply  # noqa: E402
from app.tools.replay import record_line, request_key  # noqa: E402
from tests.scripted_model import ScriptedModel  # noqa: E402

REPLAY_ENV = "SPREAD_REPLAY_TRANSCRIPT"


class Recorder:
    def __init__(self) -> None:
        self.inner = ScriptedModel()
        self.lines: dict[str, str] = {}

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int | None = None,
        images: Sequence[ImagePart] = (),
    ) -> ModelReply:
        reply = self.inner.complete(model=model, system=system, user=user, max_tokens=max_tokens)
        key = request_key(model, system, user, images)
        self.lines[key] = record_line(key, reply)
        return reply


def main() -> int:
    import os

    import app.agent as agent

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "evals" / "transcripts" / "spread.jsonl"))
    parser.add_argument("--cases", default=str(ROOT / "evals" / "cases.jsonl"))
    args = parser.parse_args()

    recorder = Recorder()
    cases: list[dict[str, Any]] = [
        json.loads(ln) for ln in Path(args.cases).read_text().splitlines() if ln.strip()
    ]
    with tempfile.TemporaryDirectory() as tmp:
        os.environ.update(
            {
                "COGNITIA_OPENROUTER_API_KEY": "recording-no-key",
                "SPREAD_CHECKPOINT_DB": f"{tmp}/checkpoints.sqlite",
                "SPREAD_OUTPUT_DIR": f"{tmp}/out",
                "SPREAD_MAX_TOKENS_PER_RUN": "200000",
                "SPREAD_MODEL": "recorded/scripted",
            }
        )
        os.environ.pop(REPLAY_ENV, None)
        agent.client_from_env = lambda settings: recorder
        for case in cases:
            run_agent(case["input"])
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(sorted(recorder.lines.values())) + "\n", encoding="utf-8")
    print(f"recorded {len(recorder.lines)} requests from {len(cases)} cases to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
