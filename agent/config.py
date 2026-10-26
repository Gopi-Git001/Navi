"""Configuration from the environment and an optional .env file. Never logs the key."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOTENV = ROOT / ".env"


class ConfigError(RuntimeError):
    """Configuration is unusable. The message never contains secret values."""


def _get(env: Mapping[str, str], name: str, default: str) -> str:
    value = (env.get(name) or "").strip()
    return value or default


@dataclass(frozen=True)
class Config:
    openai_api_key: str = field(repr=False)
    realtime_model: str
    text_model: str
    voice: str
    ptt_key: str
    browser_channel: str
    profile_dir: Path
    start_url: str

    @classmethod
    def load(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        dotenv_path: Optional[Path] = DEFAULT_DOTENV,
    ) -> "Config":
        """Values from `dotenv_path` (if it exists), overridden by `environ`.

        Does not modify os.environ and has no side effects (no folders created).
        """
        env: dict[str, str] = {}
        if dotenv_path is not None and Path(dotenv_path).is_file():
            env.update({k: v for k, v in dotenv_values(dotenv_path).items() if v is not None})
        env.update(os.environ if environ is None else environ)

        key = (env.get("OPENAI_API_KEY") or "").strip()
        if not key:
            raise ConfigError(
                "OPENAI_API_KEY is missing. Copy .env.example to .env and fill it in."
            )
        return cls(
            openai_api_key=key,
            realtime_model=_get(env, "REALTIME_MODEL", "gpt-realtime"),
            text_model=_get(env, "TEXT_MODEL", "gpt-4.1-mini"),
            voice=_get(env, "VOICE", "marin"),
            ptt_key=_get(env, "PTT_KEY", "ctrl_r"),
            browser_channel=_get(env, "BROWSER_CHANNEL", "chrome"),
            profile_dir=Path(_get(env, "PROFILE_DIR", str(ROOT / ".browser_profile"))),
            start_url=_get(env, "START_URL", "https://www.google.com"),
        )
