"""Tests for the voice test interface (issue #9): voice/io.py, voice/testio.py, voice/marks.py.

Covers: the named "done when" tests from the issue, plus the failure cases listed before
coding (empty input, errors, two things at once, safety). Everything is Linux-safe: no
sounddevice, no pynput, no microphone. Time is virtual (VirtualTime), so tests are exact.
"""
import asyncio
import json
import wave

import numpy as np
import pytest
import soundfile as sf

from voice.io import (
    CHUNK_BYTES,
    CHUNK_MS,
    CHUNK_SAMPLES,
    SAMPLE_RATE,
    AudioSink,
    AudioSource,
    KeyEvent,
    KeyInjector,
)
from voice.marks import MARK_NAMES, MarkWriter, read_marks
from voice.testio import (
    AudioFileError,
    CaptureSink,
    FileAudioSource,
    ScriptedKeys,
    StreamClock,
    VirtualTime,
)


# ---------------------------------------------------------------- helpers

def tone(seconds, sr, freq=1000.0, amp=0.5):
    t = np.arange(int(seconds * sr)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def write_wav(path, data, sr, channels=1):
    sf.write(str(path), data, sr, subtype="PCM_16")
    return path


def onset_clip(path, sr=48000, onset_s=1.234, total_s=3.0):
    """Silence, then a tone starting at exactly onset_s."""
    x = np.zeros(int(total_s * sr), dtype=np.float32)
    i = int(round(onset_s * sr))
    x[i:] = tone(total_s - onset_s, sr)
    return write_wav(path, x, sr)


def pcm(chunk):
    return np.frombuffer(chunk, dtype="<i2")


async def collect(source):
    return [c async for c in source.chunks()]


def run(vt, *coros):
    """Run coroutines to completion on virtual time; returns their results."""
    return asyncio.run(vt.run(*coros))


def make_source(path, vt, **kw):
    return FileAudioSource(path, time_fn=vt.now, sleep=vt.sleep, **kw)


# ---------------------------------------------------------------- interface shape

def test_constants_match_tech_spec():
    assert SAMPLE_RATE == 24000
    assert CHUNK_MS == 100
    assert CHUNK_SAMPLES == 2400
    assert CHUNK_BYTES == 4800


def test_interfaces_are_abstract_and_real_ones_can_subclass():
    with pytest.raises(TypeError):
        AudioSource()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        AudioSink()  # type: ignore[abstract]
    # KeyInjector is a concrete dispatcher: real (pynput) injectors reuse it.
    seen = []
    k = KeyInjector()
    k.subscribe(seen.append)
    k.key_down("ptt")
    k.key_up("ptt")
    k.hotkey("stop")
    assert [(e.kind, e.name) for e in seen] == [("down", "ptt"), ("up", "ptt"), ("hotkey", "stop")]
    assert all(isinstance(e, KeyEvent) for e in seen)


# ---------------------------------------------------------------- FileAudioSource

def test_file_source_yields_24k_mono_16bit_100ms_chunks_from_48k_wav(tmp_path):
    wav = write_wav(tmp_path / "c.wav", tone(1.0, 48000), 48000)
    vt = VirtualTime()
    src = make_source(wav, vt)
    chunks = run(vt, collect(src))[0]
    assert len(chunks) == 10  # 1.0 s / 100 ms
    assert all(isinstance(c, bytes) and len(c) == CHUNK_BYTES for c in chunks)
    data = np.concatenate([pcm(c) for c in chunks])
    assert data.dtype == np.int16
    assert len(data) == SAMPLE_RATE  # 24k samples for 1 s
    # a 1 kHz tone survives resampling: dominant FFT bin is at 1 kHz
    spec = np.abs(np.fft.rfft(data[2400:-2400].astype(np.float32)))
    peak_hz = np.argmax(spec) * SAMPLE_RATE / (len(data) - 4800)
    assert abs(peak_hz - 1000) < 20
    # amplitude roughly preserved (0.5 full scale)
    assert 0.45 * 32767 < np.max(np.abs(data)) < 0.55 * 32767


def test_file_source_downmixes_stereo_and_handles_24k_and_odd_rates(tmp_path):
    left = tone(0.5, 44100, 500)
    right = tone(0.5, 44100, 500)
    stereo = np.stack([left, right], axis=1)
    p = tmp_path / "st.wav"
    sf.write(str(p), stereo, 44100, subtype="PCM_16")
    vt = VirtualTime()
    chunks = run(vt, collect(make_source(p, vt)))[0]
    assert len(chunks) == 5
    p2 = write_wav(tmp_path / "n.wav", tone(0.5, 24000), 24000)
    vt2 = VirtualTime()
    chunks2 = run(vt2, collect(make_source(p2, vt2)))[0]
    assert len(chunks2) == 5
    p3 = write_wav(tmp_path / "lo.wav", tone(0.5, 16000), 16000)
    vt3 = VirtualTime()
    assert len(run(vt3, collect(make_source(p3, vt3)))[0]) == 5


def test_final_partial_chunk_is_padded_with_silence(tmp_path):
    wav = write_wav(tmp_path / "p.wav", tone(0.25, 48000), 48000)  # 250 ms
    vt = VirtualTime()
    chunks = run(vt, collect(make_source(wav, vt)))[0]
    assert len(chunks) == 3
    assert all(len(c) == CHUNK_BYTES for c in chunks)
    assert not pcm(chunks[-1])[-100:].any()  # padded tail is digital silence


def test_start_ms_skips_into_the_clip(tmp_path):
    wav = onset_clip(tmp_path / "o.wav", onset_s=1.0, total_s=2.0)
    vt = VirtualTime()
    chunks = run(vt, collect(make_source(wav, vt, start_ms=1000)))[0]
    assert len(chunks) == 10
    assert np.max(np.abs(pcm(chunks[0]))) > 1000  # tone is audible from the very first chunk


def test_source_exposes_its_clock_and_paces_chunks_in_real_time(tmp_path):
    wav = write_wav(tmp_path / "c.wav", tone(1.0, 48000), 48000)
    vt = VirtualTime()
    src = make_source(wav, vt)
    stamps = []

    async def consume():
        async for _ in src.chunks():
            stamps.append(src.clock.now_ms())

    run(vt, consume())
    # chunk k is delivered when its 100 ms have elapsed on the source clock
    assert stamps == pytest.approx([100.0 * (k + 1) for k in range(10)], abs=1.0)
    assert src.speed == 1.0


def test_speed_up_is_visible_so_latency_numbers_can_be_rejected(tmp_path):
    wav = write_wav(tmp_path / "c.wav", tone(1.0, 48000), 48000)
    vt = VirtualTime()
    src = make_source(wav, vt, speed=10.0)
    run(vt, collect(src))
    assert src.speed == 10.0
    assert vt.now() == pytest.approx(1.0 / 10.0, abs=0.01)  # 1 s of audio took 0.1 s
    assert src.clock.now_ms() == pytest.approx(1000.0, abs=15.0)  # clock is in stream time


def test_tail_ms_appends_trailing_silence(tmp_path):
    wav = write_wav(tmp_path / "c.wav", tone(0.5, 48000), 48000)
    vt = VirtualTime()
    chunks = run(vt, collect(make_source(wav, vt, tail_ms=300)))[0]
    assert len(chunks) == 8
    assert not any(pcm(c).any() for c in chunks[-3:])


# empty / error cases ---------------------------------------------------------

def test_empty_wav_yields_no_chunks(tmp_path):
    wav = write_wav(tmp_path / "e.wav", np.zeros(0, dtype=np.float32), 48000)
    vt = VirtualTime()
    assert run(vt, collect(make_source(wav, vt)))[0] == []


def test_missing_file_fails_loudly_and_early(tmp_path):
    with pytest.raises(AudioFileError, match="does-not-exist"):
        FileAudioSource(tmp_path / "does-not-exist.wav")


def test_corrupt_file_fails_loudly(tmp_path):
    bad = tmp_path / "bad.wav"
    bad.write_bytes(b"this is not audio")
    with pytest.raises(AudioFileError):
        FileAudioSource(bad)


def test_bad_arguments_rejected(tmp_path):
    wav = write_wav(tmp_path / "c.wav", tone(0.2, 48000), 48000)
    with pytest.raises(ValueError):
        FileAudioSource(wav, speed=0)
    with pytest.raises(ValueError):
        FileAudioSource(wav, start_ms=-1)
    with pytest.raises(ValueError):
        FileAudioSource(wav, start_ms=10_000_000)


# ---------------------------------------------------------------- ScriptedKeys

def test_scripted_keys_fire_on_the_audio_clock_within_10ms(tmp_path):
    wav = write_wav(tmp_path / "c.wav", tone(10.0, 48000), 48000)
    vt = VirtualTime()
    src = make_source(wav, vt)
    keys = ScriptedKeys([(500, "ptt_down"), (3300, "ptt_up"), (9000, "stop_hotkey")], clock=src.clock)
    seen = []
    keys.subscribe(lambda e: seen.append((src.clock.now_ms(), e.kind, e.name)))
    run(vt, collect(src), keys.run())
    assert [(k, n) for _, k, n in seen] == [("down", "ptt"), ("up", "ptt"), ("hotkey", "stop")]
    for (actual, _, _), target in zip(seen, [500, 3300, 9000]):
        assert abs(actual - target) <= 10
    # the script also reports what it fired and when
    assert [(round(t), ev) for t, ev in keys.fired] == [(500, "ptt_down"), (3300, "ptt_up"), (9000, "stop_hotkey")]


def test_scripted_keys_sorts_events_and_same_time_events_keep_order(tmp_path):
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    keys = ScriptedKeys([(200, "stop_hotkey"), (100, "ptt_down"), (200, "ptt_up")], clock=clock)
    run(vt, keys.run())
    assert [ev for _, ev in keys.fired] == ["ptt_down", "stop_hotkey", "ptt_up"]


def test_scripted_keys_empty_script_finishes_immediately():
    vt = VirtualTime()
    keys = ScriptedKeys([], clock=StreamClock(time_fn=vt.now, sleep=vt.sleep))
    run(vt, keys.run())
    assert keys.fired == []


def test_scripted_keys_rejects_bad_events():
    clock = StreamClock()
    with pytest.raises(ValueError, match="unknown"):
        ScriptedKeys([(100, "ptt_sideways")], clock=clock)
    with pytest.raises(ValueError):
        ScriptedKeys([(-5, "ptt_down")], clock=clock)


def test_scripted_keys_load_from_json_both_shapes(tmp_path):
    clock = StreamClock()
    a = tmp_path / "a.keys.json"
    a.write_text(json.dumps([[500, "ptt_down"], [3300, "ptt_up"]]))
    b = tmp_path / "b.keys.json"
    b.write_text(json.dumps([{"t_ms": 500, "event": "ptt_down"}, {"t_ms": 9000, "event": "stop_hotkey"}]))
    assert ScriptedKeys.from_json(a, clock).events == [(500, "ptt_down"), (3300, "ptt_up")]
    assert ScriptedKeys.from_json(b, clock).events == [(500, "ptt_down"), (9000, "stop_hotkey")]
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError):
        ScriptedKeys.from_json(bad, clock)
    with pytest.raises(ValueError):
        ScriptedKeys.from_json(tmp_path / "missing.json", clock)


