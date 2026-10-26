"""Audio and key interfaces for the voice layer (docs/TECH_SPEC.md sections 2 and 4).

Three small interfaces let the same voice code run against a real laptop (sounddevice,
pynput; added in M1) or against recorded clips (voice/testio.py):

- ``AudioSource``: yields 24 kHz mono 16-bit PCM in 100 ms chunks and owns a clock.
- ``AudioSink``: accepts PCM chunks that Navi wants to play, and can flush them.
- ``KeyInjector``: the push-to-talk key and the stop hotkey, as events.

Nothing here knows about the model, tools, or the browser.
"""
from __future__ import annotations

import abc
from typing import AsyncIterator, Callable, List, NamedTuple, Optional, Protocol

SAMPLE_RATE = 24_000  # Hz, mono, signed 16-bit little-endian
CHUNK_MS = 100
CHUNK_SAMPLES = SAMPLE_RATE * CHUNK_MS // 1000  # 2400
CHUNK_BYTES = CHUNK_SAMPLES * 2  # 4800


class Clock(Protocol):
    """Milliseconds on one shared timeline (monotonic; zero at the start of the run)."""

    def now_ms(self) -> float: ...


class AudioSource(abc.ABC):
    """Where microphone audio comes from."""

    #: The clock this source runs on. Keys and sinks in a test share it.
    clock: Clock

    @abc.abstractmethod
    def chunks(self) -> AsyncIterator[bytes]:
        """Async iterator of 100 ms chunks (``CHUNK_BYTES`` each), delivered in real time."""


class AudioSink(abc.ABC):
    """Where Navi's speech goes."""

    @abc.abstractmethod
    def write(self, chunk: bytes) -> None:
        """Queue PCM16 mono 24 kHz audio for playback."""

    @abc.abstractmethod
    def flush(self) -> None:
        """Drop everything not yet played (our buffer and the device buffer) right now."""

    @abc.abstractmethod
    def close(self) -> None:
        """Release the device or file. Safe to call twice."""


class KeyEvent(NamedTuple):
    kind: str  # "down" | "up" | "hotkey"
    name: str  # logical names: "ptt" (the push-to-talk key), "stop" (the stop hotkey)
    t_ms: Optional[float] = None  # injector clock time, when known


KeyHandler = Callable[[KeyEvent], None]


class KeyInjector:
    """Delivers key events to whoever subscribed (the voice layer).

    ``key_down``, ``key_up`` and ``hotkey`` are what a benchmark or a real keyboard hook calls.
    Names are logical: the real implementation maps ``"ptt"`` to the configured key
    (Right Ctrl by default) and ``"stop"`` to Ctrl+Shift+Space. A handler that raises is not
    swallowed: the error reaches the caller so a broken handler fails the run.
    """

    def __init__(self, clock: Optional[Clock] = None) -> None:
        self._handlers: List[KeyHandler] = []
        self._clock = clock

    def subscribe(self, handler: KeyHandler) -> None:
        self._handlers.append(handler)

    def key_down(self, name: str) -> None:
        self._emit("down", name)

    def key_up(self, name: str) -> None:
        self._emit("up", name)

    def hotkey(self, name: str) -> None:
        self._emit("hotkey", name)

    def _emit(self, kind: str, name: str) -> None:
        t = self._clock.now_ms() if self._clock is not None else None
        event = KeyEvent(kind, name, t)
        for handler in list(self._handlers):
            handler(event)
