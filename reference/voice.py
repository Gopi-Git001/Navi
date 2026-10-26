"""GPT Realtime voice session.

Three input modes, chosen to avoid the classic voice-agent failures:

  ptt   (default) Hold a key, talk, release. Audio only reaches the model while the key is held,
        so background noise, TV, other people and the agent's own voice can never trigger it.
  vad   Hands-free, but GATED: the server detects speech, we wait for the transcript, and only
        respond if it is a real command (filters noise/"uh"/phantom "Thank you." transcripts,
        optional wake word). The mic is muted while the agent is speaking unless you use headphones.
  text  Type commands in the console (debugging without a mic). Typing also works in voice modes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
import threading
import time

from websockets.asyncio.client import connect

from tools import INSTRUCTIONS, TOOL_SCHEMAS

RATE = 24000
CHUNK = 2400  # 100 ms

# Phrases speech-to-text models commonly hallucinate from noise/silence.
PHANTOMS = {
    "thank you", "thanks", "thank you for watching", "thanks for watching", "you", "bye", "okay", "ok",
    "uh", "um", "hmm", "mm", "mhm", "ah", "oh", "huh", "so", "yeah", "the", "a", "i", ".", "...",
    "please subscribe", "subscribe", "music", "[music]", "(music)", "applause", "silence",
}


def _now() -> str:
    return time.strftime("%H:%M:%S")


def log(tag: str, msg: str) -> None:
    print(f"{_now()} {tag:<6} {msg}", flush=True)


# ---------------------------------------------------------------------------- audio
class Player:
    def __init__(self):
        import sounddevice as sd
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._last = 0.0
        self.stream = sd.RawOutputStream(samplerate=RATE, channels=1, dtype="int16", callback=self._cb)

    def _cb(self, outdata, frames, t, status):
        n = len(outdata)
        with self._lock:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
        if chunk:
            self._last = time.monotonic()
        outdata[:] = chunk + b"\x00" * (n - len(chunk))

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()

    def feed(self, data: bytes):
        with self._lock:
            self._buf.extend(data)

    def clear(self):
        with self._lock:
            self._buf.clear()

    @property
    def busy(self) -> bool:
        with self._lock:
            pending = len(self._buf) > 0
        return pending or (time.monotonic() - self._last) < 0.6


class Mic:
    def __init__(self, loop: asyncio.AbstractEventLoop, queue: asyncio.Queue):
        import sounddevice as sd
        self.loop, self.queue = loop, queue
        self.stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=CHUNK,
                                        callback=self._cb)

    def _cb(self, indata, frames, t, status):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, bytes(indata))

    def start(self):
        self.stream.start()

    def stop(self):
        self.stream.stop()
        self.stream.close()


def _rms(chunk: bytes) -> float:
    import numpy as np
    a = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(a * a))) if a.size else 0.0


class PushToTalk:
    """Global hold-to-talk key (works while the browser window has focus)."""

    def __init__(self, loop, key_name: str, on_down, on_up):
        from pynput import keyboard
        self.kb = keyboard
        self.loop, self.on_down, self.on_up = loop, on_down, on_up
        self.key = getattr(keyboard.Key, key_name, None) or keyboard.KeyCode.from_char(key_name)
        self.held = False
        self.listener = keyboard.Listener(on_press=self._press, on_release=self._release)

    def _match(self, k) -> bool:
        return k == self.key or (hasattr(k, "char") and getattr(self.key, "char", None) and k.char == self.key.char)

    def _press(self, k):
        if self._match(k) and not self.held:
            self.held = True
            self.loop.call_soon_threadsafe(self.on_down)

    def _release(self, k):
        if self._match(k) and self.held:
            self.held = False
            self.loop.call_soon_threadsafe(self.on_up)

    def start(self):
        self.listener.start()

    def stop(self):
        self.listener.stop()


class StopHotkey:
    """Global Ctrl+Shift+Space. Stop always wins, even while push-to-talk is held.

    The chord logic (`press` / `release`) works on plain names so it can be tested without a keyboard.
    It fires once per chord: Space has to be released before it can fire again.
    """

    def __init__(self, on_stop):
        self.on_stop = on_stop
        self.down: set[str] = set()
        self.fired = False
        self.listener = None

    @staticmethod
    def name_of(key) -> str | None:
        n = getattr(key, "name", None) or ""
        if n.startswith("ctrl"):
            return "ctrl"
        if n.startswith("shift"):
            return "shift"
        if n == "space":
            return "space"
        return None

    def press(self, name: str | None) -> bool:
        if not name:
            return False
        self.down.add(name)
        if {"ctrl", "shift", "space"} <= self.down and not self.fired:
            self.fired = True
            self.on_stop()
            return True
        return False

    def release(self, name: str | None) -> None:
        if not name:
            return
        self.down.discard(name)
        if not {"ctrl", "shift", "space"} <= self.down:
            self.fired = False  # chord broken; the next full press fires again

    def start(self):
        from pynput import keyboard
        self.listener = keyboard.Listener(on_press=lambda k: self.press(self.name_of(k)),
                                          on_release=lambda k: self.release(self.name_of(k)))
        self.listener.start()

    def stop(self):
        if self.listener:
            self.listener.stop()


# ---------------------------------------------------------------------------- session
class VoiceSession:
    def __init__(self, cfg, registry, gate, mode: str = "ptt", headphones: bool = False,
                 wake_word: str | None = None):
        self.cfg, self.registry, self.gate = cfg, registry, gate
        self.mode = mode
        self.headphones = headphones
        self.wake = wake_word.lower().strip() if wake_word else None
        self.ws = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.player: Player | None = None
        self.mic: Mic | None = None
        self.ptt: PushToTalk | None = None
        self.stop_key: StopHotkey | None = None
        self.action_stopped = False                    # True only when the stop key cancelled the action
        self.action_task: asyncio.Task | None = None   # the tool call that is running right now
        self.mic_q: asyncio.Queue = asyncio.Queue()
        self.response_active = False
        self.response_wanted = False
        self.turn = 0              # increments on every new user command; stale tool chains stop
        self.recording = False
        self.rec_start = 0.0
        self.rec_peak = 0.0
        self.muted = False
        self.min_level = 200.0     # RMS below this while holding PTT = nothing was said
        self._printed_reply = False

    # ------------------------------------------------------------ config
    def _session(self) -> dict:
        audio_in: dict = {
            "format": {"type": "audio/pcm", "rate": RATE},
            "transcription": {"model": self.cfg.transcribe_model, "language": "en"},
            "noise_reduction": {"type": "near_field"},
        }
        if self.mode == "vad":
            audio_in["turn_detection"] = {"type": "semantic_vad", "eagerness": "low",
                                          "create_response": False, "interrupt_response": False}
        else:
            audio_in["turn_detection"] = None
        return {
            "type": "realtime",
            "model": self.cfg.realtime_model,
            "instructions": INSTRUCTIONS,
            "output_modalities": ["text"] if self.mode == "text" else ["audio"],
            "audio": {"input": audio_in,
                      "output": {"format": {"type": "audio/pcm", "rate": RATE}, "voice": self.cfg.voice}},
            "tools": TOOL_SCHEMAS,
            "tool_choice": "auto",
        }

    async def send(self, ev: dict) -> None:
        await self.ws.send(json.dumps(ev))

    # ------------------------------------------------------------ main
    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        url = f"wss://api.openai.com/v1/realtime?model={self.cfg.realtime_model}"
        headers = {"Authorization": f"Bearer {self.cfg.openai_api_key}"}
        async with connect(url, additional_headers=headers, max_size=None, ping_interval=20) as ws:
            self.ws = ws
            await self.send({"type": "session.update", "session": self._session()})
            tasks = [asyncio.create_task(self._recv()), asyncio.create_task(self._stdin())]
            if self.mode != "text":
                self.player = Player()
                self.player.start()
                self.mic = Mic(self.loop, self.mic_q)
                self.mic.start()
                tasks.append(asyncio.create_task(self._mic_pump()))
                if self.mode == "ptt":
                    self.ptt = PushToTalk(self.loop, self.cfg.ptt_key, self._ptt_down, self._ptt_up)
                    self.ptt.start()
            if self.mode != "text":
                try:
                    self.stop_key = StopHotkey(lambda: self.loop.call_soon_threadsafe(self.stop_everything))
                    self.stop_key.start()
                except Exception as e:  # no keyboard hook available: /stop still works
                    self.stop_key = None
                    log("sys", f"stop hotkey unavailable ({e}); type /stop instead")
            self._banner()
            try:
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for t in pending:
                    t.cancel()
                for t in done:
                    if t.exception():
                        raise t.exception()
            finally:
                for x in (self.ptt, self.stop_key, self.mic, self.player):
                    if x:
                        try:
                            x.stop()
                        except Exception:
                            pass

    def _banner(self):
        if self.mode == "ptt":
            how = f"HOLD [{self.cfg.ptt_key}] and speak, release when done."
        elif self.mode == "vad":
            how = "Hands-free: just speak." + (f" Start commands with '{self.wake}'." if self.wake else "")
            if not self.headphones:
                how += " (Mic is muted while I talk — use --headphones to allow interrupting.)"
        else:
            how = "Type commands below."
        print("\n" + "=" * 70 + f"\n Navi browser agent ready. {how}\n Typing works too. /mute toggles the mic, "
              "/stop cancels, /quit exits.\n Ctrl+Shift+Space stops everything, any time.\n" + "=" * 70 + "\n", flush=True)

    # ------------------------------------------------------------ input paths
    def _interrupt(self):
        """New user command: stop talking and abandon any in-flight tool chain."""
        self.turn += 1
        if self.player:
            self.player.clear()
        if self.response_active:
            asyncio.ensure_future(self.send({"type": "response.cancel"}))

    def stop_everything(self):
        """Stop hotkey / /stop: silence the speaker, cancel the reply, cancel the running action.

        Safe to call at any time, any number of times. A push-to-talk recording in progress is
        thrown away, so releasing the key afterwards sends nothing.
        """
        self._interrupt()
        self.response_wanted = False
        if self.recording:
            self.recording = False
        if self.action_task and not self.action_task.done():
            self.action_stopped = True
            self.action_task.cancel()
        if self.ws is not None:
            asyncio.ensure_future(self.send({"type": "input_audio_buffer.clear"}))
        log("sys", "STOPPED")

    def _ptt_down(self):
        if self.muted:
            return
        self._interrupt()
        self.recording = True
        self.rec_start = time.monotonic()
        self.rec_peak = 0.0
        asyncio.ensure_future(self.send({"type": "input_audio_buffer.clear"}))
        log("mic", "listening...")

    def _ptt_up(self):
        if not self.recording:
            return
        self.recording = False
        dur = time.monotonic() - self.rec_start
        if dur < 0.35 or self.rec_peak < self.min_level:
            asyncio.ensure_future(self.send({"type": "input_audio_buffer.clear"}))
            log("mic", "ignored (too short or silent)")
            return
        asyncio.ensure_future(self._commit_and_respond())

    async def _commit_and_respond(self):
        await self.send({"type": "input_audio_buffer.commit"})
        await self._request_response()

    async def _mic_pump(self):
        while True:
            chunk = await self.mic_q.get()
            if self.muted:
                continue
            if self.mode == "ptt":
                if not self.recording:
                    continue
                self.rec_peak = max(self.rec_peak, _rms(chunk))
            elif self.mode == "vad":
                if not self.headphones and self.player and self.player.busy:
                    continue  # don't let the agent hear itself
            await self.send({"type": "input_audio_buffer.append", "audio": base64.b64encode(chunk).decode()})

    async def _stdin(self):
        while True:
            line = await self.loop.run_in_executor(None, sys.stdin.readline)
            if line == "":
                if self.mode == "text":
                    return
                await asyncio.sleep(3600)
                continue
            line = line.strip()
            if not line:
                continue
            if line == "/quit":
                return
            if line == "/mute":
                self.muted = not self.muted
                log("mic", "muted" if self.muted else "unmuted")
                continue
            if line == "/stop":
                self.stop_everything()
                continue
            self._interrupt()
            self.gate.note_user(line)
            await self.send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": line}]}})
            await self._request_response()

    # ------------------------------------------------------------ transcript gating (vad mode)
    def _is_command(self, text: str) -> tuple[bool, str]:
        t = re.sub(r"[^\w\s']", " ", text.lower()).strip()
        t = re.sub(r"\s+", " ", t)
        if not t or t in PHANTOMS:
            return False, "noise/filler"
        if self.wake:
            if self.wake not in t:
                return False, f"no wake word '{self.wake}'"
            return True, ""
        if len(t.split()) < 2 and t not in {"stop", "back", "scroll", "yes", "no", "next", "refresh", "cancel"}:
            return False, "too short"
        return True, ""

    async def _on_transcript(self, ev: dict):
        text = (ev.get("transcript") or "").strip()
        if not text:
            return
        if self.mode == "vad":
            ok, why = self._is_command(text)
            if not ok:
                log("skip", f"\"{text}\" ({why})")
                if ev.get("item_id"):
                    await self.send({"type": "conversation.item.delete", "item_id": ev["item_id"]})
                return
            log("you", text)
            self.gate.note_user(text)
            self._interrupt()
            await self._request_response()
        else:
            log("you", text)
            self.gate.note_user(text)

    # ------------------------------------------------------------ responses & tools
    async def _request_response(self):
        if self.response_active:
            self.response_wanted = True
            return
        self.response_active = True
        await self.send({"type": "response.create"})

    async def _recv(self):
        async for raw in self.ws:
            ev = json.loads(raw)
            t = ev.get("type", "")
            if t in ("response.output_audio.delta", "response.audio.delta"):
                if self.player:
                    self.player.feed(base64.b64decode(ev["delta"]))
            elif t in ("response.output_audio_transcript.delta", "response.audio_transcript.delta",
                       "response.output_text.delta", "response.text.delta"):
                if not self._printed_reply:
                    print(f"{_now()} navi   ", end="", flush=True)
                    self._printed_reply = True
                print(ev.get("delta", ""), end="", flush=True)
            elif t == "conversation.item.input_audio_transcription.completed":
                await self._on_transcript(ev)
            elif t == "response.created":
                self.response_active = True
            elif t == "response.done":
                await self._on_response_done(ev.get("response", {}))
            elif t == "error":
                err = ev.get("error", {})
                msg = err.get("message", str(err))
                if "active response" in msg or "no active response" in msg.lower():
                    continue  # benign races around cancel/create
                log("error", msg)
            elif t == "session.updated":
                log("sys", "session configured")
        raise ConnectionError("realtime connection closed")

    async def _on_response_done(self, resp: dict):
        if self._printed_reply:
            print(flush=True)
            self._printed_reply = False
        self.response_active = False
        status = resp.get("status")
        if status == "failed":
            log("error", json.dumps(resp.get("status_details", {}))[:300])
        calls = [o for o in resp.get("output", []) if o.get("type") == "function_call"]
        if calls:
            asyncio.ensure_future(self._run_calls(calls, cancelled=(status == "cancelled")))
        elif self.response_wanted:
            self.response_wanted = False
            await self._request_response()

    async def _run_calls(self, calls: list[dict], cancelled: bool):
        my_turn = self.turn
        for c in calls:
            name, call_id = c.get("name"), c.get("call_id")
            try:
                args = json.loads(c.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            if cancelled or self.turn != my_turn:
                await self._output(call_id, "Cancelled: the user gave a new command.")
                continue
            short = json.dumps(args, ensure_ascii=False)
            log("tool", f"{name} {short[:150]}")
            self.action_stopped = False
            self.action_task = asyncio.ensure_future(self.registry.call(name, args))
            try:
                res = await self.action_task
            except asyncio.CancelledError:
                if not self.action_stopped:
                    raise  # the whole session was cancelled, not just this action
                log("  ->", "stopped by user")
                await self._output(call_id, "Stopped by the user. Do not continue this action.")
                continue
            finally:
                self.action_task = None
            first = res.text.splitlines()[0] if res.text else ""
            log("  ->", first[:150])
            await self._output(call_id, res.text)
            if res.image_b64:
                await self.send({"type": "conversation.item.create", "item": {
                    "type": "message", "role": "user",
                    "content": [{"type": "input_image", "image_url": f"data:image/jpeg;base64,{res.image_b64}"}]}})
        if self.response_wanted or (not cancelled and self.turn == my_turn):
            self.response_wanted = False
            await self._request_response()

    async def _output(self, call_id: str, text: str):
        await self.send({"type": "conversation.item.create",
                         "item": {"type": "function_call_output", "call_id": call_id, "output": text}})

    async def notify(self, text: str):
        """Background events (research finished) get announced."""
        log("bg", text[:200])
        await self.send({"type": "conversation.item.create", "item": {
            "type": "message", "role": "system",
            "content": [{"type": "input_text", "text": f"[Background update, not from the user] {text} "
                                                        "Briefly tell the user."}]}})
        await self._request_response()