def test_stop_hotkey_still_fires_while_ptt_is_held():
    """Safety: stop must work in every state, including while push-to-talk is down."""
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    keys = ScriptedKeys([(100, "ptt_down"), (400, "stop_hotkey"), (900, "ptt_up")], clock=clock)
    seen = []
    keys.subscribe(lambda e: seen.append((e.kind, e.name)))
    run(vt, keys.run())
    assert ("hotkey", "stop") in seen
    assert seen.index(("hotkey", "stop")) == 1


def test_a_failing_subscriber_is_not_swallowed():
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    keys = ScriptedKeys([(100, "ptt_down")], clock=clock)

    def boom(_):
        raise RuntimeError("handler bug")

    keys.subscribe(boom)
    with pytest.raises(RuntimeError, match="handler bug"):
        run(vt, keys.run())


# ---------------------------------------------------------------- MarkWriter

def test_marks_file_is_jsonl_with_spec_names(tmp_path):
    expected = {
        "key_down", "key_up", "speech_end_label", "utterance_commit", "first_audio_delta_in",
        "first_sample_out", "tool_start", "tool_end", "stop_requested", "audio_silent",
        "browser_halted", "confirm_readback_start", "confirm_readback_end", "confirm_heard",
        "action_executed",
    }
    assert set(MARK_NAMES) == expected
    t = [0.0]
    path = tmp_path / "run" / "marks.jsonl"  # parent directory is created for the caller
    with MarkWriter(path, now_ms=lambda: t[0]) as mw:
        t[0] = 12.5
        mw.mark("key_down")
        t[0] = 40.0
        mw.mark("key_up", key="ptt")
        mw.mark("speech_end_label", t_ms=3300.0)  # explicit time, e.g. from a clip label
    lines = path.read_text().splitlines()
    rows = [json.loads(line) for line in lines]
    assert [r["name"] for r in rows] == ["key_down", "key_up", "speech_end_label"]
    assert [r["t_ms"] for r in rows] == [12.5, 40.0, 3300.0]
    assert rows[1]["key"] == "ptt"
    assert read_marks(path) == rows


