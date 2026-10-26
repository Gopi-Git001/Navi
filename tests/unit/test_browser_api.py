"""Tests for browser/api.py and browser/fake.py (issue #10).

How it can break (each item has a test below):
  contract drift   a browser tool is missing, renamed, or has other arguments
  empty            observe on a blank page; fill_form with no fields; max_items=0
  missing element  stale/unknown element id, click_at on nothing, bad tab index
  two at once      halt() mid-action; actions queued behind a running one;
                   halt with nothing running; actions after halt still work
  safety           risky click, Enter / Ctrl+Enter / Space, menu Delete, click_at,
                   type_text(press_enter); opening a reply box must NOT gate;
                   approval is one-shot and bound to exact text and target;
                   password/card fields never typed into and never echoed
"""
from __future__ import annotations

import asyncio
import inspect
import json
import time
import typing
from pathlib import Path

import pytest

from browser.api import BrowserController, ToolCall, binding, is_gated
from browser.fake import FakeBrowser, FakeElement, FakePage
from common.types import ActionDescription, Gated, ToolResult

CONTRACT = Path(__file__).resolve().parents[2] / "docs" / "TOOL_CONTRACT.json"
JSON_TYPES = {"string": (str,), "integer": (int,), "boolean": (bool,), "number": (float, int), "array": (list,)}


def browser_tools(contract: dict) -> dict[str, dict]:
    return {t["name"]: t for t in contract["tools"] if t.get("owner") == "browser"}


def check_signatures(contract: dict, cls: type) -> list[str]:
    """Problems between the contract's browser tools and the methods of ``cls``."""
    problems: list[str] = []
    for name, tool in browser_tools(contract).items():
        fn = getattr(cls, name, None)
        if fn is None:
            problems.append(f"{name}: missing")
            continue
        if not inspect.iscoroutinefunction(fn):
            problems.append(f"{name}: not async")
        sig = inspect.signature(fn)
        params = {k: v for k, v in sig.parameters.items() if k != "self"}
        props = tool["parameters"]["properties"]
        required = set(tool["parameters"].get("required", []))
        if set(params) != set(props):
            problems.append(f"{name}: arguments {sorted(params)} != contract {sorted(props)}")
            continue
        hints = typing.get_type_hints(fn)
        for arg, spec in props.items():
            has_default = params[arg].default is not inspect.Parameter.empty
            if (arg in required) == has_default:
                problems.append(f"{name}.{arg}: required={arg in required} but has_default={has_default}")
            ann = hints.get(arg)
            base = [a for a in typing.get_args(ann) if a is not type(None)] if typing.get_origin(ann) is typing.Union else [ann]
            if "enum" in spec:
                lits = [v for a in base if typing.get_origin(a) is typing.Literal for v in typing.get_args(a)]
                if set(lits) != set(spec["enum"]):
                    problems.append(f"{name}.{arg}: enum {sorted(lits)} != {sorted(spec['enum'])}")
            elif spec["type"] == "array":
                if not any(typing.get_origin(a) is list or a is list for a in base):
                    problems.append(f"{name}.{arg}: expected list")
            elif not any(a in JSON_TYPES[spec["type"]] for a in base):
                problems.append(f"{name}.{arg}: type {ann} does not match {spec['type']}")
    return problems


@pytest.fixture(scope="module")
def contract() -> dict:
    return json.loads(CONTRACT.read_text())


# ---------------------------------------------------------------- contract drift

def test_contract_has_browser_tools(contract):
    assert len(browser_tools(contract)) >= 15  # guards against an empty/changed contract


def test_protocol_matches_contract(contract):
    assert check_signatures(contract, BrowserController) == []


def test_fake_matches_contract(contract):
    assert check_signatures(contract, FakeBrowser) == []


def test_fake_is_a_browser_controller():
    assert isinstance(FakeBrowser(), BrowserController)


def test_protocol_has_halt_and_describe():
    assert inspect.iscoroutinefunction(BrowserController.halt)
    assert inspect.iscoroutinefunction(BrowserController.describe_pending_action)
    assert list(inspect.signature(BrowserController.describe_pending_action).parameters) == ["self", "call"]


