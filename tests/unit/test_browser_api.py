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
        risk="comment", target="reply to @alice's post", ref="composer ref 14",
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
                            target="your post", ref="menuitem ref 3"),
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
    assert ei.value.description.target.startswith("https://x.test/alice/status/1 | composer ref 14")
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
    fb.page.elements[fb.element_id("composer") - 1].ref = "composer ref 99"
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


# ---------------------------------------------------------------- approve(): one-shot, bound, expiring

class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def timed_browser(ttl: float = 30.0) -> tuple[FakeBrowser, FakeClock]:
    clock = FakeClock()
    return FakeBrowser(x_page(), clock=clock, approval_ttl=ttl), clock


async def held_send(fb: FakeBrowser) -> tuple[int, ActionDescription]:
    await open_composer(fb)
    cid = fb.element_id("send")
    return cid, await fb.describe_pending_action(ToolCall("click", {"element_id": cid}))


async def test_approve_twice_same_description_still_runs_only_once(fb):
    cid, d = await held_send(fb)
    await fb.approve(d)
    await fb.approve(d)  # approving again must not stack a second use
    assert (await fb.click(cid)).status == "ok"
    with pytest.raises(Gated):
        await fb.click(cid)
    assert len(fb.executed) == 1


async def test_second_matching_action_after_consumption_needs_fresh_approval(fb):
    cid, d = await held_send(fb)
    await fb.approve(d)
    await fb.click(cid)
    with pytest.raises(Gated):
        await fb.click(cid)
    await fb.approve(d)  # a fresh approval works again, once
    assert (await fb.click(cid)).status == "ok"
    assert len(fb.executed) == 2


async def test_approve_then_target_changed_is_refused_and_described_anew(fb):
    cid, d = await held_send(fb)
    await fb.approve(d)
    fb.page.elements[fb.element_id("composer") - 1].ref = "composer ref 99"
    with pytest.raises(Gated) as ei:
        await fb.click(cid)
    assert ei.value.description.target != d.target
    assert "composer ref 99" in ei.value.description.target
    assert fb.executed == []


# DELIBERATE BEHAVIOR CHANGE requested by Agent Brain (owner of the confirm gate): this test used
# to assert that a different gated action LEFT the approval in place. Approvals are now fail-closed:
# any gated action that does not match the pending approval clears it.
async def test_mismatched_gated_action_clears_the_approval(fb):
    cid, d = await held_send(fb)
    await fb.approve(d)
    with pytest.raises(Gated) as ei:
        await fb.press_key("Enter")  # different verb: not what was approved
    assert ei.value.description.verb == "press Enter" and ei.value.description.verb != d.verb
    assert not fb.approval_open
    assert fb.executed == []
    with pytest.raises(Gated):  # the originally approved action now needs a fresh approve()
        await fb.click(cid)
    assert fb.executed == []
    await fb.approve(d)
    assert (await fb.click(cid)).status == "ok"
    assert len(fb.executed) == 1


async def test_mismatched_target_or_text_in_gated_action_clears_the_approval():
    fb = FakeBrowser(two_post_timeline())
    d1 = await fb.describe_pending_action(ToolCall("click", {"element_id": fb.element_id("send1")}))
    await fb.approve(d1)
    with pytest.raises(Gated) as ei:
        await fb.click(fb.element_id("send2"))  # other target
    assert ei.value.description.target != d1.target
    assert not fb.approval_open
    with pytest.raises(Gated):
        await fb.click(fb.element_id("send1"))
    assert fb.executed == []


async def test_composer_text_edit_after_approve_clears_it(fb):
    cid, d = await held_send(fb)
    await fb.approve(d)
    assert fb.approval_open
    await fb.type_text(fb.element_id("composer"), " plus an edit")  # no gated action involved
    assert not fb.approval_open
    with pytest.raises(Gated) as ei:
        await fb.click(cid)
    assert ei.value.description.text == "great post plus an edit"
    assert fb.executed == []
    # and with a fresh approval of the NEW text it runs
    await fb.approve(ei.value.description)
    assert (await fb.click(cid)).status == "ok"
    assert [e.text for e in fb.executed] == ["great post plus an edit"]