def test_unknown_mark_name_is_rejected_not_silently_written(tmp_path):
    with MarkWriter(tmp_path / "m.jsonl", now_ms=lambda: 0.0) as mw:
        with pytest.raises(ValueError, match="unknown mark"):
            mw.mark("first_sample_ot")  # typo must fail loudly
    assert (tmp_path / "m.jsonl").read_text() == ""


def test_marks_survive_a_crash_because_each_line_is_flushed(tmp_path):
    path = tmp_path / "m.jsonl"
    mw = MarkWriter(path, now_ms=lambda: 1.0)
    mw.mark("key_down")
    # not closed, not flushed by us: the line must already be on disk
    assert json.loads(path.read_text().splitlines()[0])["name"] == "key_down"
    mw.close()
    with pytest.raises(ValueError):
        mw.mark("key_up")  # writing after close fails loudly


def test_marks_are_monotonic_by_default_clock(tmp_path):
    with MarkWriter(tmp_path / "m.jsonl") as mw:
        for _ in range(50):
            mw.mark("tool_start")
    ts = [r["t_ms"] for r in read_marks(tmp_path / "m.jsonl")]
    assert ts == sorted(ts)


# ---------------------------------------------------------------- CaptureSink

def play_chunks_into_sink(chunks, clock, sink):
    """Navi 'speaking': chunk k is written at k * 100 ms on the shared clock."""

    async def play():
        clock.start()
        for k, c in enumerate(chunks):
            await clock.sleep_until(k * CHUNK_MS)
            sink.write(c)

    return play()


