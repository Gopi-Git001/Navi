# Voice Risks

**Author:** Navi Voice  **Date:** 2026-10-25  **Status:** Draft v2 for Gopi and the Architect
**Based on:** `reference/voice.py` (432 lines), `reference/config.py`, `reference/main.py`, `reference/safety.py` at `main` commit `612d925`, plus `docs/PRODUCT_PLAN.md` and Gopi's decisions of 2026-10-25. I read the code; I did not run it. No number below is measured. Targets are the ones Gopi approved.

## What the reference gets right (keep)
- Push-to-talk is the default, and in that mode server-side voice detection is off (`turn_detection: None`), so audio only reaches the model while the key is held (`voice.py` lines 171-175, 275-278).
- In hands-free mode (`vad`), the server is told not to create responses itself (`create_response: False`), and the client decides after seeing the transcript (lines 171-173, 329-339).
- A local speaker flush (`Player.clear`) plus `response.cancel` on interrupt (lines 74-76, 237-243).
- Short or silent push-to-talk takes are dropped (lines 260-263). Filler phrases that the transcriber invents from silence are filtered (lines 30-34).
- A newer command makes stale tool chains stop at the next step (lines 404-406).

## Top 5 risks, ranked

### 1. Navi can act on audio that wasn't a command (hands-free mode)
**What the code does.**
- With no wake word, any transcript of two or more words counts as a command (lines 321-323). TV dialogue, other people, and Navi's own spoken sentences pass.
- With a wake word, the check is a plain substring test (line 318), so "navigation", "navigate", or "navi" inside any TV line passes when the wake word is `navi`. The wake word also counts anywhere in the sentence.
- The mic is muted only while the player is busy plus 0.6 s (lines 82, 280-281). No echo cancellation. Tail echo and room reverb after that window are heard as speech.
- The audio sent to the server skips muted stretches, so the server's detector sees spliced audio.
- `--headphones` leaves the mic open while Navi talks; there is no check that headphones are really in use.

**Fix.**
- Always-listening stays a second mode, as Gopi decided. It becomes the default only after the TV and background-talk clips give 0 false triggers.
- Replace the substring test with whole-word matching, and require the wake word at the start of the utterance. Add a minimum transcript confidence if the API gives one. Keep the filler list but add a "looks like Navi's own last sentence" check against the text Navi just spoke.
- In the second mode, require a push-to-talk press or a wake word, never plain silence-to-speech.
- Never treat anything heard while Navi is speaking as a command, except the stop path (risk 2).

**Test.** Benchmark: 0 false triggers on TV and background-talk clips; 0 unintended actions in the 1-hour mixed-audio set; clips where the TV says the wake word and says "navigate", "yes", "confirm". Unit tests on the transcript gate with a phrase table, including sound-alikes. A clarifying question counts as a pass.

### 2. Stop is not reliable, and it is not fast enough (hits the stop targets)
**What the code does.**
- There is no stop hotkey. The only stop paths are the console `/stop` line (line 301) and pressing the push-to-talk key, which interrupts as a side effect (line 248).
- Spoken "stop" has no fast path. It must be recorded, committed, transcribed, and then answered by the model (lines 329-339), so it cannot meet 500 ms to silence.
- In hands-free mode without headphones, the mic is muted while Navi speaks (line 280), so "stop" cannot be heard at all while Navi is talking. This directly breaks the stop requirement.
- `_interrupt` clears speech audio and cancels the model's response, but a browser tool call that is already running keeps running (line 409, awaiting `registry.call`). Only the next call in the chain is cancelled (lines 404-406). The "browser action halts within 1 s" target is not met by the voice side alone; it needs a cancel signal into the browser layer, which I do not own.
- The default push-to-talk key is right Ctrl (`config.py` line 58). The new stop hotkey is Ctrl+Shift+Space, which includes Ctrl. Pressing it can start a push-to-talk take at the same time.
- The player's output buffer is left at the device default, so audio can keep playing for some time after the local queue is cleared. The real delay is not measured anywhere.

**Fix.**
- A local stop handler, independent of the model and the session: hotkey (Ctrl+Shift+Space) and a local "stop" keyword check on the raw audio path, running in every mode and while the mic is muted. On stop, in this order: flush the speaker, cancel the response, truncate the conversation to what was actually played, then signal the agent to halt (an interface the Architect must define).
- Hotkey handling tracks combinations as one chord, and the push-to-talk key must not overlap with it. Changing the default push-to-talk key needs Gopi's yes, so I will propose it and ask.
- Use small output buffers and open the output stream with a low latency setting.
- The stop word is only listened for with a strict energy check while Navi speaks, so echo of Navi saying "stop" does not stop itself.

**Test.** Benchmark 1.6 (the stop benchmark): audio stops p95 at most 500 ms, measured at the speaker output; browser halt at most 1 s; 30 or more stops per variant, with Navi's own voice playing through speakers; stop while the mic is muted; stop hotkey in each mode. Test that the hotkey never starts a push-to-talk take.

