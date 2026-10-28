"""Benchmark rows read from docs/BENCHMARKS.md, plus machine-readable checks.

The table in BENCHMARKS.md is the source of truth for ids, the measure text and
the target text. CHECKS holds the same targets as numbers so they can be judged.
Times are in milliseconds, rates are fractions (0.02 = 2%).
QA never changes a number here to make a result pass: if a target looks wrong,
tell the Architect, and only Gopi can lower one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DOC = Path(__file__).resolve().parents[1] / "docs" / "BENCHMARKS.md"

# Owner module per row (QA's mapping; the Architect confirms).
OWNERS = {
    "A1": "voice/", "A1b": "voice/", "A2": "voice/", "A3": "voice/",
    "A4": "agent/", "A5": "browser/", "A6": "browser/", "A7": "agent/",
    "A8": "agent/", "A9": "research/", "A10": "browser/",
    "B1": "voice/", "B2": "browser/", "B3": "agent/", "B4": "voice/", "B5": "voice/",
    "C1": "voice/", "C2": "voice/", "C3": "voice/", "C4": "agent/", "C5": "agent/",
    "C6": "agent/", "C7": "agent/", "C8": "browser/", "C9": "browser/",
    "C10": "agent/", "C11": "agent/", "C12": "agent/", "C13": "agent/",
    "D1": "agent/", "D2": "voice/", "D3": "voice/", "D4": "voice/", "D5": "voice/",
    "D6": "browser/", "D7": "voice/", "D8": "voice/", "D9": "voice/",
    "D10": "browser/", "D11": "research/",
    "E1": "agent/", "E2": "agent/", "E3": "voice/", "E4": "voice/",
}

_ZERO = [("count", "==", 0)]
_ALL = [("rate", ">=", 1.0)]

CHECKS = {
    "A1": [("median", "<=", 700), ("p95", "<=", 1000)],
    "A1b": [], "A3": [],
    "A2": [("median", "<=", 1000), ("p95", "<=", 1500)],
    "A4": [("p95", "<=", 25000)],
    "A5": [("p95", "<=", 8000)],
    "A6": [("p95", "<=", 15000)],
    "A7": [("p95", "<=", 12000)],
    "A8": [("p95", "<=", 20000)],
    "A9": [("p95", "<=", 180000)],
    "A10": [("p95", "<=", 400)],
    "B1": [("p95", "<=", 500)],
    "B2": [("max", "<=", 1000)],
    "B3": list(_ZERO),
    "B4": list(_ALL),
    "B5": [],
    "C1": list(_ZERO), "C2": list(_ZERO), "C3": list(_ZERO), "C4": list(_ZERO),
    "C5": list(_ZERO), "C6": list(_ALL), "C7": list(_ZERO), "C8": list(_ALL),
    "C9": list(_ZERO), "C10": list(_ZERO), "C11": list(_ZERO),
    "C12": list(_ZERO),  # actions taken before the clarifying question is answered
    "C13": list(_ALL),
    "D1": [("rate", ">=", 0.95)],
    "D2": [("rate", ">=", 0.97)],
    "D3": [("rate", ">=", 0.90)],
    "D4": [("rate", ">=", 0.90)],
    "D5": [("rate", ">=", 0.93)],
    "D6": [("rate", "<=", 0.02)],
    "D7": [("rate", "<=", 0.02)],
    "D8": [("rate", "<=", 0.05)],
    "D9": [("rate", ">=", 0.98)],
    "D10": [("rate", ">=", 0.95)],
    "D11": list(_ALL),
    "E1": list(_ZERO),
    "E2": [("growth_mb", "<=", 200)],
    "E3": [("drift", "<=", 0.20)],
    "E4": list(_ALL),
}


@dataclass(frozen=True)
class Row:
    id: str
    measure: str
    target: str
    owner: str


_ROW = re.compile(r"^\|\s*([A-E]\d+[a-z]?)\s*\|(.+)\|(.+)\|\s*$")


def _clean(text: str) -> str:
    return re.sub(r"\*\*", "", text).strip()


def load_rows(path=DEFAULT_DOC):
    text = Path(path).read_text(encoding="utf-8")
    found = []
    for line in text.splitlines():
        m = _ROW.match(line)
        if m:
            rid = m.group(1)
            found.append(Row(rid, _clean(m.group(2)), _clean(m.group(3)), OWNERS.get(rid, "UNKNOWN/")))
    if not found:
        raise ValueError(f"no benchmark rows found in {path}")
    return found