def test_capture_sink_marks_first_nonsilent_sample_within_one_chunk(tmp_path):
    onset_s = 1.234
    clip = onset_clip(tmp_path / "clip.wav", onset_s=onset_s)
    vt0 = VirtualTime()
    chunks = run(vt0, collect(make_source(clip, vt0)))[0]
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    out = tmp_path / "out" / "reply.wav"
    marks = tmp_path / "out" / "marks.jsonl"
    with MarkWriter(marks, now_ms=clock.now_ms) as mw:
        sink = CaptureSink(out, mw, clock=clock)
        run(vt, play_chunks_into_sink(chunks, clock, sink))
        sink.close()
    rows = [r for r in read_marks(marks) if r["name"] == "first_sample_out"]
    assert len(rows) == 1
    # spec: within one chunk (100 ms) of the real onset; the synthetic clock does much better
    assert abs(rows[0]["t_ms"] - onset_s * 1000) <= CHUNK_MS
    assert abs(rows[0]["t_ms"] - onset_s * 1000) <= 5
    # and the WAV really contains the audio, at the same clock: onset sample matches
    data, sr = sf.read(str(out), dtype="int16")
    assert sr == SAMPLE_RATE
    first = int(np.argmax(np.abs(data) > 256))
    assert abs(first / SAMPLE_RATE * 1000 - onset_s * 1000) <= 5


