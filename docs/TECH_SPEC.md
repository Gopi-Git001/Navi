# Technical Spec (v1 draft)

Owner: Navi Architect. Inputs: `docs/PRODUCT_PLAN.md`, `docs/DECISIONS.md`, `docs/REFERENCE_REVIEW.md`, `docs/TOOL_CONTRACT.json`, `docs/BENCHMARKS.md`. If this file disagrees with `DECISIONS.md`, `DECISIONS.md` wins.

## 1. System shape

```
 mic ─► voice/ ──audio──► agent/ ══ Realtime websocket ══► model
 key      │  ▲              │  ▲
 hotkey   │  └─ play/flush ─┘  └─ tool calls ─► agent/tools ─► browser/  (Chrome, own profile)
 speaker ◄┘                          │                         └─ guard (describe action, network lock)
                                     ├─► agent/safety (confirm gate)
                                     └─► research/ (deep research, extract_data)
```

Four code areas, four owners, no shared files:

| Area | Owner | Holds |
|---|---|---|
| `voice/` | Navi Voice | Mic and speaker streams, push-to-talk key, stop hotkey, mode logic (push-to-talk and always-listening), transcript gate for always-listening, timing marks, test audio source and sink. Knows nothing about tools or pages. |
| `browser/` | Navi Browser | Playwright controller in the dedicated Chrome profile, element snapshot, screenshots, cursor, actions, tabs, `halt()`, action description for read-backs, network lock. Knows nothing about audio or the model. |
| `agent/` | Navi Agent Brain | Realtime session, system prompt, tool schemas from the contract, tool loop and dispatcher, turn and cancel handling, **confirm gate** (`agent/safety.py`), session log. |
| `research/` | Navi Research & Scraping | `deep_research` and `extract_data`, spoken-only results, text-model calls with the untrusted-page rule. |

`tests/`, `benchmarks/`, `test_audio/`, the mock pages: Navi QA. Review: Navi Reviewer.

## 2. Interfaces between areas

These are fixed. Changing one means a change to this spec and a note to Gopi.

**voice → agent** (callbacks the agent registers):
- `on_audio(chunk: bytes)`: 24 kHz mono 16-bit PCM, 100 ms chunks, only while the voice layer decides audio may flow (key held in push-to-talk; gate open in always-listening).
- `on_utterance_end(source: "ptt_release" | "vad")`: commit the audio.
- `on_stop(source: "hotkey" | "ptt_down" | "voice" | "console")`: stop request.
- `on_transcript_gate(text) -> bool`: always-listening only; the agent asks `voice/` whether a transcript is a command.