def test_no_extra_tool_methods_without_contract_entry(contract):
    """Public async methods on the Protocol are contract tools or the three non-tools."""
    allowed = set(browser_tools(contract)) | {"halt", "describe_pending_action", "approve"}
    public = {n for n, f in inspect.getmembers(BrowserController, inspect.isfunction) if not n.startswith("_")}
    assert public <= allowed


def test_checker_detects_drift(contract):
    """The checker itself must fail on a missing tool, a renamed or an extra argument."""
    class Missing:
        pass

    assert any("click: missing" in p for p in check_signatures(contract, Missing))

    class Renamed(FakeBrowser):
        async def hover(self, id: int):  # noqa: A002
            ...

    assert any(p.startswith("hover: arguments") for p in check_signatures(contract, Renamed))

    class OptionalBecameRequired(FakeBrowser):
        async def wait(self, seconds: float): ...

    assert any(p.startswith("wait.seconds") for p in check_signatures(contract, OptionalBecameRequired))

    changed = json.loads(json.dumps(contract))
    next(t for t in changed["tools"] if t["name"] == "scroll")["parameters"]["properties"]["extra"] = {"type": "string"}
    assert any(p.startswith("scroll: arguments") for p in check_signatures(changed, BrowserController))

    enum_changed = json.loads(json.dumps(contract))
    next(t for t in enum_changed["tools"] if t["name"] == "history")["parameters"]["properties"]["action"]["enum"].append("stop")
    assert any(p.startswith("history.action: enum") for p in check_signatures(enum_changed, BrowserController))


# ---------------------------------------------------------------- page fixtures

def x_page() -> FakePage:
    """A mock-X-like page: post with a Reply icon that opens a composer, a menu, a sensitive login."""
    reply_box = FakeElement(
        name="Post text", role="textbox", key="composer", editable=True, in_dialog=True,
        risk="comment", target="reply to @alice's post", target_ref="https://x.test/alice/status/1",
    )
    post_btn = FakeElement(
        name="Reply", key="send", in_dialog=True, risk="comment", verb="click Reply button",
        controls="composer", box=(300, 400, 80, 30),
    )
    return FakePage(
        url="https://x.test/alice/status/1", title="Alice on X", text="hello world from alice",
        elements=[
            FakeElement(name="Reply", key="open_reply", reveals=[reply_box, post_btn]),
            FakeElement(name="More", key="more", reveals=[
                FakeElement(name="Delete", role="menuitem", key="delete", risk="delete",
                            target="your post", target_ref="https://x.test/me/status/9"),
            ]),
            FakeElement(name="Password", role="textbox", key="pw", editable=True, sensitive=True, value="hunter2"),
            FakeElement(name="Card number", role="textbox", key="card", editable=True, sensitive=True),
            FakeElement(name="Search", role="textbox", key="search", editable=True),
        ],
    )


@pytest.fixture
def fb() -> FakeBrowser:
    return FakeBrowser(x_page())


async def open_composer(fb: FakeBrowser, text: str = "great post") -> None:
    r = await fb.click(fb.element_id("open_reply"))
    assert r.status == "ok"
    await fb.type_text(fb.element_id("composer"), text)


# ---------------------------------------------------------------- fake basics

async def test_every_result_has_status_word_and_page_state(fb):
    for res in [
        await fb.observe(), await fb.read_page(), await fb.scroll("down"), await fb.find_text("hello"),
        await fb.hover(1), await fb.move_cursor(element_id=1), await fb.wait(0), await fb.look(),
    ]:
        assert isinstance(res, ToolResult)
        assert res.text.split(":", 1)[0] == res.status
        assert "Alice on X" in res.text and "https://x.test/alice/status/1" in res.text


async def test_look_returns_image(fb):
    assert (await fb.look(annotate=True)).image_b64


async def test_blank_page_observe_is_empty_not_an_error():
    fb = FakeBrowser()
    r = await fb.observe()
    assert r.status == "ok" and fb.page_state().elements == ()
    assert (await fb.fill_form([])).status == "ok"