def test_capture_sink_wav_is_time_aligned_with_gaps_as_silence(tmp_path):
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    out = tmp_path / "gap.wav"
    sink = CaptureSink(out, None, clock=clock)
    loud = (np.ones(CHUNK_SAMPLES) * 8000).astype("<i2").tobytes()

    async def go():
        clock.start()
        await vt.sleep(1.0)  # one second of nothing, then audio
        sink.write(loud)
        await vt.sleep(0.5)
        sink.write(loud)

    run(vt, go())
    sink.close()
    data, _ = sf.read(str(out), dtype="int16")
    first = int(np.argmax(np.abs(data) > 256))
    assert abs(first - SAMPLE_RATE) <= 10
    # second chunk starts 500 ms later on the clock, i.e. 1.5 s into the file
    assert abs(data[int(1.55 * SAMPLE_RATE)]) > 256
    assert not data[int(1.2 * SAMPLE_RATE): int(1.4 * SAMPLE_RATE)].any()


def test_burst_written_audio_is_laid_out_back_to_back_not_overlapped(tmp_path):
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    out = tmp_path / "burst.wav"
    sink = CaptureSink(out, None, clock=clock)
    clock.start()
    ramp = [np.full(CHUNK_SAMPLES, v, dtype="<i2").tobytes() for v in (1000, 2000, 3000)]
    for c in ramp:  # all written at t=0, faster than real time
        sink.write(c)
    sink.close()
    data, _ = sf.read(str(out), dtype="int16")
    assert len(data) == 3 * CHUNK_SAMPLES
    assert data[0] == 1000 and data[CHUNK_SAMPLES] == 2000 and data[2 * CHUNK_SAMPLES] == 3000


def test_flush_drops_unplayed_audio_and_marks_audio_silent(tmp_path):
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    out = tmp_path / "flush.wav"
    marks = tmp_path / "marks.jsonl"
    mw = MarkWriter(marks, now_ms=clock.now_ms)
    sink = CaptureSink(out, mw, clock=clock)
    loud = np.full(CHUNK_SAMPLES, 8000, dtype="<i2").tobytes()

    async def go():
        clock.start()
        for _ in range(10):  # one second of speech queued at t=0
            sink.write(loud)
        await vt.sleep(0.25)  # 250 ms have actually played
        sink.flush()

    run(vt, go())
    sink.close()
    mw.close()
    data, _ = sf.read(str(out), dtype="int16")
    assert abs(len(data) - 0.25 * SAMPLE_RATE) <= CHUNK_SAMPLES // 10  # trimmed to what played
    rows = {r["name"]: r for r in read_marks(marks)}
    assert abs(rows["audio_silent"]["t_ms"] - 250.0) <= 10


def test_first_sample_out_rearms_after_a_silent_gap_and_after_flush(tmp_path):
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    marks = tmp_path / "marks.jsonl"
    mw = MarkWriter(marks, now_ms=clock.now_ms)
    sink = CaptureSink(tmp_path / "r.wav", mw, clock=clock)
    loud = np.full(CHUNK_SAMPLES, 8000, dtype="<i2").tobytes()

    async def go():
        clock.start()
        sink.write(loud)  # reply 1 at 0 ms
        sink.write(loud)  # same reply: no second mark
        await vt.sleep(3.0)  # long silence: next reply is a new one
        sink.write(loud)
        await vt.sleep(0.05)
        sink.flush()  # stop
        await vt.sleep(0.1)
        sink.write(loud)  # new reply right after a stop must be marked again

    run(vt, go())
    sink.close()
    mw.close()
    names = [r["name"] for r in read_marks(marks)]
    assert names.count("first_sample_out") == 3


