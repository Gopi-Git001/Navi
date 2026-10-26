"""Test versions of the voice I/O interfaces (docs/TECH_SPEC.md section 4).

Lets a benchmark feed a recorded clip in, press the push-to-talk key and the stop hotkey on
a script, and capture Navi's speech out, with no microphone, speaker, or keyboard:

- ``FileAudioSource``: a WAV file as microphone audio (24 kHz mono 16-bit, 100 ms chunks).
- ``ScriptedKeys``: ``(t_ms, event)`` key events fired on the same clock as the audio.
- ``CaptureSink``: records everything Navi plays into a time-aligned WAV and marks the first
  audible sample.
- ``StreamClock`` / ``VirtualTime``: the shared clock, and a virtual-time driver for exact,
  instant unit tests.

Real ``sounddevice`` and ``pynput`` versions come in M1. Linux-safe: uses only numpy,
soundfile and the standard library.
"""
from __future__ import annotations

import asyncio
import heapq
import itertools
import json
import time
import wave
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, List, Optional, Sequence, Tuple, Union

import numpy as np

from voice.io import (
    CHUNK_MS,
    CHUNK_SAMPLES,
    SAMPLE_RATE,
    AudioSink,
    AudioSource,
    Clock,
    KeyInjector,
)
from voice.marks import MarkWriter

PathLike = Union[str, Path]


class AudioFileError(Exception):
    """The audio file is missing, unreadable, or not audio."""


# ---------------------------------------------------------------- clocks

class StreamClock:
    """Milliseconds since ``start()``, scaled by ``speed`` (1.0 = real time).

    ``speed`` above 1 is for smoke runs only: stream time then runs faster than wall time,
    so latency numbers from such a run are not real. ``time_fn`` and ``sleep`` can be
    replaced (see ``VirtualTime``) to make tests exact and instant.
    """

    def __init__(
        self,
        time_fn: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        speed: float = 1.0,
    ) -> None:
        if speed <= 0:
            raise ValueError("speed must be positive")
        self._time = time_fn
        self._sleep = sleep
        self.speed = speed
        self._t0: Optional[float] = None

    def start(self) -> None:
        """Zero the clock. Calling it again does nothing."""
        if self._t0 is None:
            self._t0 = self._time()

    def now_ms(self) -> float:
        if self._t0 is None:
            return 0.0
        return (self._time() - self._t0) * 1000.0 * self.speed

    async def sleep_until(self, t_ms: float) -> None:
        """Return when the clock reaches ``t_ms`` (immediately if it already has)."""
        while True:
            remaining = t_ms - self.now_ms()
            if remaining <= 1e-6:
                return
            await self._sleep(remaining / 1000.0 / self.speed)


class VirtualTime:
    """Deterministic fake time for tests: ``sleep`` costs no real time, ``now`` is exact.

    ``await vt.run(coro, ...)`` runs the coroutines together, jumping time forward to the next
    sleeper whenever everything else is waiting.
    """

    def __init__(self) -> None:
        self._t = 0.0
        self._queue: List[Tuple[float, int, asyncio.Future]] = []
        self._counter = itertools.count()

    def now(self) -> float:
        return self._t

    async def sleep(self, seconds: float) -> None:
        if seconds <= 0:
            await asyncio.sleep(0)
            return
        fut = asyncio.get_running_loop().create_future()
        heapq.heappush(self._queue, (self._t + seconds, next(self._counter), fut))
        await fut

    async def run(self, *coros) -> list:
        tasks = [asyncio.ensure_future(c) for c in coros]
        idle = 0
        try:
            while not all(t.done() for t in tasks):
                for _ in range(50):  # let every task run until it blocks
                    await asyncio.sleep(0)
                for t in tasks:
                    if t.done() and not t.cancelled() and t.exception() is not None:
                        raise t.exception()
                if all(t.done() for t in tasks):
                    break
                while self._queue and self._queue[0][2].done():
                    heapq.heappop(self._queue)
                if self._queue:
                    idle = 0
                    deadline, _, fut = heapq.heappop(self._queue)
                    self._t = max(self._t, deadline)
                    fut.set_result(None)
                else:
                    idle += 1
                    if idle > 200:
                        raise RuntimeError("VirtualTime: tasks are waiting on something other than virtual time")
                    await asyncio.sleep(0.001)
            return [t.result() for t in tasks]
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


# ---------------------------------------------------------------- audio helpers

