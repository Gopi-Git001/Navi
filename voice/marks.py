"""Timing marks: one JSON object per line, monotonic milliseconds (docs/TECH_SPEC.md section 4).

Only the names in ``MARK_NAMES`` are accepted, so a typo fails loudly instead of silently
producing a benchmark that is missing a number. Every line is flushed when written, so marks
survive a crash or a kill.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Union

MARK_NAMES = (
    "key_down",
    "key_up",
    "speech_end_label",
    "utterance_commit",
    "first_audio_delta_in",
    "first_sample_out",
    "tool_start",
    "tool_end",
    "stop_requested",
    "audio_silent",
    "browser_halted",
    "confirm_readback_start",
    "confirm_readback_end",
    "confirm_heard",
    "action_executed",
)


def monotonic_ms() -> float:
    return time.monotonic_ns() / 1e6


class MarkWriter:
    """Append marks to a JSONL file. Use as a context manager or call ``close()``."""

    def __init__(self, path: Union[str, Path], now_ms: Optional[Callable[[], float]] = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._now_ms = now_ms or monotonic_ms
        self._fh = open(self.path, "w", encoding="utf-8")

    def mark(self, name: str, t_ms: Optional[float] = None, **extra) -> float:
        """Write one mark and return its time. ``t_ms`` overrides the clock (for example the
        known end of speech from a clip label)."""
        if name not in MARK_NAMES:
            raise ValueError(f"unknown mark name {name!r}; valid names: {', '.join(MARK_NAMES)}")
        if self._fh is None:
            raise ValueError("MarkWriter is closed")
        t = float(self._now_ms() if t_ms is None else t_ms)
        row = {"name": name, "t_ms": t, **extra}
        self._fh.write(json.dumps(row) + "\n")
        self._fh.flush()
        return t

    def close(self) -> None:
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "MarkWriter":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def read_marks(path: Union[str, Path]) -> List[Dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
