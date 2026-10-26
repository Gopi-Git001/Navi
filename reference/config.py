"""Configuration loaded from environment / .env."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def _bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    openai_api_key: str
    realtime_model: str
    text_model: str
    transcribe_model: str
    voice: str
    browser_channel: str | None
    headless: bool
    profile_dir: Path
    output_dir: Path
    start_url: str
    ptt_key: str

    @classmethod
    def load(cls) -> "Config":
        load_dotenv(ROOT / ".env", override=True)
        key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            sys.exit("OPENAI_API_KEY is missing. Copy .env.example to .env and fill it in.")
        default_channel = "chrome"
        channel = os.getenv("BROWSER_CHANNEL", default_channel).strip() or None
        out = Path(os.getenv("OUTPUT_DIR", ROOT / "output"))
        prof = Path(os.getenv("PROFILE_DIR", ROOT / ".browser_profile"))
        out.mkdir(parents=True, exist_ok=True)
        prof.mkdir(parents=True, exist_ok=True)
        return cls(
            openai_api_key=key,
            realtime_model=os.getenv("REALTIME_MODEL", "gpt-realtime"),
            text_model=os.getenv("TEXT_MODEL", "gpt-4.1-mini"),
            transcribe_model=os.getenv("TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe"),
            voice=os.getenv("VOICE", "marin"),
            browser_channel=channel,
            headless=_bool("HEADLESS", False),
            profile_dir=prof,
            output_dir=out,
            start_url=os.getenv("START_URL", "https://www.google.com"),
            ptt_key=os.getenv("PTT_KEY", "ctrl_r"),
        )