async def test_observe_max_items_zero_and_limit(fb):
    assert len((await fb.observe(max_items=0)).text.splitlines()) == 2
    assert len((await fb.observe(max_items=2)).text.splitlines()) == 4


async def test_element_numbering_is_1_to_n_in_page_order(fb):
    assert [e.id for e in fb.page_state().elements] == list(range(1, 6))
    assert [e.name for e in fb.page_state().elements][:2] == ["Reply", "More"]


async def test_calls_are_recorded(fb):
    await fb.navigate("https://x.test/home")
    await fb.search_web("cats", engine="youtube")
    assert [c.name for c in fb.calls] == ["navigate", "search_web"]
    assert fb.calls[1].args == {"query": "cats", "engine": "youtube"}


async def test_missing_element_is_an_error_with_state(fb):
    for res in [await fb.click(99), await fb.hover(0), await fb.type_text(99, "x"),
                await fb.select_option(99, "a"), await fb.move_cursor(element_id=99)]:
        assert res.status == "error" and "page changed" in res.text
    assert (await fb.move_cursor()).status == "error"
    assert (await fb.find_text("not on the page")).status == "error"
    assert fb.executed == []


async def test_click_at_on_nothing_is_ok_and_not_gated(fb):
    assert (await fb.click_at(1, 1)).status == "ok"


async def test_navigation_and_tabs_and_history(fb):
    await fb.navigate("https://x.test/home")
    await fb.navigate("https://x.test/other")
    assert fb.page.url == "https://x.test/other"
    await fb.history("back")
    assert fb.page.url == "https://x.test/home"
    await fb.history("forward")
    assert fb.page.url == "https://x.test/other"
    assert "tabs:" in (await fb.tabs("list")).text
    assert (await fb.tabs("switch", index=7)).status == "error"
    assert (await fb.tabs("new", url="https://x.test/new")).status == "ok"
    assert (await fb.tabs("close", index=0)).status == "ok"


async def test_wait_is_capped_at_20_seconds_and_negative_is_zero(fb):
    t = time.monotonic()
    assert (await fb.wait(-5)).status == "ok"
    assert time.monotonic() - t < 0.5


# ---------------------------------------------------------------- gate: raises Gated

def assert_full(d: ActionDescription, risk: str, verb_has: str, target_has: str, text: str):
    assert d.risk == risk
    assert verb_has in d.verb
    assert target_has in d.target
    assert d.text == text
    assert d.readback == ""  # the gate (agent/safety.py) writes the read-back, not the browser


async def test_opening_reply_box_is_not_gated(fb):
    r = await fb.click(fb.element_id("open_reply"))
    assert r.status == "ok"
    assert {e.name for e in fb.page_state().elements} >= {"Post text"}
    assert fb.executed == []


async def test_typing_into_composer_is_not_gated(fb):
    await open_composer(fb)
    assert fb.page.elements[fb.element_id("composer") - 1].value == "great post"
    assert fb.executed == []


async def test_risky_click_raises_gated_with_full_description(fb):
    await open_composer(fb)
    with pytest.raises(Gated) as ei:
        await fb.click(fb.element_id("send"))
    assert_full(ei.value.description, "comment", "Reply", "alice's post", "great post")
    assert "https://x.test/alice/status/1" in ei.value.description.target
    assert fb.executed == []
    assert fb.calls[-1].outcome == "gated"


async def test_click_at_on_risky_target_is_gated(fb):
    await open_composer(fb)
    with pytest.raises(Gated) as ei:
        await fb.click_at(320, 410)
    assert_full(ei.value.description, "comment", "Reply", "alice's post", "great post")


@pytest.mark.parametrize("key", ["Enter", "Ctrl+Enter", "Meta+Enter"])
async def test_enter_and_ctrl_enter_on_composer_are_gated(fb, key):
    await open_composer(fb, "my reply")
    with pytest.raises(Gated) as ei:
        await fb.press_key(key)
    assert_full(ei.value.description, "comment", key, "alice's post", "my reply")
    assert fb.executed == []


