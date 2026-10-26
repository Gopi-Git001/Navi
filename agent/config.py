"""Configuration from the environment and an optional .env file. Never logs the key."""
from __future__ import annotations

import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOTENV = ROOT / ".env"


class ConfigError(RuntimeError):
    """Configuration is unusable. The message never contains secret values."""


# Chrome's everyday user-data dirs, as lowercase path segments. A PROFILE_DIR that contains
# one of these runs anywhere in its path is refused (Windows, Linux, macOS; backslashes ok).
_EVERYDAY_CHROME = (
    ("google", "chrome", "user data"),                  # %LOCALAPPDATA%/Google/Chrome/User Data
    (".config", "google-chrome"),                       # Linux
    (".config", "chromium"),                            # Linux
    ("application support", "google", "chrome"),        # macOS
)
_WIN_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")


def _expand_percent(raw: str, env: Mapping[str, str]) -> str:
    def sub(m: "re.Match[str]") -> str:
        return env.get(m.group(1)) or os.environ.get(m.group(1)) or m.group(0)

    return re.sub(r"%([A-Za-z0-9_]+)%", sub, raw)


def _segments(raw: str) -> list[str]:
    norm = posixpath.normpath(raw.replace("\\", "/")).casefold()
    return [seg for seg in norm.split("/") if seg and seg != "."]


def _is_everyday_chrome(*candidates: str) -> bool:
    for raw in candidates:
        segs = _segments(raw)
        for pattern in _EVERYDAY_CHROME:
            n = len(pattern)
            if any(tuple(segs[i:i + n]) == pattern for i in range(len(segs) - n + 1)):
                return True
    return False


def _resolve_profile_dir(raw: str, env: Mapping[str, str]) -> Path:
    """Relative paths resolve against the repo root (never the cwd). Refuses everyday Chrome."""
    expanded = _expand_percent(raw, env)
    candidates = [raw, expanded]
    if _WIN_ABS.match(expanded):
        path = Path(expanded)  # a Windows absolute path, kept as given
    else:
        path = Path(os.path.expanduser(expanded))
        if not path.is_absolute():
            path = ROOT / path
        path = Path(os.path.normpath(path))
        candidates += [str(path), str(path.resolve())]  # resolve() follows symlinks
    if _is_everyday_chrome(*candidates):
        raise ConfigError(
            "PROFILE_DIR points at Chrome's everyday profile. Navi only uses its own "
            "dedicated profile folder (default: .browser_profile/ in the repo). "
            "Pick another folder or remove PROFILE_DIR."
        )
    return path


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
            profile_dir=_resolve_profile_dir(_get(env, "PROFILE_DIR", str(ROOT / ".browser_profile")), env),
            start_url=_get(env, "START_URL", "https://www.google.com"),
        )
