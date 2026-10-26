"""Temporary ToolResult stand-in, used only until `common/types.py` (issue #4) is on main.

Fields match `docs/TOOL_CONTRACT.json` -> `tool_result.fields`. `research/api.py` prefers
`common.types.ToolResult` when it exists and falls back to this one. Delete this file once
#4 has merged and the fallback in `api.py` is removed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ToolResult:
    status: str  # ok | error | blocked | cancelled | awaiting_confirmation
    text: str  # first word is the status word
    image_b64: Optional[str] = None  # look only; never set by research
    gate: Optional[Any] = None  # never set by research (read-only)
