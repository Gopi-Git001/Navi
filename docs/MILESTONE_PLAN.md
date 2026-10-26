# Milestone Plan

Owner: Navi Architect. **APPROVED by Gopi on 2026-10-25, with one edit: a laptop checkpoint at the end of M2.** After approval I create one GitHub Issue per task below (goal, done = named tests or benchmarks, don't-touch list, one owner) and nothing merges to `main` without passing tests and Navi Reviewer approval.

Rule inside every milestone: Navi QA's tests and benchmark checks for that milestone land first, then features make them pass.

Owners: **QA** Navi QA, **Voice** Navi Voice, **Browser** Navi Browser, **Brain** Navi Agent Brain, **Research** Navi Research & Scraping, **Rev** Navi Reviewer, **Arch** Navi Architect.

## M0. Foundations (docs and test tooling; no product code)
Goal: everyone builds against the same plan, contract, and targets.
- Arch: merge `DECISIONS.md`, `REFERENCE_REVIEW.md`, `TECH_SPEC.md`, `TOOL_CONTRACT.json` (frozen after Gopi's OK), `BENCHMARKS.md`. 
- QA: benchmark harness skeleton, `COMMAND_SCRIPT.csv` (20 commands from the plan's 10 examples plus 10 more, with unclear-speech and confirm cases), mock X page (with X-shaped post, delete, DM requests), mock pages 2-11, clip recording plan with Gopi.
- Brain: repo layout, config, `.env.example`, session-log format.
- Rev: review checklist applied to these docs.
- **Done when:** Gopi approves the contract and targets; QA's harness runs on an empty system and reports "all fail" for every row (proves tests can fail).

## M1. Talk and answer (voice loop, no browser actions)
Goal: hold the key, speak, hear a reply fast; stop works.
- QA: tests and benchmarks A1, B1, B3, B4, C3, D9 using the voice test interface.
- Voice: `voice/` audio in/out, push-to-talk, Ctrl+Shift+Space, test interface (`io.py`, `testio.py`), timing marks.
- Brain: Realtime session, prompt v0, cancel and turn handling, session log.
- **Early check, week 1:** measure A1 on QA's machine. If the median is above 700 ms with the real model, I bring Gopi options before M2 starts.
- **Done when:** A1, B1, B3, B4, C3, D9 pass on QA's computer; on Gopi's laptop he runs the checkpoint script and reports numbers.

## M2. See and act (browser on mock pages)
Goal: Navi controls Chrome from voice on local pages, with no risky actions yet.
- QA: tests for D6, A5, A6, A7, A10, B2, C9, C10, and the mock-page suites.
- Browser: Chrome in dedicated profile, snapshot, screenshots, cursor, all browser tools in the contract, `halt()`, sensitive-field refusal.
- Brain: dispatcher with the contract tools, cancellable tool task, prompt v1.
- **Done when:** the listed rows pass; D1 ≥ 95% on the non-risky half of `COMMAND_SCRIPT.csv`. **Checkpoint:** Gopi tests Navi controlling Chrome on his own machine before M3 starts. M3 does not begin until he says so.

## M3. Safe actions and X comment (checkpoint with Gopi)
Goal: Navi can comment on X, only after the spoken word "confirm".
- QA: tests for C4-C8, C11, C12, A4 (all against mock X).
- Browser: action description, risk classes, click and key coverage, network lock.
- Brain: `agent/safety.py` confirm gate, read-back, held-action execution, window rules, disconnect clearing.
- **Done when:** C4-C12 and A4 pass on mock X. **Checkpoint:** Gopi signs into X in Navi's own Chrome profile, runs the comment task on a post he picks, and approves or blocks real posting. Nothing posts to real X before that approval.

## M4. Read, scrape, research (spoken only)
- QA: tests for A8, A9, D10, D11, plus the scrape and read-aloud mock page.
- Research: `extract_data`, `deep_research`, `research_status` with no files, untrusted-page rule.
- Brain: wire the three tools, spoken summaries.
- **Done when:** the listed rows pass; D1 ≥ 95% on the full command script.

## M5. Always-listening mode and hardening
- QA: C1, C2, A2, D3-D5, D7, D8, E1-E4 and the 1-hour mixed-audio run.
- Voice: always-listening mode with transcript gate, headphones barge-in, mute while speaking.
- Brain/Browser: fix what QA finds.
- **Done when:** every row in `BENCHMARKS.md` meets its target (or always-listening stays off by default if it misses C1 and C2; push-to-talk stays the default). Final run on Gopi's laptop; QA report and Reviewer approval attached.

## Final summary
After M5 I send Gopi what changed and exactly what to test on his laptop.

## Gopi's answers
See `docs/DECISIONS.md` entries 15-17 and the approvals recorded there. Tool contract approved as drafted (spoken only, no CSV). Name to use everywhere, including handoff skill and README: Gopi.