def test_capture_sink_all_silence_and_no_writes_make_no_mark(tmp_path):
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    marks = tmp_path / "marks.jsonl"
    mw = MarkWriter(marks, now_ms=clock.now_ms)
    sink = CaptureSink(tmp_path / "s.wav", mw, clock=clock)
    clock.start()
    for _ in range(5):
        sink.write(bytes(CHUNK_BYTES))  # digital silence
    near_silent = (np.random.default_rng(0).integers(-20, 20, CHUNK_SAMPLES)).astype("<i2").tobytes()
    sink.write(near_silent)  # background hiss under the threshold
    sink.close()
    mw.close()
    assert read_marks(marks) == []
    # a sink that never got a single write still leaves a valid, empty WAV
    sink2 = CaptureSink(tmp_path / "empty.wav", None, clock=clock)
    sink2.close()
    with wave.open(str(tmp_path / "empty.wav")) as w:
        assert w.getnframes() == 0 and w.getframerate() == SAMPLE_RATE and w.getnchannels() == 1


def test_capture_sink_rejects_bad_input_and_use_after_close(tmp_path):
    clock = StreamClock()
    sink = CaptureSink(tmp_path / "b.wav", None, clock=clock)
    with pytest.raises(ValueError):
        sink.write(b"\x00\x01\x02")  # odd number of bytes is not 16-bit PCM
    with pytest.raises(TypeError):
        sink.write("not bytes")  # type: ignore[arg-type]
    sink.close()
    sink.close()  # closing twice is harmless
    with pytest.raises(ValueError):
        sink.write(bytes(CHUNK_BYTES))


def test_playing_and_stopping_at_the_same_instant_is_consistent(tmp_path):
    """Two things at once: audio arrives in the same tick as the stop flush."""
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    marks = tmp_path / "m.jsonl"
    mw = MarkWriter(marks, now_ms=clock.now_ms)
    sink = CaptureSink(tmp_path / "r.wav", mw, clock=clock)
    keys = ScriptedKeys([(200, "stop_hotkey")], clock=clock)
    loud = np.full(CHUNK_SAMPLES, 8000, dtype="<i2").tobytes()
    keys.subscribe(lambda e: sink.flush() if e.name == "stop" else None)

    async def speak():
        clock.start()
        for _ in range(10):
            sink.write(loud)
            await vt.sleep(0.1)

    run(vt, speak(), keys.run())
    sink.close()
    mw.close()
    data, _ = sf.read(str(tmp_path / "r.wav"), dtype="int16")
    names = [r["name"] for r in read_marks(marks)]
    assert "audio_silent" in names
    # nothing the sink was handed after the stop is lost or duplicated: file is finite and aligned
    assert len(data) >= int(0.2 * SAMPLE_RATE) - 10


# ---------------------------------------------------------------- StreamClock

def test_clock_is_zero_before_start_and_start_is_idempotent():
    vt = VirtualTime()
    c = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    assert c.now_ms() == 0.0
    c.start()
    run(vt, vt.sleep(0.3))
    assert c.now_ms() == pytest.approx(300.0, abs=1.0)
    c.start()  # must not reset
    assert c.now_ms() == pytest.approx(300.0, abs=1.0)


def test_sleep_until_in_the_past_returns_immediately():
    vt = VirtualTime()
    c = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    c.start()
    run(vt, vt.sleep(1.0))
    before = vt.now()
    run(vt, c.sleep_until(500.0))
    assert vt.now() == before


def test_capture_sink_accepts_a_marks_path_like_the_spec(tmp_path):
    vt = VirtualTime()
    clock = StreamClock(time_fn=vt.now, sleep=vt.sleep)
    marks = tmp_path / "m.jsonl"
    sink = CaptureSink(tmp_path / "r.wav", str(marks), clock=clock)
    clock.start()
    sink.write(np.full(CHUNK_SAMPLES, 8000, dtype="<i2").tobytes())
    sink.flush()
    sink.close()  # closes the marks file it opened
    names = [r["name"] for r in read_marks(marks)]
    assert names == ["first_sample_out", "audio_silent"]
