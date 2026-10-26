"""Tests for the global stop hotkey (Ctrl+Shift+Space) and VoiceSession.stop_everything().

Run:  python -m pytest reference/test_voice_stop.py -q      (no mic, keyboard or network needed)
"""
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import pytest

from voice import StopHotkey, VoiceSession


# ------------------------------------------------------------------ helpers
class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    def types(self):
        return [e["type"] for e in self.sent]


class FakePlayer:
    def __init__(self):
        self.cleared = 0

    def clear(self):
        self.cleared += 1


class Res:
    def __init__(self, text):
        self.text, self.image_b64 = text, None


class SlowRegistry:
    """A tool call that takes a long time, like a page load."""

    def __init__(self):
        self.started = asyncio.Event()
        self.calls = []
        self.finished = 0

    async def call(self, name, args):
        self.calls.append(name)
        self.started.set()
        await asyncio.sleep(30)
        self.finished += 1
        return Res("done")


def make_session(registry=None):
    s = VoiceSession(cfg=None, registry=registry, gate=None, mode="ptt")
    s.ws = FakeWS()
    s.player = FakePlayer()
    s.loop = asyncio.get_running_loop()
    return s


async def drain():
    for _ in range(5):
        await asyncio.sleep(0)


def fn_call(i, name="click"):
    return {"type": "function_call", "name": name, "call_id": f"c{i}", "arguments": "{}"}


def outputs(s):
    return [e["item"]["output"] for e in s.ws.sent
            if e["type"] == "conversation.item.create" and e["item"].get("type") == "function_call_output"]


# ------------------------------------------------------------------ the chord
def press_all(h, *names):
    return [h.press(n) for n in names]


def test_chord_fires_once_on_ctrl_shift_space():
    fired = []
    h = StopHotkey(lambda: fired.append(1))
    press_all(h, "ctrl", "shift", "space")
    assert fired == [1]
    h.press("space")  # key auto-repeat while held
    h.press("space")
    assert fired == [1]


def test_partial_chords_do_not_fire():
    fired = []
    h = StopHotkey(lambda: fired.append(1))
    press_all(h, "ctrl", "space")
    h.release("space"); h.release("ctrl")
    press_all(h, "shift", "space")
    h.release("space"); h.release("shift")
    press_all(h, "space")
    assert fired == []


def test_chord_fires_again_after_release():
    fired = []
    h = StopHotkey(lambda: fired.append(1))
    press_all(h, "ctrl", "shift", "space")
    h.release("space")
    h.press("space")
    assert fired == [1, 1]


def test_order_does_not_matter_and_unknown_keys_are_ignored():
    fired = []
    h = StopHotkey(lambda: fired.append(1))
    press_all(h, "space", "shift", None, "ctrl")
    assert fired == [1]


def test_key_names_cover_left_right_and_generic_keys():
    class K:
        def __init__(self, name):
            self.name = name
    for n in ("ctrl", "ctrl_l", "ctrl_r"):
        assert StopHotkey.name_of(K(n)) == "ctrl"
    for n in ("shift", "shift_l", "shift_r"):
        assert StopHotkey.name_of(K(n)) == "shift"
    assert StopHotkey.name_of(K("space")) == "space"
    assert StopHotkey.name_of(K("enter")) is None
    assert StopHotkey.name_of("a") is None  # plain character keys have no .name


def test_right_ctrl_held_for_push_to_talk_still_completes_the_chord():
    """Right Ctrl is the PTT key. Holding it, then Shift+Space, must stop."""
    fired = []
    h = StopHotkey(lambda: fired.append(1))
    press_all(h, "ctrl", "shift", "space")  # ctrl_r normalises to "ctrl"
    assert fired == [1]


# ------------------------------------------------------------------ stop_everything
async def test_stop_with_nothing_running_is_safe_and_repeatable():
    s = make_session()
    s.stop_everything()
    s.stop_everything()
    await drain()
    assert s.player.cleared == 2
    assert "response.cancel" not in s.ws.types()


async def test_stop_cancels_the_reply_and_flushes_audio():
    s = make_session()
    s.response_active = True
    s.response_wanted = True
    turn = s.turn
    s.stop_everything()
    await drain()
    assert s.player.cleared == 1
    assert "response.cancel" in s.ws.types()
    assert s.turn == turn + 1
    assert s.response_wanted is False


async def test_stop_cancels_the_running_action_and_does_not_start_a_new_reply():
    reg = SlowRegistry()
    s = make_session(reg)
    chain = asyncio.ensure_future(s._run_calls([fn_call(1), fn_call(2)], cancelled=False))
    await asyncio.wait_for(reg.started.wait(), 1)
    s.stop_everything()
    await asyncio.wait_for(chain, 1)
    assert reg.finished == 0, "the action must not run to completion"
    assert reg.calls == ["click"], "the second call in the chain must not start"
    outs = outputs(s)
    assert len(outs) == 2 and outs[0].startswith("Stopped by the user")
    assert outs[1].startswith("Cancelled")
    assert "response.create" not in s.ws.types(), "stop must not trigger a new reply"
    assert s.action_task is None


async def test_stop_while_ptt_is_held_discards_the_recording():
    s = make_session()
    s._ptt_down()
    assert s.recording
    s.stop_everything()
    await drain()
    s._ptt_up()  # user lets go of Right Ctrl afterwards
    await drain()
    assert not s.recording
    types = s.ws.types()
    assert "input_audio_buffer.commit" not in types
    assert "response.create" not in types


async def test_new_command_after_stop_works_normally():
    reg = SlowRegistry()
    s = make_session(reg)
    chain = asyncio.ensure_future(s._run_calls([fn_call(1)], cancelled=False))
    await asyncio.wait_for(reg.started.wait(), 1)
    s.stop_everything()
    await asyncio.wait_for(chain, 1)

    async def quick(name, args):
        return Res("ok")
    reg.call = quick
    await s._run_calls([fn_call(2)], cancelled=False)
    assert "response.create" in s.ws.types()
    assert outputs(s)[-1] == "ok"


async def test_cancelling_the_whole_session_is_not_mistaken_for_a_stop():
    reg = SlowRegistry()
    s = make_session(reg)
    chain = asyncio.ensure_future(s._run_calls([fn_call(1)], cancelled=False))
    await asyncio.wait_for(reg.started.wait(), 1)
    chain.cancel()
    with pytest.raises(asyncio.CancelledError):
        await chain
