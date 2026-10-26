"""JSONL session log. One object per line: {"t_ms", "event", ...safe fields}.

Privacy rule: this file never holds typed field values, page text, or secrets.
Only an allowlist of field names is written, and even those only with identifier-like
values (lowercase letters, digits and _ . : -, at most 32 chars) or finite numbers.
`source` and `reason` are stricter: only values from the closed sets SOURCES and REASONS
below are written (an identifier regex alone would let a short typed secret like "hunter2"
through).
Anything else is replaced by the fixed placeholder "redacted"; fields that are not on
the allowlist are dropped. Only the NAMES of redacted or dropped fields are recorded.

Logging must never crash a session: OSError on write is swallowed and counted in
`SessionLog.failures`.
"""
from __future__ import annotations

import json
import math
import os
import re
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
# Closed sets for the two fields that describe "who/why" and are most tempting to fill with
# free text. Anything else is written as "redacted" (and the field name goes to dropped_fields).
# Adjust here only; every value must be a lowercase identifier and not a user-typed string.
#
# SOURCES: docs/TECH_SPEC.md section 3 (on_stop source: hotkey | ptt_down | voice | console;
# on_utterance_end source: ptt_release | vad) plus the origins of events the agent logs.
SOURCES: frozenset[str] = frozenset({
    # from TECH_SPEC section 3
    "hotkey", "ptt_down", "voice", "console", "ptt_release", "vad",
    # input origins and other emitters (not enumerated by the spec)
    "ptt", "wake", "typed_test", "fake", "tool", "model", "user",
})
# REASONS: why a call, reply or action ended. Includes the contract's non-ok status words
# (TOOL_CONTRACT.json tool_result.status) and the gate/confirm rule outcomes; the rest is a
# small chosen set.
REASONS: frozenset[str] = frozenset({
    # contract status words other than ok
    "error", "blocked", "cancelled", "awaiting_confirmation",
    # chosen set (not enumerated by the spec)
    "timeout", "stop_hotkey", "barge_in", "gate_expired", "gate_mismatch",
    "rate_limit", "none",
})
_ENUMERATED: dict[str, frozenset[str]] = {"source": SOURCES, "reason": REASONS}
_RESERVED = {"t_ms", "event", "dropped_fields"}
PLACEHOLDER = "redacted"
_IDENT = re.compile(r"[a-z0-9_.:-]{1,32}")
_OPEN_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)


def _is_safe_value(value: Any, field: str = "") -> bool:
    allowed = _ENUMERATED.get(field)
    if allowed is not None:
        return isinstance(value, str) and value in allowed
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(value)
    return isinstance(value, str) and _IDENT.fullmatch(value) is not None


class SessionLog:
    def __init__(
        self,
        path: Union[str, Path],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = Path(path)
        self._clock = clock  # seconds, monotonic
        self._lock = threading.Lock()
        self.failures = 0  # lines lost to OSError (read-only disk, missing dir ...)

    def mark(self, name: str, **fields: Any) -> None:
        if name not in MARKS:
            raise ValueError(f"unknown session log mark: {name!r}")
        row: dict[str, Any] = {"t_ms": int(round(self._clock() * 1000)), "event": name}
        dropped: list[str] = []
        for k, v in fields.items():
            if k not in SAFE_FIELDS or k in _RESERVED:
                dropped.append(k)
            elif _is_safe_value(v, k):
                row[k] = v
            else:
                row[k] = PLACEHOLDER
                dropped.append(k)
        if dropped:
            row["dropped_fields"] = sorted(dropped)
        data = (json.dumps(row, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, _OPEN_FLAGS, 0o644)
                try:
                    # One write call per line in append mode: whole lines even with several writers.
                    os.write(fd, data)
                finally:
                    os.close(fd)
            except OSError:
                self.failures += 1
