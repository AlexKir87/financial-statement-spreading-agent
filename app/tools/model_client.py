"""The one model client: OpenRouter behind a small protocol, schema-validated output.

`StructuredCaller` is what the nodes use. It validates every reply against a Pydantic model,
retries once with the validation error, and refuses further calls once the run's token cap is
spent. Model ids are passed in from settings; none is written here. The provider's own
structured-output feature is not relied on (support for the chosen model is `unknown`): the
schema goes in the prompt and the reply is validated here.
"""

import base64
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Protocol, TypeVar

from pydantic import BaseModel, SecretStr, ValidationError

from app.config import ConfigError, Settings
from app.prompts import DEFAULT_PROMPT_VERSION
from app.state import Usage, sum_usage
from app.telemetry import log_event

# Not in docs/reference/ (OpenRouter facts are `unknown` there); confirmed by the first live run.
OPENROUTER_BASE_URL: Final = "https://openrouter.ai/api/v1"
MAX_REPLY_TOKENS: Final = 8192  # per-reply ceiling; the run cap still applies
MAX_ATTEMPTS: Final = 2  # first call plus one retry with the validation error

M = TypeVar("M", bound=BaseModel)


class ModelUnavailable(RuntimeError):
    """Provider outage, rate limit or auth failure after the SDK's own backoff.

    Nodes do not catch it: the run pauses at the last checkpoint and resumes with the same
    `thread_id` (architecture, section 8). The message carries the error type only.
    """


class TokenBudgetExceeded(RuntimeError):
    """The run's token cap is spent; no further model call is made."""


class InvalidModelOutput(ValueError):
    """The reply failed validation twice. The message holds field paths, never reply text."""


@dataclass(frozen=True)
class ImagePart:
    """One page image sent with a prompt (vision route). `mime` is image/jpeg or image/png."""

    data: bytes = field(repr=False)
    mime: str


@dataclass(frozen=True)
class ModelReply:
    text: str
    usage: Usage


class ModelClient(Protocol):
    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int | None = None,
        images: Sequence[ImagePart] = (),
    ) -> ModelReply: ...


class OpenRouterClient:
    """OpenAI-compatible chat call to OpenRouter. The key is a `SecretStr` and is never logged."""

    def __init__(
        self,
        api_key: SecretStr,
        *,
        base_url: str = OPENROUTER_BASE_URL,
        sdk: Any = None,
        max_retries: int = 3,
        timeout_seconds: float = 120.0,
    ) -> None:
        if sdk is None:
            import openai  # imported here so tests and non-model runs need no network setup

            sdk = openai.OpenAI(
                api_key=api_key.get_secret_value(),
                base_url=base_url,
                max_retries=max_retries,
                timeout=timeout_seconds,
            )
        self._sdk = sdk

    def __repr__(self) -> str:
        return "OpenRouterClient()"

    def complete(
        self,
        *,
        model: str,
        system: str,
        user: str,
        max_tokens: int | None = None,
        images: Sequence[ImagePart] = (),
    ) -> ModelReply:
        import openai

        content: str | list[dict[str, Any]] = user
        if images:
            content = [{"type": "text", "text": user}] + [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{i.mime};base64,{base64.b64encode(i.data).decode()}"
                    },
                }
                for i in images
            ]
        try:
            resp = self._sdk.chat.completions.create(
                model=model,
                temperature=0,
                **({} if max_tokens is None else {"max_tokens": max_tokens}),
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": content},
                ],
            )
        except openai.BadRequestError:
            raise InvalidModelOutput("provider rejected the request") from None
        except openai.OpenAIError as exc:
            raise ModelUnavailable(type(exc).__name__) from None
        choices = getattr(resp, "choices", None)
        if not choices:  # a 200 carrying an error body
            raise ModelUnavailable("EmptyResponse")
        text = choices[0].message.content or ""
        usage = getattr(resp, "usage", None)
        if (
            usage is not None
            and usage.prompt_tokens is not None
            and usage.completion_tokens is not None
        ):
            counted = Usage(input_tokens=usage.prompt_tokens, output_tokens=usage.completion_tokens)
        else:  # no counts from the provider: estimate (~4 characters a token) so the cap bites
            counted = Usage(input_tokens=len(system + user) // 4, output_tokens=len(text) // 4)
        return ModelReply(text=text, usage=counted)


@dataclass
class TokenBudget:
    """Counts every call. `cap` is `SPREAD_MAX_TOKENS_PER_RUN`, `used` what earlier nodes spent."""

    cap: int
    used: int = 0

    def check(self) -> None:
        if self.used >= self.cap:
            raise TokenBudgetExceeded("token cap reached")

    def charge(self, usage: Usage) -> None:
        self.used += usage.total_tokens


_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL)


def _summarise(exc: ValidationError | json.JSONDecodeError | ValueError) -> str:
    """Field paths and rule messages only: the offending input is never echoed."""
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'reply'}: {e['msg']}" for e in exc.errors()
        )
    if isinstance(exc, json.JSONDecodeError):
        return "reply is not valid JSON"
    return str(exc)


@dataclass
class StructuredCaller:
    client: ModelClient
    model: str
    budget: TokenBudget
    prompt_version: str
    logger: logging.Logger | None = None
    usage: Usage = field(default_factory=Usage)

    def call(
        self,
        *,
        system: str,
        user: str,
        schema: type[M],
        check: Callable[[M], None] | None = None,
        images: Sequence[ImagePart] = (),
    ) -> M:
        """One validated reply. `check` raises `ValueError` for rules the schema cannot hold."""
        schema_text = json.dumps(schema.model_json_schema(), sort_keys=True)
        prompt = (
            f"{user}\n\nReply with one JSON object only, matching this JSON schema:\n{schema_text}"
        )
        problem = ""
        for _ in range(MAX_ATTEMPTS):
            self.budget.check()
            ask = prompt
            if problem:
                ask += f"\n\nYour previous reply was rejected: {problem}\nReply with JSON only."
            reply = self.client.complete(
                model=self.model,
                system=system,
                user=ask,
                max_tokens=max(1, min(self.budget.cap - self.budget.used, MAX_REPLY_TOKENS)),
                **({"images": images} if images else {}),
            )
            self.budget.charge(reply.usage)
            self.usage = sum_usage(self.usage, reply.usage)
            if self.logger is not None:
                log_event(
                    self.logger,
                    "model.call",
                    model_id=self.model,
                    prompt_version=self.prompt_version,
                    input_tokens=reply.usage.input_tokens,
                    output_tokens=reply.usage.output_tokens,
                )
            body = _FENCE.sub(r"\1", reply.text)
            try:
                parsed = schema.model_validate_json(body)
                if check is not None:
                    check(parsed)
            except (ValidationError, ValueError) as exc:  # JSON errors are ValidationErrors here
                problem = _summarise(exc)
                continue
            return parsed
        raise InvalidModelOutput(problem)


def make_caller(
    settings: Settings,
    client: ModelClient,
    *,
    tokens_used: int = 0,
    logger: logging.Logger | None = None,
) -> StructuredCaller:
    """A caller for one node: model id and prompt version from settings."""
    if not settings.model_id:
        raise ConfigError("missing required environment variables: SPREAD_MODEL")
    return StructuredCaller(
        client=client,
        model=settings.model_id,
        budget=TokenBudget(cap=settings.max_tokens_per_run, used=tokens_used),
        prompt_version=settings.prompt_version or DEFAULT_PROMPT_VERSION,
        logger=logger,
    )
