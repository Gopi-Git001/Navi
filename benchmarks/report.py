"""Statistics, targets check and report table for the Navi benchmarks.

Rules (docs/BENCHMARKS.md): times are median, p95 and max; rates are shown as
numerator over denominator; zero events are shown with a 95% upper bound.
Percentiles use the nearest-rank method.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

Check = tuple  # (stat, op, value)


def _need(values):
    if not values:
        raise ValueError("no values: refusing to invent a statistic")


def median(values):
    _need(values)
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def p95(values):
    _need(values)
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def max_value(values):
    _need(values)
    return max(values)


def wilson(num: int, den: int, z: float = 1.96):
    """Wilson score interval for num successes out of den trials."""
    if den <= 0:
        raise ValueError("denominator must be positive")
    if num < 0 or num > den:
        raise ValueError("numerator must be between 0 and the denominator")
    p = num / den
    denom = 1 + z * z / den
    centre = (p + z * z / (2 * den)) / denom
    half = z * math.sqrt(p * (1 - p) / den + z * z / (4 * den * den)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def rate_text(num: int, den: int) -> str:
    if den <= 0:
        return "no data (0/0)"
    lo, hi = wilson(num, den)
    return f"{num}/{den} ({_pct(num / den)}, 95% CI {_pct(lo)}-{_pct(hi)})"


def zero_upper_text(hours: float) -> str:
    """Rule of three: 0 events in T hours -> 95% upper bound 3/T per hour."""
    if hours <= 0:
        raise ValueError("hours must be positive")
    bound = 3 / hours
    bound_text = f"{bound:g}"
    hours_text = f"{hours:g}"
    return f"0 in {hours_text} h, 95% upper bound {bound_text} per hour"


@dataclass
class Measurement:
    stats: dict = field(default_factory=dict)
    text: str = ""

    @classmethod
    def from_times(cls, times_ms):
        t = list(times_ms)
        s = {"median": median(t), "p95": p95(t), "max": max_value(t), "n": len(t)}
        text = f"median {s['median']:g}, p95 {s['p95']:g}, max {s['max']:g} ms (n={len(t)})"
        return cls(s, text)

    @classmethod
    def from_count(cls, n: int):
        return cls({"count": n}, f"{n} events")

    @classmethod
    def from_rate(cls, num: int, den: int):
        rate = num / den if den > 0 else None
        return cls({"rate": rate, "num": num, "den": den}, rate_text(num, den))

    @classmethod
    def from_drift(cls, drift: float):
        return cls({"drift": drift}, f"{drift * 100:+.1f}%")

    @classmethod
    def from_growth_mb(cls, mb: float):
        return cls({"growth_mb": mb}, f"{mb:g} MB")

    def to_dict(self):
        return {"stats": self.stats, "text": self.text}


_OPS = {
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
}


def evaluate(checks, m: Measurement) -> str:
    """MEETS, MISSES (a target was not met or a needed number is missing) or REPORTED."""
    if not checks:
        return "REPORTED"
    for stat, op, value in checks:
        got = m.stats.get(stat)
        if got is None or not _OPS[op](got, value):
            return "MISSES"
    return "MEETS"


def _primary(checks):
    stat, op, _ = checks[-1]
    return stat, op


def status_with_history(row_id, m: Measurement, checks, prev: Optional[dict]) -> str:
    status = evaluate(checks, m)
    if status != "MEETS" or not prev or row_id not in prev or not checks:
        return status
    stat, op = _primary(checks)
    before = prev[row_id].get("stats", {}).get(stat)
    now = m.stats.get(stat)
    if before is None or now is None:
        return status
    lower_is_better = op in ("<=", "==")
    if stat == "count":
        worse = now > before if lower_is_better else now < before
    else:
        tol = 0.05 * abs(before)
        worse = now > before + tol if lower_is_better else now < before - tol
    return "WORSE" if worse else status


@dataclass
class Line:
    id: str
    measure: str
    owner: str
    target: str
    last: str
    this: str
    status: str
    windows: Optional[str]


def render_table(lines) -> str:
    head = "| ID | Measure | Owner | Target | Last run | This run | Status | Windows-verified |"
    sep = "|---|---|---|---|---|---|---|---|"
    out = [head, sep]
    for l in lines:
        win = l.windows if l.windows else "—"
        out.append(
            f"| {l.id} | {l.measure} | {l.owner} | {l.target} | {l.last} | {l.this} | {l.status} | {win} |"
        )
    return "\n".join(out)