@pytest.mark.parametrize("edit", ["type_clear", "press_key", "fill_form"])
async def test_every_way_of_changing_composer_text_clears_the_approval(fb, edit):
    cid, d = await held_send(fb)
    comp = fb.element_id("composer")
    await fb.approve(d)
    if edit == "type_clear":
        await fb.type_text(comp, "different", clear=True)
    elif edit == "press_key":
        await fb.click(comp)
        await fb.approve(d)  # focusing is harmless; re-approve to isolate the key press
        await fb.press_key("!")
    else:
        await fb.fill_form([{"element_id": comp, "value": "different"}])
    assert not fb.approval_open
    with pytest.raises(Gated):
        await fb.click(cid)
    assert fb.executed == []


async def test_typing_the_same_text_back_does_not_change_it_but_other_fields_do_not_clear(fb):
    cid, d = await held_send(fb)
    await fb.approve(d)
    await fb.type_text(fb.element_id("search"), "cats")  # a non-composer field: harmless
    assert fb.approval_open
    assert (await fb.click(cid)).status == "ok"


async def test_empty_typing_does_not_change_composer_and_keeps_approval(fb):
    cid, d = await held_send(fb)
    await fb.approve(d)
    await fb.type_text(fb.element_id("composer"), "")  # nothing changes
    assert fb.approval_open
    assert (await fb.click(cid)).status == "ok"


@pytest.mark.parametrize("how", ["navigate", "tab_new", "history_reload", "halt"])
async def test_navigation_tab_history_and_halt_still_clear_the_approval(how):
    fb = FakeBrowser(x_page())
    cid, d = await held_send(fb)
    await fb.approve(d)
    if how == "navigate":
        await fb.navigate("https://x.test/n")
    elif how == "tab_new":
        await fb.tabs("new", url="https://x.test/t")
    elif how == "history_reload":
        await fb.history("reload")
    else:
        await fb.halt()
    assert not fb.approval_open


async def test_harmless_actions_do_not_consume_the_approval(fb):
    cid, d = await held_send(fb)
    await fb.approve(d)
    await fb.scroll("down")
    await fb.observe()
    await fb.look()
    await fb.read_page()
    await fb.find_text("hello")
    await fb.hover(cid)
    await fb.click(fb.element_id("more"))  # opening a menu is not gated
    assert fb.approval_open
    assert (await fb.click(cid)).status == "ok"
    assert len(fb.executed) == 1


async def test_unused_approval_expires_after_30s_by_default():
    fb = FakeBrowser(x_page())
    assert fb.approval_ttl == 30.0
    fb, clock = timed_browser()  # default 30 s, fake clock, no real sleeping
    cid, d = await held_send(fb)
    await fb.approve(d)
    clock.now += 29.9
    assert fb.approval_open
    clock.now += 0.2  # 30.1 s after approve
    assert not fb.approval_open
    with pytest.raises(Gated):
        await fb.click(cid)
    assert fb.executed == []


async def test_approval_still_valid_just_before_expiry():
    fb, clock = timed_browser()
    cid, d = await held_send(fb)
    await fb.approve(d)
    clock.now += 29.9
    assert (await fb.click(cid)).status == "ok"


async def test_expiry_is_injectable_and_a_new_approve_restarts_the_window():
    fb, clock = timed_browser(ttl=5.0)
    cid, d = await held_send(fb)
    await fb.approve(d)
    clock.now += 4
    await fb.approve(d)  # restart
    clock.now += 4  # 8 s after first, 4 s after second
    assert (await fb.click(cid)).status == "ok"
    await fb.approve(d)
    clock.now += 5
    with pytest.raises(Gated):
        await fb.click(cid)


async def test_expired_approval_is_gone_even_if_page_returns_to_the_same_state():
    fb, clock = timed_browser()
    cid, d = await held_send(fb)
    await fb.approve(d)
    clock.now += 31
    with pytest.raises(Gated):
        await fb.click(cid)
    clock.now += 0  # a second attempt later also stays gated (not revived)
    with pytest.raises(Gated):
        await fb.click(cid)


async def test_model_confirmed_flag_never_approves(fb, contract):
    """No contract tool has a `confirmed` argument; passing one is rejected and approves nothing."""
    for tool in contract["tools"]:
        assert "confirmed" not in tool["parameters"]["properties"]
    cid, d = await held_send(fb)
    with pytest.raises(TypeError):
        await fb.click(cid, confirmed=True)
    with pytest.raises(TypeError):
        await fb.press_key("Enter", confirmed=True)
    with pytest.raises(TypeError):
        await fb.type_text(fb.element_id("composer"), "x", press_enter=True, confirmed=True)
    assert fb.executed == []
    # even smuggled inside a ToolCall for description, it neither approves nor changes the description
    with_flag = await fb.describe_pending_action(ToolCall("click", {"element_id": cid, "confirmed": True}))
    assert with_flag == d and is_gated(with_flag)
    with pytest.raises(Gated):
        await fb.click(cid)
    assert fb.executed == []


