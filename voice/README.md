# Voice

Voice capture, speech recognition, and speech synthesis for Navi (owner: Voice engineer).

## Test audio I/O (issue #9)

Run the voice layer with no microphone, speaker or keyboard. Interfaces are in `voice/io.py`
(`AudioSource`, `AudioSink`, `KeyInjector`), the file-based versions in `voice/testio.py`, and
the timing marks writer in `voice/marks.py`. They match `docs/TECH_SPEC.md` sections 2 and 4.

```python
from voice.marks import MarkWriter
from voice.testio import FileAudioSource, ScriptedKeys, CaptureSink

src = FileAudioSource("test_audio/clip.wav", tail_ms=3000)     # any WAV; becomes 24 kHz mono 16-bit, 100 ms chunks
keys = ScriptedKeys([(500, "ptt_down"), (3300, "ptt_up"), (6000, "stop_hotkey")], clock=src.clock)
with MarkWriter("output/marks.jsonl", now_ms=src.clock.now_ms) as marks:
    sink = CaptureSink("output/navi_out.wav", marks, clock=src.clock)
    # voice layer: keys.subscribe(handler); iterate src.chunks(); sink.write(...) / sink.flush()
    # run keys.run() next to the audio loop; call sink.close() at the end
```

- **One clock.** The source, keys, marks and sink share `src.clock` (milliseconds since the first
  chunk). Key events fire within 10 ms of their `t_ms`.
- **Key events:** `ptt_down`, `ptt_up` (push-to-talk key) and `stop_hotkey`. The stop hotkey fires
  whatever state push-to-talk is in. Script files are JSON: `[[500, "ptt_down"], ...]` or
  `[{"t_ms": 500, "event": "ptt_down"}]` (`ScriptedKeys.from_json`).
- **`CaptureSink`** writes a WAV on the same clock (gaps are silence; audio that arrives faster
  than real time is laid out back to back) and marks `first_sample_out` at the first audible
  sample of each reply. `flush()` drops audio that would not have played yet and marks
  `audio_silent`, so stop latency is `stop_requested` to `audio_silent`.
- **Marks** are JSONL, one `{"name", "t_ms", ...}` per line, flushed per line. Only the names in
  `voice.marks.MARK_NAMES` are accepted.
- **`speed` > 1** on `FileAudioSource` is for smoke tests only. Latency numbers from such a run
  are not real.
- **Unit tests** use `VirtualTime`, which makes sleeps free and timings exact:
  `python -m pytest tests/unit/test_voice_testio.py`.

Real `sounddevice` and `pynput` versions of these interfaces come in M1.
