# Reference Review

Author: Navi Architect. Source: the 11 Python files in `reference/` on `main` (commit `612d925`), about 91 KB. Nothing was run; this is a read-through. The reference is read-only. We copy ideas and parts, then rewrite them under `voice/`, `browser/`, `agent/`, and `research/` with tests first.

## What each module does

- `main.py`: command-line entry. Builds the config, browser, text model, research manager, tool registry and voice session; reconnects after a dropped connection; has a mic level test.
- `config.py`: reads `.env` (API key, model names, voice, browser channel, profile folder, push-to-talk key).
- `voice.py`: one OpenAI Realtime websocket session. Mic and speaker streams, push-to-talk (hold a key, audio only flows while held), a gated hands-free mode (waits for the transcript, drops fillers and phantom transcripts like "Thank you."), mic muted while Navi speaks, and the tool-call loop. A turn counter abandons stale tool chains when you speak again.
- `tools.py`: the system instructions, 23 tool schemas, and the dispatcher that calls the browser, scraper and research code.
- `browser.py`: Playwright controller on a persistent profile. Numbered list of visible elements (including shadow DOM), annotated screenshots, a visible red cursor, click, type, form fill, scroll, tabs, key presses.
- `page_scripts.py`: JavaScript injected into pages: the element snapshot, cursor, number overlay, page text, tables, links, and `DESCRIBE` (label, sensitive-field and submit-button detection).
- `safety.py`: the confirmation gate. Risky button words (post, send, delete, buy...) are refused until the user says something affirmative after Navi asks.
- `research.py`: background deep research in a second tab (plan queries, search DuckDuckGo then Bing, read pages, write a cited report with the text model).
- `scrape.py`: AI extraction and table and link scraping to CSV and JSON.
- `llm.py`: small text-model wrapper that tells the model page text is untrusted data.
- `smoke_test.py`: one script against a local HTML page (form gate, password block, shadow DOM, scroll, tables). Good bones, but it is not a test suite.

## Keep

1. Realtime speech-to-speech as the core. It is the only path that can plausibly hit a 700 ms first word.
2. Numbered element snapshot plus `look` screenshots with the same numbers. This is the main defense against wrong clicks. Includes shadow DOM and covered-element filtering.
3. Every action returns fresh page state; ids are only valid for the latest state.
4. Push-to-talk, the turn counter, and deleting rejected audio from the conversation.
5. Gate in code, not in the prompt. The idea is right; the implementation needs changes (below).
6. Blocking password, card, CVV, SSN and account fields (`DESCRIBE.sensitive`) and the "page text is data" rule in the prompt and in every text-model call.
7. A dedicated persistent browser profile, which matches Gopi's decision 4.
8. The visible red cursor; it makes mistakes visible to Gopi.
9. The tool-result convention of short text starting with a status word, and the tool-dispatcher shape.
10. `smoke_test.py` scenarios, ported into real tests.

## Change

1. **Confirmation gate does not match decision 3.** `AFFIRM` accepts yes, okay, go ahead, sure, correct and more. The gate looks for those words anywhere in any utterance since the prompt, and the model, not code, speaks the read-back. New design: exact word "confirm", checked on the very next utterance, code-built read-back, code runs the action (decision 7-9).
2. **Gate misses real X posting.** It only fires on submit buttons inside a `<form>` or on labels matching the risky words, and it checks Enter but not Ctrl+Enter (which posts on X), keyboard Space on a focused button, or custom-JavaScript submits. The README admits this. X's Post button is a plain `div role=button`. Add the network-level second lock (decision 10).
3. **Stop is weak.** Stop exists only as the console `/stop` or by pressing push-to-talk. There is no global Ctrl+Shift+Space. `_run_calls` awaits each tool to the end, so a running click or scroll cannot be interrupted, only skipped on the next step. `Player.clear()` empties our buffer but not the audio device buffer. New: a global hotkey, a cancellable tool task, `browser.halt()`, and a measured audio-stop time.
4. **Hands-free mode has latency and safety gaps.** It waits for the transcript before responding (adds the full transcription time), the phantom list contains real words ("yeah", "okay"), and any 2-word chatter from a TV passes `_is_command`. It also mutes the mic while Navi speaks, so "stop" by voice cannot be heard through speakers. Keep it as mode two behind the false-trigger benchmark; likely needs a stricter gate.
5. **Files for scrape and research.** `scrape.py` and `research.py` write CSV, JSON and Markdown to `output/`. The plan says no files in v1 (decision 13). Return results as spoken text. Drop `scrape_tables` and `scrape_links`.
6. **Browser is Edge by default** and the launch adds `--disable-blink-features=AutomationControlled`. Gopi wants Chrome; the flag hides automation and needs his decision (decision 14).
7. **`voice.py` mixes jobs.** Audio device handling, key handling, mode logic, the Realtime protocol, and the tool loop are in one 17 KB file. Split into `voice/` (audio, keys, modes, stop, timing marks) and `agent/` (session, tool loop, gate, prompts) so different bots can own them.
8. **No timing marks or session log.** We cannot measure time to first word, stop latency, or task time. Add monotonic timestamps at key-up, first audio delta in, first sample out, tool start and end, stop requested and done, written to a local JSONL log with no secrets.
9. **Test hooks missing.** The mic, speaker, and push-to-talk key are hard-wired to real devices. Add swappable audio source, sink, and key injector (see Voice test interface in `docs/TECH_SPEC.md`).
10. **Sensitive-field check relies on names.** It misses fields with unhelpful names; keep the list but also block by `autocomplete`, input type, and nearby label, and test on the password/card mock page. Also never read the typed value back aloud.
11. **Read-back needs real content.** Add a browser function that returns the target, verb, and exact text of what a gated click would do (the comment text, the form values, the DM recipient).
12. **Model-only clarifying questions.** "Ask ONE short question" is prompt-only. Keep it, but the benchmark must measure it.
13. **Tests are one script.** Move to a real test suite with the mock X page. (A test runner such as pytest is a new dependency; Gopi to approve.)
14. **Reconnect loop restarts the whole session** and drops any held confirm. A held confirm must be cleared on disconnect.
15. **Minor.** Personal example name in README and smoke test; `.env.example` is referenced but not in `reference/`; research skips x.com so it cannot research on X; no rate limiting for X actions.

## Open questions

1. Should Navi hide that Chrome is automated (the removed flag)? Hiding it may help on sites that block bots and may break X's terms; leaving it risks challenges. Gopi decides.
2. Which model for the voice (`gpt-realtime`) and the text model (`gpt-4.1-mini`)? The 700 ms target depends on the Realtime model and Gopi's network; M1 measures it before we commit.
3. Is a pre-recorded local acknowledgement ("Okay.") acceptable if the model's first word is too slow for the 700 ms median? It counts as a spoken word, but it is a workaround Gopi should approve.
4. Does Gopi accept that with speakers and always-listening mode, "stop" by voice cannot be heard while Navi speaks (mic muted to avoid hearing itself)? Hotkey and headphones mode cover it.
5. Is a local wake-word or keyword detector allowed later? It is a new dependency, and decision 2 says no wake word.
6. What is the real X posting rate limit Navi should self-impose (number of comments per hour)?
7. Real X may change its page structure, so the mock page cannot prove everything. The M3 checkpoint on Gopi's laptop covers that.