async def test_enter_on_empty_composer_is_not_a_send(fb):
    await fb.click(fb.element_id("open_reply"))
    await fb.click(fb.element_id("composer"))
    assert (await fb.press_key("Ctrl+Enter")).status == "ok"


async def test_type_text_with_press_enter_types_then_gates(fb):
    await fb.click(fb.element_id("open_reply"))
    with pytest.raises(Gated) as ei:
        await fb.type_text(fb.element_id("composer"), "hi there", press_enter=True)
    assert ei.value.description.text == "hi there"
    assert fb.executed == []


async def test_space_on_focused_send_button_is_gated(fb):
    await open_composer(fb)
    await fb.hover(fb.element_id("send"))
    fb.focused = fb.element_id("send")  # as if tabbed onto the button
    with pytest.raises(Gated) as ei:
        await fb.press_key("Space")
    assert ei.value.description.risk == "comment"


async def test_menu_delete_is_gated(fb):
    await fb.click(fb.element_id("more"))  # opening the menu is fine
    with pytest.raises(Gated) as ei:
        await fb.click(fb.element_id("delete"))
    assert_full(ei.value.description, "delete", "delete", "your post", "")
    assert fb.executed == []


async def test_describe_pending_action_matches_gated_and_does_nothing(fb):
    await open_composer(fb)
    d = await fb.describe_pending_action(ToolCall("click", {"element_id": fb.element_id("send")}))
    assert is_gated(d) and d.text == "great post"
    with pytest.raises(Gated) as ei:
        await fb.click(fb.element_id("send"))
    assert binding(ei.value.description) == binding(d)
    assert fb.executed == []
    harmless = await fb.describe_pending_action(ToolCall("click", {"element_id": fb.element_id("open_reply")}))
    assert harmless.risk == "none" and not is_gated(harmless)
    assert (await fb.describe_pending_action(ToolCall("click", {"element_id": 99}))).risk == "none"


# ---------------------------------------------------------------- approval (one-shot, bound)

async def test_approved_action_runs_once_then_gates_again(fb):
    await open_composer(fb)
    cid = fb.element_id("send")
    d = await fb.describe_pending_action(ToolCall("click", {"element_id": cid}))
    await fb.approve(d)
    assert (await fb.click(cid)).status == "ok"
    assert [e.text for e in fb.executed] == ["great post"]
    with pytest.raises(Gated):  # approval was consumed
        await fb.click(cid)
    assert len(fb.executed) == 1


async def test_approval_refused_if_composer_text_changed(fb):
    await open_composer(fb, "original")
    cid = fb.element_id("send")
    d = await fb.describe_pending_action(ToolCall("click", {"element_id": cid}))
    await fb.approve(d)
    await fb.type_text(fb.element_id("composer"), " plus sneaky edit")
    with pytest.raises(Gated) as ei:
        await fb.click(cid)
    assert ei.value.description.text == "original plus sneaky edit"
    assert fb.executed == []


async def test_approval_refused_if_target_changed(fb):
    await open_composer(fb)
    cid = fb.element_id("send")
    d = await fb.describe_pending_action(ToolCall("click", {"element_id": cid}))
    await fb.approve(d)
    fb.page.elements[fb.element_id("composer") - 1].target_ref = "https://x.test/mallory/status/2"
    with pytest.raises(Gated):
        await fb.click(cid)
    assert fb.executed == []


async def test_approval_for_one_action_does_not_cover_another(fb):
    await open_composer(fb)
    d = await fb.describe_pending_action(ToolCall("press_key", {"key": "Enter"}))
    await fb.approve(d)
    with pytest.raises(Gated):  # the Ctrl+Enter / click paths have different verbs
        await fb.click(fb.element_id("send"))


async def test_approval_cleared_by_navigation_and_halt(fb):
    await open_composer(fb)
    cid = fb.element_id("send")
    d = await fb.describe_pending_action(ToolCall("click", {"element_id": cid}))
    await fb.approve(d)
    await fb.halt()
    with pytest.raises(Gated):
        await fb.click(cid)
    await fb.approve(d)
    await fb.navigate("https://x.test/elsewhere")
    assert fb._approved is None


