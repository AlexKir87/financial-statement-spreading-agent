"""Run settings read from environment variables by name (the spec "Environment variables").

Secret values are held as `SecretStr` so a repr, log line or traceback never prints them.
Errors name the variable, never its value.
"""

import os
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

ENV_OPENROUTER_API_KEY = "COGNITIA_OPENROUTER_API_KEY"
ENV_ANTHROPIC_API_KEY = "COGNITIA_ANTHROPIC_API_KEY"
ENV_EVAL_JUDGE_MODEL = "EVAL_JUDGE_MODEL"
ENV_CHECKPOINT_DB = "SPREAD_CHECKPOINT_DB"
ENV_OUTPUT_DIR = "SPREAD_OUTPUT_DIR"
ENV_MAX_REPAIR_ATTEMPTS = "SPREAD_MAX_REPAIR_ATTEMPTS"
ENV_MAX_TOKENS_PER_RUN = "SPREAD_MAX_TOKENS_PER_RUN"
ENV_PROMPT_VERSION = "SPREAD_PROMPT_VERSION"
ENV_MODEL = "SPREAD_MODEL"

REQUIRED_ENV = (ENV_OPENROUTER_API_KEY, ENV_CHECKPOINT_DB, ENV_OUTPUT_DIR, ENV_MAX_TOKENS_PER_RUN)
DEFAULT_MAX_REPAIR_ATTEMPTS = 2


class ConfigError(ValueError):
    """Settings are missing or invalid. The message carries variable names only."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    openrouter_api_key: SecretStr
    checkpoint_db: Path
    output_dir: Path
    max_tokens_per_run: int = Field(gt=0)
    max_repair_attempts: int = Field(default=DEFAULT_MAX_REPAIR_ATTEMPTS, ge=0)
    prompt_version: str | None = None
    model_id: str | None = None  # OpenRouter model id for the model nodes; chosen by evals
    anthropic_api_key: SecretStr | None = None  # eval judge only
    eval_judge_model: str | None = None
    # Two units of the reported scale count as rounding (spec, Flagged concern 8; plan step 29,
    # was 1). One setting, so the client's answer is a one-line change.
    rounding_tolerance_units: Decimal = Field(default=Decimal(2), ge=0)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if environ is None else environ
        missing = [name for name in REQUIRED_ENV if not env.get(name)]
        if missing:
            raise ConfigError("missing required environment variables: " + ", ".join(missing))
        raw: dict[str, object] = {
            "openrouter_api_key": env[ENV_OPENROUTER_API_KEY],
            "checkpoint_db": env[ENV_CHECKPOINT_DB],
            "output_dir": env[ENV_OUTPUT_DIR],
            "max_tokens_per_run": env[ENV_MAX_TOKENS_PER_RUN],
        }
        optional = {
            "max_repair_attempts": ENV_MAX_REPAIR_ATTEMPTS,
            "prompt_version": ENV_PROMPT_VERSION,
            "model_id": ENV_MODEL,
            "anthropic_api_key": ENV_ANTHROPIC_API_KEY,
            "eval_judge_model": ENV_EVAL_JUDGE_MODEL,
        }
        for field, name in optional.items():
            if env.get(name):
                raw[field] = env[name]
        try:
            return cls.model_validate(raw)
        except ValidationError as exc:
            # Report which variable is wrong, without echoing the offending value.
            by_field = {
                **{f: n for f, n in optional.items()},
                **{
                    "openrouter_api_key": ENV_OPENROUTER_API_KEY,
                    "checkpoint_db": ENV_CHECKPOINT_DB,
                    "output_dir": ENV_OUTPUT_DIR,
                    "max_tokens_per_run": ENV_MAX_TOKENS_PER_RUN,
                },
            }
            bad = sorted({by_field.get(str(e["loc"][0]), str(e["loc"][0])) for e in exc.errors()})
            raise ConfigError("invalid environment variables: " + ", ".join(bad)) from None
