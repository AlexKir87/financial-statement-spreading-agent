"""Replay a recorded model transcript instead of calling a provider (CI evals, offline runs).

A transcript is a JSONL file; each line is `{"request_sha256": ..., "reply": ...,
"input_tokens": n, "output_tokens": n}`. The hash covers the model id, system prompt, user prompt
and any image bytes. A request with no recorded reply raises `ReplayMiss` (never a guess), so a
changed prompt or schema shows up as a failure and the transcript is re-recorded. Replay proves
the graph's plumbing; it says nothing about a live model's accuracy.
"""

import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

from app.state import Usage
from app.tools.model_client import ImagePart, ModelReply


class ReplayMiss(LookupError):
    """No recorded reply for this request. The message holds the key, never the prompt."""


def request_key(model: str, system: str, user: str, images: Sequence[ImagePart] = ()) -> str:
    digest = hashlib.sha256()
    for part in (model, system, user):
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    for image in images:
        digest.update(hashlib.sha256(image.data).digest())
    return digest.hexdigest()


def record_line(key: str, reply: ModelReply) -> str:
    return json.dumps(
        {
            "request_sha256": key,
            "reply": reply.text,
            "input_tokens": reply.usage.input_tokens,
            "output_tokens": reply.usage.output_tokens,
        },
        sort_keys=True,
    )


class ReplayClient:
    def __init__(self, path: Path | str) -> None:
        self._replies: dict[str, ModelReply] = {}
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            self._replies[row["request_sha256"]] = ModelReply(
                text=row["reply"],
                usage=Usage(input_tokens=row["input_tokens"], output_tokens=row["output_tokens"]),
            )

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int | None = None,
        images: Sequence[ImagePart] = (),
    ) -> ModelReply:
        key = request_key(model, system, user, images)
        try:
            return self._replies[key]
        except KeyError:
            raise ReplayMiss(f"no recorded reply for request {key[:12]}") from None
