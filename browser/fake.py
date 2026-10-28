"""In-memory FakeBrowser for Agent Brain and QA (issue #10, M0). No Playwright.

It implements ``browser.api.BrowserController`` against scripted pages. It does
NOT contain gate logic: a scripted element carries the page facts (role, risk
class, target, composer text, sensitive flag) and the fake raises ``Gated`` for
any element whose scripted risk is not "none". Whether to approve is the
caller's business (agent/safety.py).

Test knobs: ``pages``, ``calls`` (every call with its outcome), ``executed``
(descriptions of risky actions that really ran), ``type_delay`` (seconds per
typed character, to simulate a long action that ``halt()`` must stop).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Optional

from browser.api import ElementInfo, PageState, ToolCall, binding, is_gated
from common.types import ActionDescription, Gated, ToolResult

FAKE_JPEG_B64 = "ZmFrZS1qcGVn"  # base64("fake-jpeg")
SEND_KEYS = {"enter", "ctrl+enter", "meta+enter", "cmd+enter", "control+enter"}
ACTIVATE_KEYS = {"enter", "space", " "}
HALT_TICK = 0.01  # every wait/typing loop checks the halt flag at least this often


@dataclass
class FakeElement:
    """A scripted element: the page facts the real browser must supply."""

    name: str
    role: str = "button"  # button, link, textbox, menuitem, combobox, ...
    key: str = ""  # lookup key for tests and ``controls``
    risk: str = "none"  # risk class if activating this sends/deletes/etc.
    verb: str = ""  # defaults from risk ("post", "delete", ...)
    target: str = ""  # e.g. "reply to @alice's post"
    ref: str = ""  # stable element ref, e.g. "composer ref 14"; defaults to "<key or role> ref <n>"
    editable: bool = False  # composer / text box
    value: str = ""  # editable content (composer text read from the page)
    controls: str = ""  # key of the composer this button sends
    sensitive: bool = False  # password/card/CVV/SSN/bank/account/ID field
    in_dialog: bool = False
    reveals: list["FakeElement"] = field(default_factory=list)  # e.g. opens reply box
    box: tuple[float, float, float, float] = (0, 0, 0, 0)  # x, y, w, h


@dataclass
class FakePage:
    url: str
    title: str = ""
    text: str = ""
    elements: list[FakeElement] = field(default_factory=list)


@dataclass
class RecordedCall:
    name: str
    args: dict[str, Any]
    outcome: str  # ok | error | blocked | cancelled | gated


class FakeBrowser:
    def __init__(
        self,
        page: Optional[FakePage] = None,
        type_delay: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
        approval_ttl: float = 30.0,
    ):
        self._clock = clock  # injectable so tests never really sleep
        self.approval_ttl = approval_ttl
        self.page = page or FakePage(url="about:blank", title="blank")
        self.pages: dict[str, FakePage] = {}
        self.type_delay = type_delay
        self.calls: list[RecordedCall] = []
        self.executed: list[ActionDescription] = []
        self.focused: Optional[int] = None
        self.tab_urls: list[str] = [self.page.url]
        self._history: list[str] = [self.page.url]
        self._hist_pos = 0
        self._epoch = 0
        self._active = 0
        self._halt = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._lock = asyncio.Lock()
        self._approved: Optional[tuple[ActionDescription, float]] = None  # (description, expires_at)

    # ---- helpers ---------------------------------------------------------

    def _el(self, element_id: int) -> Optional[FakeElement]:
        if 1 <= element_id <= len(self.page.elements):
            return self.page.elements[element_id - 1]
        return None

    def element_id(self, key_or_name: str) -> int:
        """Test helper: the current id (1..N) of an element by key or name."""
        for i, e in enumerate(self.page.elements, 1):
            if key_or_name in (e.key, e.name):
                return i
        raise KeyError(key_or_name)

    def _by_key(self, key: str) -> Optional[FakeElement]:
        return next((e for e in self.page.elements if e.key == key), None)

    def page_state(self) -> PageState:
        """Test helper: the structured page state behind the last result text."""
        return self._state()

    def _state(self) -> PageState:
        els = tuple(
            ElementInfo(
                id=i,
                role=e.role,
                name=e.name,
                value="(hidden)" if e.sensitive and e.value else ("" if e.sensitive else e.value),
                sensitive=e.sensitive,
                in_dialog=e.in_dialog,
            )
            for i, e in enumerate(self.page.elements, 1)
        )
        return PageState(url=self.page.url, title=self.page.title, elements=els)

    def _result(self, status: str, msg: str, **kw: Any) -> ToolResult:
        st = self._state()
        kw.pop("page", None)
        lines = [f"{status}: {msg}", f"Page: {st.title} ({st.url})"]
        lines += [f'[{e.id}] {e.role} "{e.name}"' + (f" = {e.value}" if e.value else "") for e in st.elements]
        return ToolResult(status=status, text="\n".join(lines), **kw)  # type: ignore[arg-type]

    def _describe_element(self, el: FakeElement, verb: str = "", text: str = "") -> ActionDescription:
        composer = self._by_key(el.controls) if el.controls else None
        if not text:
            text = composer.value if composer else (el.value if el.editable else "")
        v = verb or el.verb or (f"click {el.risk}" if el.risk != "none" else "click")
        owner = composer or el  # a send button is identified by the composer it sends
        ref = owner.ref or f"{owner.key or owner.role} ref {self.page.elements.index(owner) + 1}"
        # target always = "<page url> | <element ref>" (+ " | <label>"): unique per post and element,
        # and part of what an approval is bound to (decision 20). The gate may split on " | ".
        label = el.target or (composer.target if composer else "")
        target = f"{self.page.url} | {ref}" + (f" | {label}" if label else "")
        # readback is filled in by the gate (agent/safety.py), not by the browser.
        return ActionDescription(risk=el.risk, verb=v, target=target, text=text)  # type: ignore[arg-type]

    def _describe_key(self, key: str) -> ActionDescription:
        none = ActionDescription(risk="none", verb=f"press {key}", target="", text="")
        el = self._el(self.focused) if self.focused else None
        if el is None:
            return none
        k = key.lower()
        if el.editable and k in SEND_KEYS:
            # Enter / Ctrl+Enter on a composer sends only when there is text to send.
            if el.risk != "none" and el.value:
                return self._describe_element(el, verb=f"press {key}", text=el.value)
            return none
        if not el.editable and k in ACTIVATE_KEYS and el.risk != "none":
            return self._describe_element(el, verb=f"press {key}")
        return none

    def _describe(self, call: ToolCall) -> ActionDescription:
        a = call.args
        none = ActionDescription(risk="none", verb=call.name, target="", text="")
        if call.name == "click" and (el := self._el(a.get("element_id", -1))):
            # clicking a text box only focuses it; sending is a button or Enter
            return self._describe_element(el) if el.risk != "none" and not el.editable else none
        if call.name == "click_at":
            for e in self.page.elements:
                x, y, w, h = e.box
                if w and h and x <= a["x"] <= x + w and y <= a["y"] <= y + h and e.risk != "none" and not e.editable:
                    return self._describe_element(e)
            return none
        if call.name == "press_key":
            return self._describe_key(a["key"])
        if call.name == "type_text" and a.get("press_enter") and (el := self._el(a.get("element_id", -1))):
            if el.editable and el.risk != "none" and not el.sensitive:
                typed = a["text"] if a.get("clear") else el.value + a["text"]
                return self._describe_element(el, verb="press Enter", text=typed)
        return none

    async def describe_pending_action(self, call: ToolCall) -> ActionDescription:
        return self._describe(call)

    @property
    def approval_open(self) -> bool:
        """True while an unused, unexpired approval is held (test helper)."""
        if self._approved is not None and self._clock() >= self._approved[1]:
            self._approved = None  # expired: gone for good
        return self._approved is not None

    def _edit_clears_approval(self, el: FakeElement, old_value: str) -> None:
        """Changing a composer's text after approve is a mismatch: clear the approval.

        A composer is an editable element with a non-"none" risk. Other fields are harmless.
        """
        if el.editable and el.risk != "none" and el.value != old_value:
            self._approved = None

    async def approve(self, description: ActionDescription) -> None:
        # Replaces any earlier approval (never stacks) and restarts the expiry window.
        self._approved = (description, self._clock() + self.approval_ttl)

    def _gate_or_run(self, call: ToolCall) -> None:
        """Raise Gated for a risky action unless it is the exact approved one."""
        d = self._describe(call)
        if not is_gated(d):
            return
        # Fail closed: the approval is consumed only by the next MATCHING gated action.
        # Any gated action that does not match (other verb, target or text) clears it and
        # raises Gated with the new description; the next action then needs a fresh approve().
        # Harmless actions leave it alone, except an edit of the composer text (see
        # _edit_clears_approval). Navigation, tab change, history and halt() clear it too.
        if self.approval_open:
            approved, _ = self._approved  # type: ignore[misc]
            self._approved = None
            if binding(approved) == binding(d):
                self.executed.append(d)
                return
        raise Gated(d)

    # ---- call plumbing: record, serialize, halt --------------------------

    async def _call(self, name: str, args: dict[str, Any], fn) -> ToolResult:
        epoch = self._epoch
        self._active += 1
        self._idle.clear()
        outcome = "error"
        try:
            async with self._lock:
                if epoch != self._epoch or self._halt.is_set():
                    outcome = "cancelled"
                    return self._result("cancelled", f"{name} stopped before it started")
                try:
                    res = await fn()
                except Gated:
                    outcome = "gated"
                    raise
                outcome = res.status
                return res
        finally:
            self.calls.append(RecordedCall(name, args, outcome))
            self._active -= 1
            if self._active == 0:
                self._idle.set()

    async def halt(self) -> None:
        self._epoch += 1  # anything queued before now never runs
        self._approved = None
        self._halt.set()
        await self._idle.wait()
        self._halt.clear()

    async def _sleep(self, seconds: float) -> bool:
        """Sleep in ticks; True if it ran to the end, False if halted."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._halt.is_set():
                return False
            await asyncio.sleep(min(HALT_TICK, max(0.0, end - time.monotonic())))
        return not self._halt.is_set()

    # ---- tools -----------------------------------------------------------

    def _goto(self, url: str) -> None:
        self.page = self.pages.get(url) or FakePage(url=url, title=url)
        self.focused = None
        self._approved = None  # navigation clears any held approval
        self._history = self._history[: self._hist_pos + 1] + [url]
        self._hist_pos = len(self._history) - 1
        self.tab_urls[-1:] = [url]

    async def navigate(self, url: str) -> ToolResult:
        async def go():
            self._goto(url)
            return self._result("ok", f"opened {url}")
        return await self._call("navigate", {"url": url}, go)

    async def search_web(self, query: str, engine: Optional[Literal["google", "bing", "duckduckgo", "youtube", "amazon", "x"]] = None) -> ToolResult:
        async def go():
            self._goto(f"https://{engine or 'google'}.fake/search?q={query}")
            return self._result("ok", f"searched {engine or 'google'} for {query}")
        return await self._call("search_web", {"query": query, "engine": engine}, go)

    async def observe(self, max_items: Optional[int] = None) -> ToolResult:
        async def go():
            r = self._result("ok", "page state")
            if max_items is not None:
                r = ToolResult("ok", "\n".join(r.text.split("\n")[: 2 + max(0, max_items)]))
            return r
        return await self._call("observe", {"max_items": max_items}, go)

    async def look(self, annotate: Optional[bool] = None) -> ToolResult:
        async def go():
            return self._result("ok", "screenshot", image_b64=FAKE_JPEG_B64)
        return await self._call("look", {"annotate": annotate}, go)

    async def read_page(self, mode: Optional[Literal["main", "full"]] = None, start: Optional[int] = None) -> ToolResult:
        async def go():
            body = self.page.text[start or 0:]
            r = self._result("ok", "page text")
            return ToolResult("ok", r.text + "\n---\n" + body)
        return await self._call("read_page", {"mode": mode, "start": start}, go)

    async def _activate(self, el: FakeElement) -> None:
        self.focused = self.page.elements.index(el) + 1
        if el.reveals:  # opening a reply box etc.: not gated, just shows more elements
            idx = self.page.elements.index(el) + 1
            self.page.elements[idx:idx] = el.reveals
            el.reveals = []

    async def click(self, element_id: int, double: Optional[bool] = None, button: Optional[Literal["left", "right", "middle"]] = None) -> ToolResult:
        args = {"element_id": element_id, "double": double, "button": button}

        async def go():
            el = self._el(element_id)
            if el is None:
                return self._result("error", f"element {element_id} is gone; the page changed, use the state below")
            self._gate_or_run(ToolCall("click", {"element_id": element_id}))
            await self._activate(el)
            return self._result("ok", f"clicked [{element_id}] {el.name}")
        return await self._call("click", args, go)

    async def click_at(self, x: float, y: float, double: Optional[bool] = None) -> ToolResult:
        async def go():
            self._gate_or_run(ToolCall("click_at", {"x": x, "y": y}))
            hit = next(
                (e for e in self.page.elements if e.box[2] and e.box[3]
                 and e.box[0] <= x <= e.box[0] + e.box[2] and e.box[1] <= y <= e.box[1] + e.box[3]),
                None,
            )
            if hit is not None:
                await self._activate(hit)
            return self._result("ok", f"clicked at ({x}, {y})")
        return await self._call("click_at", {"x": x, "y": y, "double": double}, go)

    async def move_cursor(self, element_id: Optional[int] = None, x: Optional[float] = None, y: Optional[float] = None) -> ToolResult:
        async def go():
            if element_id is None and (x is None or y is None):
                return self._result("error", "give element_id or both x and y")
            if element_id is not None and self._el(element_id) is None:
                return self._result("error", f"element {element_id} is gone; the page changed")
            return self._result("ok", "cursor moved")
        return await self._call("move_cursor", {"element_id": element_id, "x": x, "y": y}, go)

    async def hover(self, element_id: int) -> ToolResult:
        async def go():
            el = self._el(element_id)
            if el is None:
                return self._result("error", f"element {element_id} is gone; the page changed")
            return self._result("ok", f"hovering [{element_id}] {el.name}")
        return await self._call("hover", {"element_id": element_id}, go)

    async def type_text(self, element_id: int, text: str, press_enter: Optional[bool] = None, clear: Optional[bool] = None) -> ToolResult:
        args = {"element_id": element_id, "text": text, "press_enter": press_enter, "clear": clear}

        async def go():
            el = self._el(element_id)
            if el is None:
                return self._result("error", f"element {element_id} is gone; the page changed")
            if el.sensitive:
                return self._result("blocked", f"[{element_id}] is a sensitive field; ask Gopi to type it himself")
            if not el.editable:
                return self._result("error", f"[{element_id}] is not a text field")
            self.focused = element_id
            before = el.value
            if clear:
                el.value = ""
            self._edit_clears_approval(el, before)
            for i in range(0, len(text), 5):  # chunks so halt takes effect between them
                if self.type_delay and not await self._sleep(self.type_delay * len(text[i:i + 5])):
                    self._edit_clears_approval(el, before)
                    return self._result("cancelled", f"stopped after typing {len(el.value)} characters")
                el.value += text[i:i + 5]
            self._edit_clears_approval(el, before)
            if press_enter:
                self._gate_or_run(ToolCall("press_key", {"key": "Enter"}))
            return self._result("ok", f"typed into [{element_id}]")
        return await self._call("type_text", args, go)

    async def fill_form(self, fields: list[dict[str, Any]]) -> ToolResult:
        async def go():
            filled, skipped = 0, []
            for f in fields:
                el = self._el(f["element_id"])
                if el is None or el.sensitive or not el.editable:
                    skipped.append(f["element_id"])
                    continue
                before = el.value
                el.value = f["value"]
                self._edit_clears_approval(el, before)
                filled += 1
            note = f"; skipped sensitive or missing: {skipped}" if skipped else ""
            return self._result("ok", f"filled {filled} field(s), not submitted{note}")
        return await self._call("fill_form", {"fields": [f.get("element_id") for f in fields]}, go)

    async def select_option(self, element_id: int, option: str) -> ToolResult:
        async def go():
            el = self._el(element_id)
            if el is None:
                return self._result("error", f"element {element_id} is gone; the page changed")
            el.value = option
            return self._result("ok", f"selected {option}")
        return await self._call("select_option", {"element_id": element_id, "option": option}, go)

    async def scroll(self, direction: Literal["down", "up", "top", "bottom", "left", "right"], amount: Optional[float] = None, element_id: Optional[int] = None) -> ToolResult:
        async def go():
            return self._result("ok", f"scrolled {direction}")
        return await self._call("scroll", {"direction": direction, "amount": amount, "element_id": element_id}, go)

    async def find_text(self, text: str) -> ToolResult:
        async def go():
            if text.lower() in self.page.text.lower():
                return self._result("ok", f"found {text!r}")
            return self._result("error", f"{text!r} not found on this page")
        return await self._call("find_text", {"text": text}, go)

    async def press_key(self, key: str) -> ToolResult:
        async def go():
            el = self._el(self.focused) if self.focused else None
            if el is not None and el.sensitive and len(key) == 1:
                return self._result("blocked", "focused field is sensitive; ask Gopi to type it himself")
            self._gate_or_run(ToolCall("press_key", {"key": key}))
            if el is not None and el.editable and not el.sensitive and len(key) == 1:
                before = el.value
                el.value += key
                self._edit_clears_approval(el, before)
            return self._result("ok", f"pressed {key}")
        return await self._call("press_key", {"key": key}, go)

    async def history(self, action: Literal["back", "forward", "reload"]) -> ToolResult:
        async def go():
            if action == "back" and self._hist_pos > 0:
                self._hist_pos -= 1
            elif action == "forward" and self._hist_pos < len(self._history) - 1:
                self._hist_pos += 1
            elif action not in ("back", "forward", "reload"):
                return self._result("error", f"unknown history action {action}")
            url = self._history[self._hist_pos]
            self.page = self.pages.get(url) or FakePage(url=url, title=url)
            self.focused = None
            self._approved = None
            return self._result("ok", f"history {action}")
        return await self._call("history", {"action": action}, go)

    async def tabs(self, action: Literal["list", "new", "switch", "close"], index: Optional[int] = None, url: Optional[str] = None) -> ToolResult:
        async def go():
            if action == "list":
                return self._result("ok", "tabs: " + ", ".join(f"{i}={u}" for i, u in enumerate(self.tab_urls)))
            self._approved = None  # tab change clears any held approval
            if action == "new":
                self.tab_urls.append(url or "about:blank")
                self._goto(url or "about:blank")
            elif action in ("switch", "close"):
                if index is None or not 0 <= index < len(self.tab_urls):
                    return self._result("error", f"no tab {index}")
                if action == "close" and len(self.tab_urls) > 1:
                    self.tab_urls.pop(index)
                if action == "switch" or len(self.tab_urls) >= 1:
                    u = self.tab_urls[min(index, len(self.tab_urls) - 1)]
                    self.page = self.pages.get(u) or FakePage(url=u, title=u)
            else:
                return self._result("error", f"unknown tabs action {action}")
            return self._result("ok", f"tabs {action}")
        return await self._call("tabs", {"action": action, "index": index, "url": url}, go)

    async def wait(self, seconds: Optional[float] = None) -> ToolResult:
        async def go():
            s = min(20.0, max(0.0, 1.0 if seconds is None else seconds))
            if not await self._sleep(s):
                return self._result("cancelled", "wait stopped")
            return self._result("ok", f"waited {s:g}s")
        return await self._call("wait", {"seconds": seconds}, go)
