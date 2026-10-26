"""Shared result types, exactly as in docs/TOOL_CONTRACT.json (tool_result, risk classes)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal, Optional, get_args

Status = Literal["ok", "error", "blocked", "cancelled", "awaiting_confirmation"]
Risk = Literal["none", "post", "comment", "form_submit", "dm", "email", "delete", "purchase"]

STATUSES: tuple[str, ...] = get_args(Status)
RISKS: tuple[str, ...] = get_args(Risk)


@dataclass(frozen=True)
class ActionDescription:
    """What a pending risky action would do. Also the contract's `gate` object.

    `readback` is the exact phrase code speaks; it is filled in by agent/safety.py.
    """

    risk: Risk
    verb: str
    target: str
    text: str
    readback: str = ""

    def __post_init__(self) -> None:
        if self.risk not in RISKS:
            raise ValueError(f"unknown risk class: {self.risk!r}")


@dataclass(frozen=True)
class ToolResult:
    """Every tool returns this. The model sees text and, for `look`, an image.

    Contract: the first line of `text` starts with the status word (then end of line or a
    non-word character), e.g. "ok: clicked Reply".
    """

    status: Status
    text: str
    image_b64: Optional[str] = field(default=None, repr=False)
    gate: Optional[ActionDescription] = None

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status: {self.status!r}")
        first_line = self.text.split("\n", 1)[0]
        if not re.match(re.escape(self.status) + r"(?![A-Za-z0-9_])", first_line):
            # Never echo the text: it may hold page content or typed values.
            raise ValueError(f"text must start with the status word {self.status!r}")
        if self.status == "awaiting_confirmation" and self.gate is None:
            raise ValueError("awaiting_confirmation requires a gate")
        if self.status != "awaiting_confirmation" and self.gate is not None:
            raise ValueError("gate is only allowed with awaiting_confirmation")


class Gated(Exception):
    """Raised by browser/ instead of acting when risk != none. Never decides approval."""

    def __init__(self, description: ActionDescription) -> None:
        # Message carries no typed text: exceptions end up in logs.
        super().__init__(f"gated: {description.risk} {description.verb}")
        self.description = description
