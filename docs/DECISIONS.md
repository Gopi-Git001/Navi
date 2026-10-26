# Decisions

Owner: Navi Architect. Every entry is binding on all bots until Gopi changes it. Entries 1-6 are Gopi's; entries 7-14 are the Architect's and Gopi can overturn any of them.

| # | Date | Decision | By |
|---|---|---|---|
| 1 | 2026-10-25 | Design from `docs/PRODUCT_PLAN.md` plus the real reference code in `reference/` (read-only). There is no previous-attempt folder. | Gopi |
| 2 | 2026-10-25 | **Push-to-talk is the default for v1.** Always-listening (no wake word) is a second mode and becomes the default only after it passes the false-trigger benchmark (0 responses to TV and background-talk clips). Navi never acts on audio that was not a command. | Gopi |
| 3 | 2026-10-25 | **Only the spoken word "confirm" approves** a post, comment, form submit, DM, email, or delete. "Yes", "okay", "go ahead" do not count. Navi reads back exactly what it will do before asking. | Gopi |
| 4 | 2026-10-25 | **No Gopi personal Chrome profile.** Navi uses its own dedicated, persistent Chrome profile; Gopi signs into X there once. The agent has no access to his other accounts. | Gopi |
| 5 | 2026-10-25 | Benchmark targets are in `docs/BENCHMARKS.md` (time to first word, stop, X comment time, false triggers, confirm gate, wrong-click 2%). Stop hotkey is Ctrl+Shift+Space. Unclear speech answered by a clarifying question counts as a pass. | Gopi |
| 6 | 2026-10-25 | All automated tests use the local mock X page. Nothing posts to real X until Gopi approves at the M3 checkpoint. Automated benchmarks run on QA's computer with recorded clips; real-world numbers come from Gopi's Windows laptop at each milestone checkpoint. | Gopi |
| 7 | 2026-10-25 | **Confirm is checked by code, not by the model.** The confirm gate lives in `agent/` and compares the speech-to-text transcript of the very next utterance to the word "confirm" (case and punctuation ignored, nothing else in the utterance). "Yeah, confirm" or "I don't confirm" does not approve; Navi asks again. A garbled transcript fails safe. | Architect |
| 8 | 2026-10-25 | **Code speaks the read-back and runs the action.** The gate builds the read-back text from the real page (target, exact comment or field text) and plays it itself. After "confirm", code executes the held action. The model cannot retry, edit, or self-approve. The `confirmed` argument is removed from every tool. | Architect |
| 9 | 2026-10-25 | **Confirm window:** the held action expires 30 seconds after the read-back ends, and immediately on any new command, navigation, tab change, or stop. In push-to-talk the user holds the key to say "confirm". | Architect |
| 10 | 2026-10-25 | **Second lock under the gate.** Browser code blocks risky network writes (for example X's CreateTweet, DeleteTweet, and DM send requests, and any form POST) unless the gate has opened for that exact action. This covers custom-JavaScript submits, Ctrl+Enter, and coordinate clicks that the reference gate missed. The mock X page must use the same request shapes so this lock is tested. | Architect |
| 11 | 2026-10-25 | **Stop is a control event, not a tool.** Ctrl+Shift+Space works in every mode, even with the mic muted: flush speaker, cancel the model reply, cancel the running tool task, halt the browser, clear any held confirm. Push-to-talk key-down also interrupts. | Architect |
| 12 | 2026-10-25 | **Push-to-talk key default:** Right Ctrl (`ctrl_r`), as in the reference, configurable through `PTT_KEY`. | Architect |
| 13 | 2026-10-25 | **No files for v1.** The product plan puts saving scrape or research output to files out of scope. Scrape and research results are spoken (and shown in the console). The reference's CSV/JSON writing and `scrape_tables`/`scrape_links` tools are cut. | Architect |
| 14 | 2026-10-25 | **Browser is Chrome** (`BROWSER_CHANNEL=chrome`) in its own persistent profile folder (`.browser_profile/`, git-ignored), not Edge as in the reference. The reference launch flag `--disable-blink-features=AutomationControlled` is removed until Gopi decides whether Navi should hide that it is automated (see open question in `docs/TECH_SPEC.md`). | Architect |

## Changing a decision
Only Gopi can change 1-6. The Architect changes 7-14 after telling Gopi. Add a new row; never edit an old one.
