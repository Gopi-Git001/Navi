# Benchmarks (DRAFT)

**Author:** Navi QA  **Date:** 2026-10-25  **Status:** Draft for Gopi and Navi Architect. No code yet.

Nothing in `docs/` defines Navi's targets, wake word, or platform yet (`BENCHMARKS.md`, `PRODUCT_PLAN.md`, `TECH_SPEC.md`, and `COMMAND_SCRIPT.csv` are all empty stubs). So this draft defines **how each number is measured**. Every target value below is a **proposed starting point** that Gopi or the Architect must confirm. Items marked **[OPEN]** need an answer before the harness can be built.

## 0. Rules that apply to every benchmark

- **Same inputs every run.** Audio comes only from files in `test_audio/` and the command list from `docs/COMMAND_SCRIPT.csv`. No live microphone.
- **Ground truth comes from sidecar labels, never from the system under test.** Each clip has a label file (see section 3) with its transcript, the sample where speech starts and ends, and whether it should trigger Navi.
- **Repeat runs.** Each command or task runs 3 times. A command counts as a success only if at least 2 of 3 runs pass, and the report also shows the raw 3-run pass rate so flaky behavior is visible.
- **Report the distribution, not just the mean.** Times are reported as p50, p95, and max. Rates are reported with their numerator and denominator (for example "3 of 120") and a 95% confidence interval (Wilson interval).
- **Zero events is not zero risk.** If no false triggers happen in T hours of audio, the report says "0 in T h (95% upper bound 3/T per hour)", not "0 per hour".
- **Record the environment** with every run: git commit of each module, OS, browser and version, driver, machine name, STT and TTS provider and model, network on or off, run date. Results from different machines are not compared with each other. **[OPEN]** Which machine is the reference machine (Gopi's laptop?).
- **Pass/fail.** Each benchmark has a target. The run fails if any target is missed, and the report names the owner module for each failure (`voice/`, `browser/`, `agent/`). I report failures to the owner and do not fix their code.
- **Comparison with the last run.** Results are stored as one JSON file per run in `benchmarks/results/`. The report shows current value, last run, change, and a regression flag (worse than the last run by more than the noise band measured from repeat runs).

## 1. Primary benchmarks

### 1.1 False-trigger rate

- **What it measures.** How often Navi starts listening or acts when nobody addressed it.
- **Exact measurement.** Play the "negative" audio set (silence, TV, other people talking, coughs and other noises, near-miss words) into the voice pipeline. A false trigger is any wake or activation event, or any action request sent to the agent, during a clip whose label says `should_trigger = false`. Count events, not clips. Several events inside one 5-second window count as one.
- **Unit.** False triggers per hour of negative audio. Also reported per category (silence, TV, conversation, non-speech sounds, near-miss words) so we can see which one causes trouble.
- **How it is run.** The harness streams each clip at real time speed (1x) into the pipeline's audio input, collects the activation events with timestamps, and compares them to the labels. At least 4 hours of negative audio are needed in total; see section 3.
- **Proposed target.** At most 1 false trigger per 4 hours on the full set, and 0 triggers that lead to an action in the browser. **[OPEN]** Depends on whether Navi is wake-word or push-to-talk.
- **Companion number: missed-trigger rate.** Of the clean command clips (`should_trigger = true`), the percentage that did not activate Navi. Reported next to false triggers, because a system can look perfect on false triggers by never listening.

### 1.2 Command success rate

- **What it measures.** Whether Navi did what the command said, from audio to final browser state.
- **Exact measurement.** For each row of `COMMAND_SCRIPT.csv`, play its clips, then check the final state of a local test page against the row's `expected_action` (for example the selected dropdown value, the submitted form fields, the open tab URL). A command succeeds only if the expected final state is reached and nothing extra was changed (no additional clicks, no extra fields touched).
- **Unit.** Percent of commands that succeeded. Reported at three levels so failures can be assigned: (a) speech recognition word error rate against the label transcript, (b) the agent chose the right tool and arguments, (c) the final page state is correct.
- **How it is run.** Clip goes in through the audio harness, the real agent and browser run against the local test pages, and a checker reads the page state through the page's own event log.
- **Proposed target.** 90% or more end to end on clean clips, 80% or more on noisy, TV, and accented sets, and each of those sets is also reported on its own. **[OPEN]** Needs the 10 to 20 commands in `COMMAND_SCRIPT.csv`.

### 1.3 Wrong-click rate

- **What it measures.** How often Navi acts on the wrong element, which is worse than failing to act.
- **Exact measurement.** Every browser test page logs each click, key press, and value change with the id of the element that received it. An action is "wrong" if it lands on an element other than the one the command intended (as listed in the command's `expected_action`), including a correct element on a different page or tab. Actions on controls marked `data-risk="irreversible"` (delete, submit, buy, send) that the command did not ask for are counted separately as critical.
- **Unit.** Wrong actions divided by all click, type, and select actions Navi attempted, as a percent. Critical wrong actions are a separate count.
- **How it is run.** Run alongside 1.2 on pages that make mistakes likely: many near-identical buttons, overlapping overlays, shadow DOM, items that move during scroll, dropdowns, and pop-ups.
- **Proposed target.** 2% or less wrong-click rate, and exactly 0 critical wrong actions. Any critical wrong action fails the whole run.

### 1.4 Time to first spoken word

- **What it measures.** How long the user waits for Navi to start talking back after they stop speaking.
- **Exact measurement.** Start time is the end of the user's speech, taken from the label's `speech_end_ms` in the clip, not from the system's own voice detection. End time is the first sample of Navi's synthesized speech that is above the silence threshold in the captured output. The output is captured at the speaker output (a loopback or virtual audio device) so audio buffering delay is included. **[OPEN]** Confirm the voice module exposes an output we can capture.
- **Unit.** Milliseconds, reported as p50, p95, and max over at least 30 commands, and broken down by stage when the voice module emits timing marks (end of speech detected, text recognized, agent reply started, first audio produced).
- **How it is run.** Clean command clips played at 1x speed, with the network as it will be in normal use. A cold-start run (first command after launch) is reported separately from warm runs.
- **Proposed target.** p50 under 800 ms and p95 under 1500 ms.

### 1.5 Total task time

- **What it measures.** How long a whole task takes from the end of the command to the task being finished.
- **Exact measurement.** Start is the end of the user's speech (from the label). End is the moment the test page's log shows the final required state reached, which is checked by the page and not by Navi saying "done". Tasks that fail do not get a time. They are counted as failures in 1.2 and listed separately here.
- **Unit.** Seconds, p50 and p95, per task. Tasks are grouped as short (1 action), medium (2 to 5 actions), and long (6 or more actions or several pages).
- **How it is run.** A fixed list of tasks, each run 3 times, on the local test pages so website changes or network speed cannot move the result.
- **Proposed target.** Set per group after the first baseline run. **[OPEN]** I recommend we take the first real run as the baseline and then set targets, rather than guessing now.

### 1.6 Stop latency

- **What it measures.** How fast Navi obeys "stop".
- **Exact measurement.** Two variants. (a) **Speech stop:** while Navi is speaking, play a stop clip. Time from the start of the stop word (label `speech_start_ms`) until Navi's output is silent for good. (b) **Action stop:** while Navi is in the middle of a multi-step browser task, play a stop clip. Time from the start of the stop word until the last browser action in the page log, with a check that no new action happens afterward for 5 seconds. The end of the stop word is also logged so both "from the start of the word" and "from the end of the word" can be reported.
- **Unit.** Milliseconds, p50 and p95, over at least 30 stops per variant. Also the **stop failure rate**: percent of stops where Navi kept talking or acting more than 3 seconds later.
- **How it is run.** Stop clips are played into the pipeline at a timed offset after the task or reply starts. Variant (a) also plays Navi's own voice through the speakers into the mic path on at least some runs, to test echo.
- **Proposed target.** p95 under 500 ms from the end of the stop word, and 0 stop failures.

## 2. Secondary benchmarks (reported, not pass/fail until targets are agreed)

- **Word error rate (WER)** per clip category. Needed to explain command failures.
- **Wake-word accuracy.** Missed-trigger rate on clean clips. **[OPEN]** Wake word still undecided.
- **Confirmation handling.** For actions that need confirmation per `SAFETY_RULES.md` (currently empty), the percent where "yes" proceeds, "no" cancels, and silence cancels. Needs the safety rules first.
- **Barge-in correctness.** Navi stops talking when the user starts a new command.
- **Soak stability.** A 1-hour run of mixed audio with no crash, no frozen state, and memory growth below a limit. **[OPEN]** Limit.

## 3. Audio clips to record

### 3.1 Recording specification

- **Format.** WAV, mono, 16-bit. Record at 48 kHz and keep the originals. The harness resamples to whatever the pipeline needs, so recordings stay reusable.
- **Microphone.** Record with the actual microphone Navi will use (the laptop's built-in mic, **[OPEN]** confirm), plus 20% of the clips again on a headset mic.
- **Levels.** Peaks between -12 dBFS and -3 dBFS. No clipping. Keep a 0.5 s pad of room tone before and after speech.
- **Naming.** `<category>_<speaker>_<command-id>_<take>.wav`, for example `clean_s01_c07_t2.wav`.
- **Labels.** One sidecar `<name>.json` per clip with: `transcript`, `speech_start_ms`, `speech_end_ms`, `should_trigger`, `command_id` (links to `COMMAND_SCRIPT.csv`), `speaker_id`, `accent_group`, `environment`, `noise_type`, `noise_level_db` or SNR, `mic`. Start and end times are marked by hand, then checked by a second person.
- **Speakers.** At least 6 distinct people, mixed voices and ages, recorded with consent. Speaker ids only, no names, in the repo.
- **Scripts.** Each speaker reads from a printed script so the transcript is known in advance. Anyone who misreads re-records that take.

### 3.2 Clip list

"N commands" means rows in `COMMAND_SCRIPT.csv`. I assume 20 commands. If the final script has fewer, the counts per category scale down but the hours of negative audio stay the same.

| # | Category | What to record | Count |
|---|---|---|---|
| A | Clean commands | Quiet room, normal distance (about 50 cm). 20 commands x 3 speakers x 2 takes. | 120 clips |
| B | Noisy room | Real noise played in the room while speaking, 10 commands x 2 speakers x 3 noise types (cafe chatter, street traffic, fan or kitchen) x 2 levels (moderate, loud). | 120 clips |
| C1 | TV in background, with command | TV at normal living-room volume while speaking, 10 commands x 2 speakers x 2 kinds of content (news with talking, action movie with loud effects). | 40 clips |
| C2 | TV only, no command | TV speech and ads with no one talking to Navi. Include 10 short clips where the TV says the wake word or a command-like phrase, if the wake word is chosen. | 1 hour total, plus 10 bait clips |
| D1 | Silence and room tone | Quiet room, fan or air conditioning on, and an empty room with outdoor sound. | 1 hour total |
| D2 | People talking, not to Navi | Two or more people chatting in the room, a phone call, someone talking to a pet. | 1 hour total |
| D3 | Everyday sounds | Typing, mouse clicks, music with and without lyrics, dishes, door sounds, footsteps. | 1 hour total |
| E1 | Coughs | Coughs from 3 speakers, 10 each, at different loudness. | 30 clips |
| E2 | Other body sounds | Sneezes, throat clearing, laughs, sighs, "um" and "hmm", 10 of each. | 50 clips |
| E3 | Cough or noise next to a command | A cough right before, in the middle of, or right after a command, 20 commands. | 20 clips |
| F | Accented speech | 10 commands x 6 accent groups x 2 speakers, 1 take. Proposed groups: Indian English, US Southern, US general (as reference), British, Spanish-influenced English, East Asian-influenced English. **[OPEN]** Gopi to pick the groups that matter for real users. | 120 clips |
| G1 | Stop, clean | "Stop" and 4 other stop phrases (for example "Navi stop", "cancel", "never mind", "wait") x 3 speakers x 2 takes. | 30 clips |
| G2 | Stop in noise | The same phrases over TV or room noise. | 15 clips |
| G3 | Stop while Navi is talking | Spoken while Navi's own voice plays through the speakers, to test echo. Re-recorded once the voice is chosen. | 20 clips |
| H1 | Speech style | Fast, slow, whispered, shouted, and far away (3 m) versions of 6 commands, 2 speakers. | 60 clips |
| H2 | Messy commands | Self-corrections ("open the settings, no, the profile"), trailing off, long pauses mid-command, and commands cut off. | 20 clips |
| H3 | Sounds-alike | Phrases that sound close to the wake word or to important commands (for example near "stop" or near "delete"). These must not trigger or must not act. | 30 clips |
| I | Confirmations | "Yes", "no", "confirm", "cancel" x 3 speakers x 2 takes, plus a silence-after-prompt clip. | 25 clips |

**Totals.** About 700 command-type clips (A to I excluding the hour-long sets) and 4 hours of negative audio (C2, D1, D2, D3), which gives a false-trigger measurement that can show 0.75 per hour or better. Rough recording time is two to three sessions of a few hours with the speakers, plus the long negative recordings, which can be recorded unattended.

### 3.3 Who records what

- **Gopi, plus at least 5 other people,** record sets A, B, C1, E, F, G, H, I. Please tell me who can be a speaker.
- **Negative sets (C2, D1, D2, D3)** can be captured once with a machine recording over an ordinary evening, as long as no one reads the wake word aloud.
- **Public speech datasets** are not used for the official numbers. They may be used for early smoke tests only, and only after Gopi approves any new dependency.

## 4. Browser test pages the benchmarks need (local HTML, no code yet)

These give 1.2, 1.3, and 1.5 something fixed to act on. All pages log every event with the target element id to `window.__navi_log` and to a local endpoint, so the checker never trusts Navi's own report.

1. **Forms:** text, number, date, checkbox, radio, and submit, with look-alike labels.
2. **Dropdowns:** native select, custom dropdown, and a very long list.
3. **Pop-ups and dialogs:** alert, confirm, modal, new window, and cookie banner.
4. **Overlays:** sticky header covering a target, an overlay that appears after a delay, and a click-through trap.
5. **Shadow DOM:** open shadow roots, nested, and one inside an iframe.
6. **Infinite scroll:** a feed that loads more items, with a target item at a known position.
7. **Near-identical buttons:** a list of 20 "Delete", "Edit", and "Buy" buttons, each with a unique id, for wrong-click testing.
8. **Multi-tab flow:** a 3-page task to give the long-task timing something to measure.

## 5. One-command run and report (to build after sign-off)

- One command runs the unit tests, the browser tests, the audio benchmarks, and prints a single report: each benchmark, current value, target, pass or fail, last run, change, and the owner module for any failure.
- Fast mode (a subset of about 5 minutes) for every change, and full mode (all clips, all 4 hours of negative audio at 1x, so over 4 hours) nightly or before a release. **[OPEN]** Whether 4 hours of real-time audio is acceptable for the full run. The alternative is replaying negative audio faster than real time, which makes the false-trigger numbers less trustworthy.

## 6. Open questions for Gopi and the Architect

1. Wake word or push-to-talk? If wake word, which phrase? This changes sections 1.1 and 3.
2. Reference machine and microphone for official numbers.
3. Do the proposed targets in section 1 look right, or do you have your own? I will not lower any target without asking you.
4. The 10 to 20 commands for `COMMAND_SCRIPT.csv` (I own that file; I can draft them from the Product Plan once it exists).
5. Which accents matter most for your real users, and who can record.
6. Does the voice module give an audio input I can feed files into, and an output I can capture? I need both from `voice/` and will ask the voice owner.
7. Where does Navi run, and is the browser a clean profile or the signed-in one? This changes test page setup.
8. The skills named in my role, `benchmark-report` and `handoff`, are not available to me, so the report layout and handoff layout above are my own until I get them. Please send them if they exist.
