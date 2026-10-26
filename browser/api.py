"""BrowserController: the exact interface the agent calls (issue #10, M0).

One async method per browser-owned tool in ``docs/TOOL_CONTRACT.json`` (same
name, same arguments), plus ``halt()`` and ``describe_pending_action()``.
``tests/unit/test_browser_api.py`` fails if this file drifts from the contract.

Rules that every implementation (the fake now, real Chrome later) keeps:
  * every action returns a ``ToolResult`` that carries the updated page state;
  * ``browser/`` never decides approval: when an action's risk is not "none" it
    raises ``Gated`` *instead of acting*; the confirm gate (agent/safety.py,
    owned by Agent Brain) decides and later runs the held call itself, once;
  * password, card, CVV, SSN, bank and account fields are never typed into;
  * element ids are valid only for the latest result and numbered 1..N in the
    order the page reports them (numbering is not changed here).

Shared types (``ToolResult``, ``ActionDescription``, ``Gated``) come from
``common/types.py`` (issue #4). ``ToolResult`` has no page field, so the updated
page state is in ``ToolResult.text`` (status word first, then title, URL and the
numbered elements). ``PageState`` below is the browser-side structure behind it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Protocol, runtime_checkable

from common.types import RISKS, ActionDescription, Gated, Risk, Status, ToolResult  # noqa: F401

# --------------------------------------------------------------------------
# Types. ToolResult, ActionDescription, Gated, STATUSES, RISKS come from
# common/types.py (issue #4). ToolCall and PageState are browser-side only.
# --------------------------------------------------------------------------

#: Risk classes that need the spoken "confirm" (contract: risk_classes_requiring_confirm).
GATED_RISKS: frozenset[str] = frozenset(r for r in RISKS if r != "none")


def is_gated(description: ActionDescription) -> bool:
    return description.risk in GATED_RISKS


def binding(description: ActionDescription) -> tuple[str, str, str, str]:
    """What an approval is tied to (decision 20): risk, verb, target, exact text.

    ``target`` is always ``"<page url> | <element ref>"`` plus an optional
    ``" | <label>"`` (for example ``"https://x.test/post/123 | composer ref 14 |
    reply to @alice's post"``), so two posts or two composers never share one. ``readback`` is excluded: the
    gate fills it in (agent/safety.py), the browser does not.
    """
    return (description.risk, description.verb, description.target, description.text)


@dataclass(frozen=True)
class ToolCall:
    """A tool call as the agent makes it: contract tool name plus its arguments.

    TODO(#4 follow-up): move to common/types.py if Agent Brain adds one.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ElementInfo:
    """One numbered element in a page state (ids 1..N in the order reported)."""

    id: int
    role: str
    name: str
    value: str = ""  # "(hidden)" for sensitive fields; never the typed secret
    sensitive: bool = False
    in_dialog: bool = False


@dataclass(frozen=True)
class PageState:
    url: str
    title: str
    elements: tuple[ElementInfo, ...] = ()


# --------------------------------------------------------------------------
# The interface
# --------------------------------------------------------------------------

@runtime_checkable
class BrowserController(Protocol):
    """One method per browser-owned tool in docs/TOOL_CONTRACT.json (v1.0)."""

    async def navigate(self, url: str) -> ToolResult: ...

    async def search_web(
        self,
        query: str,
        engine: Optional[Literal["google", "bing", "duckduckgo", "youtube", "amazon", "x"]] = None,
    ) -> ToolResult: ...

    async def observe(self, max_items: Optional[int] = None) -> ToolResult: ...

    async def look(self, annotate: Optional[bool] = None) -> ToolResult: ...

    async def read_page(
        self, mode: Optional[Literal["main", "full"]] = None, start: Optional[int] = None
    ) -> ToolResult: ...

    async def click(
        self,
        element_id: int,
        double: Optional[bool] = None,
        button: Optional[Literal["left", "right", "middle"]] = None,
    ) -> ToolResult: ...

    async def click_at(self, x: float, y: float, double: Optional[bool] = None) -> ToolResult: ...

    async def move_cursor(
        self,
        element_id: Optional[int] = None,
        x: Optional[float] = None,
        y: Optional[float] = None,
    ) -> ToolResult: ...

    async def hover(self, element_id: int) -> ToolResult: ...

    async def type_text(
        self,
        element_id: int,
        text: str,
        press_enter: Optional[bool] = None,
        clear: Optional[bool] = None,
    ) -> ToolResult: ...

    async def fill_form(self, fields: list[dict[str, Any]]) -> ToolResult: ...

    async def select_option(self, element_id: int, option: str) -> ToolResult: ...

    async def scroll(
        self,
        direction: Literal["down", "up", "top", "bottom", "left", "right"],
        amount: Optional[float] = None,
        element_id: Optional[int] = None,
    ) -> ToolResult: ...

    async def find_text(self, text: str) -> ToolResult: ...

    async def press_key(self, key: str) -> ToolResult: ...

    async def history(self, action: Literal["back", "forward", "reload"]) -> ToolResult: ...

    async def tabs(
        self,
        action: Literal["list", "new", "switch", "close"],
        index: Optional[int] = None,
        url: Optional[str] = None,
    ) -> ToolResult: ...

    async def wait(self, seconds: Optional[float] = None) -> ToolResult: ...

    # ---- not tools -------------------------------------------------------

    async def halt(self) -> None:
        """Cancel the running action and every queued one; return within 1 s.

        Leaves the page as it is. Releases held keys/buttons. Actions started
        after ``halt()`` returns run normally.
        """
        ...

    async def describe_pending_action(self, call: ToolCall) -> ActionDescription:
        """Describe what ``call`` would do, from the page, without doing it.

        ``risk == "none"`` for harmless calls. Used by the gate for read-backs.
        """
        ...

    async def approve(self, description: ActionDescription) -> None:
        """Open a one-shot approval for exactly ``description`` (the gate calls this).

        * Consumed only by the next action whose description matches exactly
          (risk, verb, target, text); then the same action raises ``Gated`` again.
        * Approving again replaces the earlier approval (never stacks) and
          restarts the expiry window.
        * A different gated action raises ``Gated`` and leaves the approval alone.
        * Expires if unused (default 30 s, injectable clock in the fake).
        * Cleared by navigation, tab change, history and ``halt()``.
        * If composer text or target differ at execution time the call is refused
          and ``Gated`` is raised with the new description (decision 20).
        * Only this method approves. No tool argument can: the contract has no
          ``confirmed`` argument, and passing one is a ``TypeError``.
        """
        ...