# ---------------------------------------------------------------- target identifies the post uniquely

def two_post_timeline() -> FakePage:
    """One timeline page with two posts, each with its own reply composer and send button."""
    def post(n: int, author: str) -> list[FakeElement]:
        return [
            FakeElement(name="Post text", role="textbox", key=f"composer{n}", editable=True, risk="comment",
                        target=f"reply to @{author}'s post", ref=f"composer ref {n}", value=f"reply {n}"),
            FakeElement(name="Reply", key=f"send{n}", risk="comment", verb="click Reply button", controls=f"composer{n}"),
        ]
    return FakePage(url="https://x.test/home", title="Home", elements=post(1, "alice") + post(2, "bob"))


async def test_target_always_has_page_url_and_element_ref(fb):
    await open_composer(fb)
    for call in (ToolCall("click", {"element_id": fb.element_id("send")}),):
        d = await fb.describe_pending_action(call)
        assert d.target.startswith("https://x.test/alice/status/1 | composer ref 14")
    await fb.click(fb.element_id("composer"))
    with pytest.raises(Gated) as ei:
        await fb.press_key("Ctrl+Enter")
    assert ei.value.description.target.startswith("https://x.test/alice/status/1 | composer ref 14")
    await fb.click(fb.element_id("more"))
    with pytest.raises(Gated) as ei:
        await fb.click(fb.element_id("delete"))
    assert ei.value.description.target.startswith("https://x.test/alice/status/1 | menuitem ref 3")


async def test_element_without_scripted_ref_still_gets_a_ref():
    page = FakePage(url="https://x.test/p/5", elements=[FakeElement(name="Send DM", key="dm", risk="dm")])
    fb = FakeBrowser(page)
    d = await fb.describe_pending_action(ToolCall("click", {"element_id": 1}))
    assert d.target.startswith("https://x.test/p/5 | ") and "dm ref" in d.target


async def test_two_composers_on_one_page_have_different_targets_and_approvals_do_not_cross():
    fb = FakeBrowser(two_post_timeline())
    d1 = await fb.describe_pending_action(ToolCall("click", {"element_id": fb.element_id("send1")}))
    d2 = await fb.describe_pending_action(ToolCall("click", {"element_id": fb.element_id("send2")}))
    assert d1.target != d2.target
    assert "composer ref 1" in d1.target and "composer ref 2" in d2.target
    await fb.approve(d1)
    with pytest.raises(Gated):
        await fb.click(fb.element_id("send2"))  # approval for post 1 is refused for post 2
    assert fb.executed == []
    # Fail-closed (Agent Brain's deliberate change): the refused click cleared the approval for post 1,
    # so post 1 needs a fresh approve() too. Previously this asserted the old approval still worked.
    with pytest.raises(Gated):
        await fb.click(fb.element_id("send1"))
    await fb.approve(d1)
    assert (await fb.click(fb.element_id("send1"))).status == "ok"
    assert [e.target for e in fb.executed] == [d1.target]


async def test_same_ref_on_two_different_post_pages_has_different_targets():
    def page(url: str) -> FakePage:
        return FakePage(url=url, elements=[
            FakeElement(name="Post text", role="textbox", key="composer", editable=True, risk="comment",
                        ref="composer ref 14", value="same text"),
            FakeElement(name="Reply", key="send", risk="comment", controls="composer"),
        ])
    fb = FakeBrowser(page("https://x.test/post/123"))
    fb.pages["https://x.test/post/456"] = page("https://x.test/post/456")
    d123 = await fb.describe_pending_action(ToolCall("click", {"element_id": 2}))
    assert d123.target == "https://x.test/post/123 | composer ref 14"
    await fb.approve(d123)
    await fb.navigate("https://x.test/post/456")  # navigation clears it anyway
    d456 = await fb.describe_pending_action(ToolCall("click", {"element_id": 2}))
    assert d456.target == "https://x.test/post/456 | composer ref 14" != d123.target
    await fb.approve(d123)  # re-approve the OLD description while on the other post
    with pytest.raises(Gated):
        await fb.click(2)
    assert fb.executed == []
