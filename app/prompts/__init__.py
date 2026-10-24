"""Versioned prompt files: `<name>.<version>.md` next to this module (kept out of the code)."""

from pathlib import Path
from typing import Final

PROMPT_DIR: Final = Path(__file__).parent
DEFAULT_PROMPT_VERSION: Final = "v1"


def load_prompt(name: str, version: str | None = None) -> str:
    path = PROMPT_DIR / f"{name}.{version or DEFAULT_PROMPT_VERSION}.md"
    if path.parent != PROMPT_DIR or not path.is_file():
        raise FileNotFoundError(
            f"no prompt {name!r} at version {version or DEFAULT_PROMPT_VERSION!r}"
        )
    return path.read_text(encoding="utf-8").strip()