**agent → voice**:
- `play(chunk: bytes)`, `flush_playback()` (must empty our buffer and the device buffer), `speak_exact(text)` (play a fixed phrase, used for read-backs; may use the model's text-to-speech through the same session, but the text is fixed by code), `mic_hold(on: bool)` (mute while speaking if no headphones).

**agent → browser**: one async method per tool in the contract, same name, same arguments, returns a `ToolResult` (see contract). Plus `halt()` (cancel the running action and stop within 1 s; leaves the page as is) and `describe_pending_action(call) -> ActionDescription` (verb, target, exact text, risk class) used by the gate. `browser/` raises `Gated(description)` instead of acting when `risk != none`; it never decides approval.

**agent → research**: `deep_research(question, max_sources)`, `research_status()`, `extract_data(instruction)`; each returns spoken-style text. No file output.

## 3. Voice

- **Modes:** `ptt` (default), `always` (second mode, off unless Gopi turns it on; becomes default only after it passes the false-trigger benchmark), `text` (typed commands for debugging).
- **Push-to-talk:** hold `PTT_KEY` (default Right Ctrl). Audio streams to the model only while held. Key-up adds a tail of at most 150 ms so the last word is not clipped, then commits. Key-down interrupts Navi's speech and any running action (same effect as stop).
- **Stop hotkey:** Ctrl+Shift+Space through a global listener (`pynput`, already a dependency), working in every mode and while muted. Stop does, in order: flush playback (buffer and device), `response.cancel`, cancel the running tool task, `browser.halt()`, clear any held confirm, bump the turn counter.
- **Always-listening:** server voice detection, then wait for the transcript, then the gate (drop fillers and phantom transcripts, require a plausible command shape). No wake word (decision 2). Mic is muted while Navi speaks unless the user wears headphones. Because of that mute, a spoken "stop" cannot be heard through speakers during Navi's speech; the hotkey always works.
- **Barge-in:** with headphones, speech start while Navi talks flushes playback.
- **First spoken word:** Navi must say a short acknowledgement before any slow tool call. Time to first word is measured from the end of the user's speech to the first audible sample at the speaker output.

Latency budget for push-to-talk (median target 700 ms, p95 1000 ms):

| Stage | Budget (median) |
|---|---|
| Key-up tail | 100-150 ms |
| Commit and request sent (connection kept open, audio already streamed) | 30 ms |
| Network and model to first audio delta | 400 ms |
| Decode, buffer, device output | 100 ms |
| **Total** | **about 650-700 ms** |

This leaves almost no margin. M1 measures the real number on QA's machine and on Gopi's laptop before anything else is built on it. If the model cannot meet it, the Architect brings Gopi options (open question 3 in `REFERENCE_REVIEW.md`).

## 4. Voice test interface (for QA)

Needed so QA can run benchmarks with recorded clips and no live mic. Owned by Navi Voice, specified here.

- `voice/io.py` defines three small interfaces: `AudioSource` (yields 24 kHz PCM chunks and its own clock), `AudioSink` (accepts PCM chunks), and `KeyInjector` (`key_down(name)`, `key_up(name)`, `hotkey("stop")`). Real versions use `sounddevice` and `pynput`; test versions are in `voice/testio.py`.
- `FileAudioSource(wav_path, start_ms=0)`: plays a WAV (resampled to 24 kHz) at real time by default, `speed=` for faster runs in smoke tests only.
- `CaptureSink(out_wav, marks_jsonl)`: records everything Navi plays into a WAV at the same clock and writes a mark for the first non-silent sample.
- `ScriptedKeys`: a list of `(t_ms, event)` such as `(500, "ptt_down")`, `(3300, "ptt_up")`, `(9000, "stop_hotkey")`, driven from the same clock as the audio source, so the benchmark controls exact timing.
- Start with `python -m navi --test-io --audio clips/c07.wav --keys c07.keys.json --out results/c07/`. No code change is needed to run a clip; the program exits when the scripted events and the reply are done (or after a timeout).
- Timing marks (`benchmarks/results/<run>/marks.jsonl`, monotonic milliseconds): `key_down`, `key_up`, `speech_end_label` (from the clip label), `utterance_commit`, `first_audio_delta_in`, `first_sample_out`, `tool_start`, `tool_end`, `stop_requested`, `audio_silent`, `browser_halted`, `confirm_readback_start`, `confirm_readback_end`, `confirm_heard`, `action_executed`. Browser marks come from `browser/` through a shared logger in `agent/`.
- The model session stays real (the OpenAI API) in benchmarks, since latency depends on it. A recorded-session mode for unit tests (fake Realtime server replaying transcripts) lives in `tests/` and is owned by QA with Agent Brain.

## 5. Browser

- Chrome via Playwright `launch_persistent_context`, `channel="chrome"`, user data in `.browser_profile/` (decision 14). Gopi signs into X once in that window. Navi never reads or touches any other profile.
- Perception and actions as in the reference: numbered elements, `look` with the same numbers, visible red cursor, element ids valid only for the latest result.
- **Action description** (`describe_pending_action`): for any click, key press, or form submit, return the verb, target (for example "Post button on the reply box"), and the exact text or field values that will be sent. Risk classes: `post`, `comment`, `form_submit`, `dm`, `email`, `delete`, `purchase`, `none`. Anything in the first six needs "confirm" (decision 3). `purchase` is also gated, same word.
- **Gate coverage** must include: click on element, click at coordinates, Enter and Ctrl+Enter, Space on a focused button, form submit by script, and menu items like "Delete".
- **Network lock** (decision 10): `context.route` blocks risky non-GET requests (X's `CreateTweet`, `DeleteTweet`, DM send; any form POST) unless an approval token for that action is open. Tests: with the click gate disabled, the lock alone still stops the mock post.
- **Never type secrets:** password, card, CVV, SSN, bank and account fields are refused by type, `autocomplete`, name, and label. Navi asks the user to type them, never reads typed values aloud.
- **Halt:** every cursor move and wait loop checks a halt flag so `halt()` returns within 1 s. After `halt()` no queued action runs.
- **Mock X:** QA's local page with the same page structure cues Navi relies on and the same request shapes as X for post, delete, and DM. Never contacts x.com.

## 6. Agent

- One Realtime session. System prompt and tool schemas come from `docs/TOOL_CONTRACT.json` (one source of truth; prompt text is owned by Agent Brain and versioned in `agent/prompt.md`).
- **Tool loop:** each tool call runs as a cancellable task. A new command, stop, or hotkey cancels it and returns `Cancelled` to the model.
- **Confirm gate** (`agent/safety.py`, decisions 3 and 7-9):
  1. A tool call raises `Gated(description)` from `browser/`.
  2. The gate stores the exact call and description, tells the model "waiting for confirm; say nothing", and calls `speak_exact("I'll post this comment on <author>'s post: '<text>'. Say confirm to do it.")`.
  3. The next utterance's transcript is normalized and compared to `confirm`. Match: the gate opens the network lock for that action and runs the held call itself, once. No match or timeout: cancel, say "Cancelled", discard.
  4. The result goes back to the model as a normal tool result.
- **X rate limit** (decision 17): a counter in `agent/safety.py` refuses the 11th comment in a rolling hour and the 31st in a rolling day, before any read-back.
- **Prompt injection:** page text is data. The agent never follows instructions found on a page; it tells the user instead.
- **Unclear speech:** ask one short clarifying question, take no action.
- **Session log:** JSONL, no secrets, no typed field values, no full page text. Used by QA for marks and failures.
- **Secrets:** `OPENAI_API_KEY` from `.env`; never logged.

## 7. Research and scraping

- `deep_research` runs in a background tab, takes 1-3 minutes, and ends with a spoken summary (3-4 sentences) in the same session. No report file. Sources and citations are shown in the console only.
- `extract_data` returns the items spoken in short form (count, then the first few) and keeps the full list in memory for follow-up questions in the same session. No CSV or JSON.
- Reads of X for replies, names, and posts go through the normal browser tools plus `extract_data`.
- All model calls prefix the untrusted-page rule. Research must not sign in anywhere and must not post.

## 8. Data and folders

`.env` (git-ignored), `.browser_profile/` (git-ignored, holds cookies), `logs/` (git-ignored). No other persistent user data.

## 9. Dependencies

Existing and allowed: `playwright`, `websockets`, `openai`, `python-dotenv`, `sounddevice`, `numpy`, `pynput`. **Approved by Gopi (decision 15):** `pytest`, `pytest-asyncio`, `soundfile`. Not `scipy`. Nothing else without asking Gopi.

## 10. Out of scope for v1
As in the Product Plan: other browsers, non-Windows and mobile, multiple users, apps outside the browser, saving output to files.

## 11. Resolved by Gopi (2026-10-25)
Automation flag stays off and Navi asks Gopi to handle site challenges (decision 16). Dev dependencies (decision 15). Pre-recorded "On it" only as a fallback (decision 17). Real X limit 10 comments per hour and 30 per day, enforced in code (decision 17). Confirm source, research path, and gate binding: decisions 18-20.