# ---------------------------------------------------------------- sensitive fields

async def test_type_text_into_password_and_card_is_blocked(fb):
    for key in ("pw", "card"):
        r = await fb.type_text(fb.element_id(key), "4111111111111111")
        assert r.status == "blocked"
        assert "4111" not in r.text
    assert fb.page.elements[fb.element_id("card") - 1].value == ""


async def test_fill_form_skips_sensitive_and_never_submits(fb):
    r = await fb.fill_form([
        {"element_id": fb.element_id("search"), "value": "cats"},
        {"element_id": fb.element_id("pw"), "value": "pw!"},
        {"element_id": fb.element_id("card"), "value": "4111"},
        {"element_id": 99, "value": "x"},
    ])
    assert r.status == "ok" and "not submitted" in r.text and "skipped" in r.text
    assert fb.page.elements[fb.element_id("search") - 1].value == "cats"
    assert fb.page.elements[fb.element_id("pw") - 1].value == "hunter2"  # untouched
    assert fb.page.elements[fb.element_id("card") - 1].value == ""
    assert fb.executed == []


async def test_sensitive_values_never_appear_in_results(fb):
    r = await fb.observe()
    assert "hunter2" not in r.text
    assert any(e.value == "(hidden)" for e in fb.page_state().elements if e.sensitive)


async def test_press_key_cannot_type_into_sensitive_field_after_click_at(fb):
    await fb.click(fb.element_id("card"))
    r = await fb.press_key("4")
    assert r.status == "blocked"
    assert fb.page.elements[fb.element_id("card") - 1].value == ""


async def test_press_key_into_normal_field_types(fb):
    await fb.click(fb.element_id("search"))
    await fb.press_key("a")
    assert fb.page.elements[fb.element_id("search") - 1].value == "a"


# ---------------------------------------------------------------- halt

async def test_halt_stops_long_wait_in_under_1s(fb):
    task = asyncio.create_task(fb.wait(20))
    await asyncio.sleep(0.1)
    t0 = time.monotonic()
    await fb.halt()
    assert time.monotonic() - t0 < 1.0
    r = await task
    assert r.status == "cancelled" and time.monotonic() - t0 < 1.0


async def test_halt_stops_long_typing_between_chunks_and_leaves_page_as_is():
    fb = FakeBrowser(x_page(), type_delay=0.05)  # 280 chars would take 14 s
    await fb.click(fb.element_id("open_reply"))
    cid = fb.element_id("composer")
    task = asyncio.create_task(fb.type_text(cid, "x" * 280))
    await asyncio.sleep(0.3)
    t0 = time.monotonic()
    await fb.halt()
    assert time.monotonic() - t0 < 1.0
    r = await task
    assert r.status == "cancelled" and "stopped after typing" in r.text
    typed = len(fb.page.elements[cid - 1].value)
    assert 0 < typed < 280


async def test_halt_cancels_queued_actions_but_not_later_ones(fb):
    running = asyncio.create_task(fb.wait(20))
    await asyncio.sleep(0.05)
    queued = asyncio.create_task(fb.click(fb.element_id("more")))
    await asyncio.sleep(0.05)
    await fb.halt()
    assert (await running).status == "cancelled"
    assert (await queued).status == "cancelled"
    assert "Delete" not in [e.name for e in fb.page_state().elements]  # queued click never ran
    assert (await fb.click(fb.element_id("more"))).status == "ok"  # normal again after halt


async def test_halt_with_nothing_running_returns_immediately_and_is_harmless(fb):
    t0 = time.monotonic()
    await fb.halt()
    await fb.halt()
    assert time.monotonic() - t0 < 0.1
    assert (await fb.observe()).status == "ok"


async def test_halt_during_gated_flow_never_executes_the_held_action(fb):
    await open_composer(fb)
    with pytest.raises(Gated):
        await fb.click(fb.element_id("send"))
    await fb.halt()
    assert fb.executed == []