### 3. Spoken confirmation can be faked or mistaken (confirm gate)
This touches the safety gate, which I do not own. I am reporting it, not changing it.
- `reference/safety.py` still accepts "yes", "yeah", "sure", "okay", "go ahead", "do it" (line 21-24), which Gopi's decision rules out. Only "confirm" should approve.
- The gate counts any affirmation said after the gate was raised, not one said after Navi finished reading back what it will do. An affirmation spoken during the read-back, or heard from the TV, can count (lines 59-69).
- The 4-second wait lets a late transcript count.
- From voice, I can help by tagging each transcript with whether Navi was speaking when it was said, and by delivering the confirm word only as a push-to-talk take in the default mode.

**Next owner:** @Navi Architect to assign the safety gate change; I will supply the "was Navi speaking" tag and the push-to-talk-only delivery.
**Test.** Confirm-gate benchmark: 0 actions before "confirm", 100% of runs; clips of "yes", "okay", "go ahead", sound-alikes, and the TV saying "confirm".

### 4. Missed, cut-off, or late commands (push-to-talk edges and lag)
**What the code does.**
- Audio is sent in 100 ms blocks and only after the key goes down (lines 275-278). There is no pre-roll, so the first syllable can be lost.
- On key release the code commits right away (line 264), but the last blocks may still be waiting in the queue to be sent (lines 270-282). The commit can arrive before them and clip the end of the command.
- The "nothing was said" check uses a fixed loudness number of 200 (line 161). A quiet mic or a whisper is dropped, a noisy room is not.
- Log timestamps are whole seconds (line 38), and there are no timing marks, so the 700 ms median and 1000 ms p95 first-word target cannot be measured at all.
- A background update (`notify`, lines 425-432) asks for a response right away, even while the user is holding the key, so Navi can start talking over the user.
- After a lost connection, `main.py` reconnects (lines 64-70) but the response flags and recording state are not reset (lines 154-160). A response that was active when the connection dropped can leave `response_active` true, and Navi would never respond again until restart.

**Fix.** Keep a short pre-roll buffer and a short tail after release; drain the send queue before committing; set the quiet threshold from a startup measurement of the room; add millisecond timing marks (key down, speech end, commit, first audio in, first audio out, stop heard, audio silent); hold background announcements while the user is speaking; reset all session state on reconnect.
**Test.** Benchmark 1.4: first spoken word median at most 700 ms, p95 at most 1000 ms, cold start and warm reported separately; clips E, F, H1, H2 (fast, slow, whispered, far away, self-corrections); a test that the first and last 200 ms of each clip reach the session; a forced disconnect during a response, then a new command works.

### 5. Windows audio devices (and no way to test without a microphone)
**What the code does.**
- Devices are always the system defaults (lines 52, 89). No selection, no change handling, no error handling around opening them. A missing, busy, or unsupported device ends the program with a raw error.
- The stream status flags in both callbacks are ignored (lines 54, 92), so dropouts and underruns are silent.
- The session sample rate of 24000 Hz is requested straight from the device. Some Windows drivers and modes do not accept that rate; the code has no fallback or resampler.
- Bluetooth headsets that switch to a call profile when the mic opens, headset plug and unplug, and sleep and resume are all unhandled.
- The mic queue is unbounded (line 86), so a slow network lets old audio pile up.
- The global key listener (`pynput`) may not receive keys while a window run as administrator has focus (a Windows rule). Hold-to-talk and the stop hotkey would then silently stop working.
- For QA's harness there is no way to feed audio files in or capture Navi's speech out. Both `Mic` and `Player` talk only to the sound card.

**Fix.** Startup self-check (mic opens, level moves, speaker plays a short sound, permission granted) with plain messages; explicit device choice remembered between runs; device-change watching and reopen; one resampler; bounded queue; a visible state (listening, thinking, speaking, muted, error); and a small input and output interface so QA can substitute a file source and a capture sink, with the real sound card as the default.
**Test.** Device matrix on Gopi's Windows laptop with the normal mic: built-in, wired, Bluetooth, USB, unplug during a session, sleep and resume, mic permission off, mic held by another app, admin-window focus for the hotkeys. The 1-hour soak with no crash and no frozen state.

## Not voice-owned, but found while reading
- `safety.py` accepts "yes" and similar words (risk 3), and has no timing rule tied to the read-back. Owner: Architect and whoever owns the safety gate.
- No bridge exists from stop to the browser layer (risk 2). Owner: Architect to define the cancel signal in the tool contract; browser to implement.

## Decisions I need
1. **Push-to-talk key.** Default is right Ctrl in the reference, which overlaps the new stop hotkey. May I propose another default? (Gopi)
2. **Session changes.** I want to keep the session format as is and add only client-side behavior. Anything beyond that, such as echo handling inside the session, I will ask first. (Gopi)
3. **New dependencies.** None needed so far. A wake-word detector or echo canceller would be one; I will ask before adding. (Gopi)
4. **Voice interface for QA.** I propose a small `voice/` interface: audio source in, audio sink out, event stream (key down, speech end, first audio, stop heard) with millisecond timestamps. (Architect to confirm in the spec; QA to use.)
5. **Cancel signal into the browser layer** for stop. (Architect)
