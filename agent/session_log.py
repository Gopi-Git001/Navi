"""JSONL session log. One object per line: {"t_ms", "event", ...safe fields}.

Privacy rule: this file never holds typed field values, page text, or secrets.
Only an allowlist of short code-like fields is written; everything else is dropped
and only the dropped field NAMES are recorded.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable, Union

# Mark names from docs/TECH_SPEC.md section 4 ("Timing marks").
MARKS: tuple[str, ...] = (
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

# Short identifiers and numbers only. Free-text names (text, value, password,
# page_text, secret, key, token, error, target ...) are deliberately absent.
SAFE_FIELDS = frozenset({
    "tool", "call_id", "status", "risk", "verb", "source", "turn", "reason",
    "count", "duration_ms", "run_id", "clip",
})
_RESERVED = {"t_ms", "event", "dropped_fields"}
_MAX_STR = 64


def _is_safe_value(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and len(value) <= _MAX_STR and "\n" not in value


class SessionLog:
    def __init__(
        self,
        path: Union[str, Path],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path)
        self._clock = clock  # seconds, monotonic
        self._lock = threading.Lock()

    def mark(self, name: str, **fields: Any) -> None:
        if name not in MARKS:
            raise ValueError(f"unknown session log mark: {name!r}")
        row: dict[str, Any] = {"t_ms": int(round(self._clock() * 1000)), "event": name}
        dropped: list[str] = []
        for k, v in fields.items():
            if k in SAFE_FIELDS and k not in _RESERVED and _is_safe_value(v):
                row[k] = v
            else:
                dropped.append(k)
        if dropped:
            row["dropped_fields"] = sorted(dropped)
        line = json.dumps(row, ensure_ascii=True, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
                f.flush()