def resample_to_24k(x: np.ndarray, sr: int) -> np.ndarray:
    """Mono float32 samples at ``sr`` Hz to 24 kHz. Low-pass first when going down.

    Good enough for speech test clips (windowed-sinc low-pass, then linear interpolation);
    not meant as a general-purpose resampler.
    """
    if sr == SAMPLE_RATE or len(x) == 0:
        return x
    if sr > SAMPLE_RATE:
        fc = 0.45 * SAMPLE_RATE / sr  # cycles per sample
        n = np.arange(-63, 64)
        h = np.sinc(2 * fc * n) * np.blackman(len(n))
        h /= h.sum()
        x = np.convolve(x, h, mode="same")
    n_out = int(len(x) * SAMPLE_RATE / sr)
    pos = np.arange(n_out) * (sr / SAMPLE_RATE)
    return np.interp(pos, np.arange(len(x)), x).astype(np.float32)


def _to_pcm16(x: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(x * 32767.0), -32768, 32767).astype("<i2")


# ---------------------------------------------------------------- source

class FileAudioSource(AudioSource):
    """A WAV file played as microphone audio.

    Reads any sample rate and channel count, converts to 24 kHz mono 16-bit, and delivers
    100 ms chunks paced to the clock (the chunk covering 0-100 ms arrives at 100 ms, like a
    real microphone). The last chunk is padded with silence. The clock starts when iteration
    starts; its zero is the first emitted sample (after ``start_ms`` is skipped).

    ``speed`` above 1 is for smoke tests only; ``tail_ms`` appends silence so a run can keep
    going while Navi replies.
    """

    def __init__(
        self,
        wav_path: PathLike,
        start_ms: float = 0,
        speed: float = 1.0,
        tail_ms: float = 0,
        time_fn: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        import soundfile as sf

        path = Path(wav_path)
        if speed <= 0:
            raise ValueError("speed must be positive")
        if start_ms < 0 or tail_ms < 0:
            raise ValueError("start_ms and tail_ms must not be negative")
        if not path.exists():
            raise AudioFileError(f"audio file not found: {path}")
        try:
            data, sr = sf.read(str(path), dtype="float32", always_2d=True)
        except (RuntimeError, ValueError, OSError) as exc:
            raise AudioFileError(f"cannot read audio file {path}: {exc}") from exc
        skip = int(round(start_ms * sr / 1000.0))
        if skip > data.shape[0]:
            raise ValueError(f"start_ms={start_ms} is past the end of {path} ({data.shape[0] / sr * 1000:.0f} ms)")
        mono = data[skip:].mean(axis=1) if data.shape[1] > 1 else data[skip:, 0]
        pcm = _to_pcm16(resample_to_24k(mono.astype(np.float32), int(sr)))
        pcm = np.concatenate([pcm, np.zeros(int(round(tail_ms * SAMPLE_RATE / 1000.0)), dtype="<i2")])
        pad = (-len(pcm)) % CHUNK_SAMPLES
        if pad:
            pcm = np.concatenate([pcm, np.zeros(pad, dtype="<i2")])
        self._pcm = pcm
        self.path = path
        self.speed = speed
        self.clock = StreamClock(time_fn=time_fn, sleep=sleep, speed=speed)
        self._used = False

    @property
    def duration_ms(self) -> float:
        return len(self._pcm) * 1000.0 / SAMPLE_RATE

    async def chunks(self) -> AsyncIterator[bytes]:
        if self._used:
            raise RuntimeError("a FileAudioSource can only be played once; create a new one")
        self._used = True
        self.clock.start()
        for k in range(len(self._pcm) // CHUNK_SAMPLES):
            await self.clock.sleep_until((k + 1) * CHUNK_MS)
            yield self._pcm[k * CHUNK_SAMPLES:(k + 1) * CHUNK_SAMPLES].tobytes()


# ---------------------------------------------------------------- keys

_KEY_EVENTS = {
    "ptt_down": lambda k: k.key_down("ptt"),
    "ptt_up": lambda k: k.key_up("ptt"),
    "stop_hotkey": lambda k: k.hotkey("stop"),
}


class ScriptedKeys(KeyInjector):
    """Fires ``(t_ms, event)`` key events on a shared clock.

    Events: ``ptt_down``, ``ptt_up``, ``stop_hotkey``. Same-time events fire in the order
    given. The stop hotkey fires whatever state push-to-talk is in. ``fired`` records
    ``(actual_clock_ms, event)`` for each event.
    """

    def __init__(self, events: Sequence[Tuple[float, str]], clock: Clock) -> None:
        super().__init__(clock)
        checked: List[Tuple[float, str]] = []
        for item in events:
            try:
                t, name = item
            except (TypeError, ValueError):
                raise ValueError(f"key event must be (t_ms, event), got {item!r}") from None
            if isinstance(t, bool) or not isinstance(t, (int, float)) or t < 0:
                raise ValueError(f"key event time must be a number >= 0, got {t!r}")
            if name not in _KEY_EVENTS:
                raise ValueError(f"unknown key event {name!r}; valid: {', '.join(_KEY_EVENTS)}")
            checked.append((t, name))
        self.events: List[Tuple[float, str]] = sorted(checked, key=lambda e: e[0])  # stable
        self._clock_obj = clock
        self.fired: List[Tuple[float, str]] = []

    @classmethod
    def from_json(cls, path: PathLike, clock: Clock) -> "ScriptedKeys":
        """Load ``[[500, "ptt_down"], ...]`` or ``[{"t_ms": 500, "event": "ptt_down"}, ...]``."""
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"cannot read key script {path}: {exc}") from exc
        if not isinstance(raw, list):
            raise ValueError(f"key script {path} must be a JSON list")
        events = []
        for item in raw:
            if isinstance(item, dict):
                if "t_ms" not in item or "event" not in item:
                    raise ValueError(f"key script entry needs t_ms and event: {item!r}")
                events.append((item["t_ms"], item["event"]))
            else:
                events.append(tuple(item) if isinstance(item, list) else item)
        return cls(events, clock)

    async def run(self) -> None:
        """Wait for each event's time on the clock and fire it. Starts the clock if needed."""
        start = getattr(self._clock_obj, "start", None)
        if start is not None:
            start()
        sleep_until = self._clock_obj.sleep_until  # type: ignore[attr-defined]
        for t_ms, name in self.events:
            await sleep_until(t_ms)
            self.fired.append((self._clock_obj.now_ms(), name))
            _KEY_EVENTS[name](self)


# ---------------------------------------------------------------- sink

class CaptureSink(AudioSink):
    """Records what Navi plays into a WAV that lines up with the clock.

    Gaps between writes become silence, and audio written faster than real time is laid out
    back to back (as a real speaker would play it). The first audible sample of each reply is
    marked ``first_sample_out`` (when ``marks`` is given). A reply is "new" after a flush or
    after ``rearm_gap_ms`` of silence. ``flush()`` drops audio that would not have played yet,
    and marks ``audio_silent`` at the time of the flush. The WAV is written on ``close()``.
    """

    def __init__(
        self,
        out_wav: PathLike,
        marks: Optional[MarkWriter],
        clock: Clock,
        silence_threshold: int = 256,
        rearm_gap_ms: float = 1000.0,
        origin_ms: float = 0.0,
    ) -> None:
        self.out_wav = Path(out_wav)
        self._marks = marks
        self._clock = clock
        self._thr = silence_threshold
        self._rearm_gap_ms = rearm_gap_ms
        self._origin = origin_ms
        self._parts: List[np.ndarray] = []
        self._n = 0  # samples laid out on the timeline so far
        self._last_audible_end: Optional[float] = None
        self._closed = False

    def _end_ms(self) -> float:
        return self._origin + self._n * 1000.0 / SAMPLE_RATE

    def write(self, chunk: bytes) -> None:
        if self._closed:
            raise ValueError("CaptureSink is closed")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError(f"chunk must be bytes, got {type(chunk).__name__}")
        if len(chunk) % 2:
            raise ValueError("chunk is not 16-bit PCM (odd number of bytes)")
        samples = np.frombuffer(bytes(chunk), dtype="<i2")
        now = self._clock.now_ms()
        end = self._end_ms()
        if now > end:  # nothing was playing: keep the file aligned with the clock
            gap = int(round((now - end) * SAMPLE_RATE / 1000.0))
            if gap:
                self._parts.append(np.zeros(gap, dtype="<i2"))
                self._n += gap
        play_start = self._end_ms()
        self._parts.append(samples.copy())
        self._n += len(samples)
        loud = np.flatnonzero(np.abs(samples.astype(np.int32)) > self._thr)
        if loud.size:
            onset = play_start + loud[0] * 1000.0 / SAMPLE_RATE
            audible_end = play_start + (loud[-1] + 1) * 1000.0 / SAMPLE_RATE
            new_reply = self._last_audible_end is None or (onset - self._last_audible_end) >= self._rearm_gap_ms
            if new_reply and self._marks is not None:
                self._marks.mark("first_sample_out", t_ms=onset)
            self._last_audible_end = audible_end

    def flush(self) -> None:
        if self._closed:
            return
        now = self._clock.now_ms()
        keep = max(0, int(round((now - self._origin) * SAMPLE_RATE / 1000.0)))
        if keep < self._n:
            joined = np.concatenate(self._parts) if self._parts else np.zeros(0, dtype="<i2")
            self._parts = [joined[:keep]]
            self._n = keep
        self._last_audible_end = None
        if self._marks is not None:
            self._marks.mark("audio_silent", t_ms=now)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.out_wav.parent.mkdir(parents=True, exist_ok=True)
        data = np.concatenate(self._parts) if self._parts else np.zeros(0, dtype="<i2")
        with wave.open(str(self.out_wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(data.astype("<i2").tobytes())
