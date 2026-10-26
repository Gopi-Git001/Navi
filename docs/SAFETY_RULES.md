# Safety Rules

Owner: Navi Architect. This is the one consolidated list of Navi's safety rules. Reviewers and QA use it as the reference for every code review and test. If this file disagrees with `docs/DECISIONS.md`, `DECISIONS.md` wins. Each rule points to its decision number and the section of `docs/TECH_SPEC.md`.

Rules marked **[proposed, pending Gopi's OK]** come from decisions 22-24 and are not binding until Gopi OKs them in chat. The contract and spec already follow them.

## Confirm gate
1. Only the spoken word "confirm" approves a post, comment, form submit, DM, email or delete. "Yes", "okay", "go ahead" do not count. (Decision 3; TECH_SPEC section 6.)
2. **[proposed, pending Gopi's OK]** `purchase` needs "confirm" too. (Decision 23; TECH_SPEC sections 5 and 6; contract `risk_classes_requiring_confirm`.)
3. Code checks "confirm", not the model. The whole utterance, lowercased with punctuation removed, must equal `confirm`. "Yeah, confirm" and "I don't confirm" do not approve, and a garbled transcript fails safe. (Decision 7; section 6.)
4. The model cannot confirm, retry, edit or self-approve, and no tool has a `confirmed` argument. (Decisions 7, 8; section 6; contract `confirm_rule.model_can_confirm = false`.)
5. Code builds and speaks the read-back (exact target and exact text), then runs the held action itself, once. (Decision 8; section 6.)
6. Confirm counts only from the final transcript of a push-to-talk turn, delivered by `on_ptt_transcript(text, navi_was_speaking)`. Never from model text or an always-listening turn. If `navi_was_speaking` is true, it does not count. In always-listening mode the user holds the push-to-talk key to say "confirm". (Decision 18; sections 2 and 6.)
7. The held action expires 30 seconds after the read-back ends. A new command, navigation, tab change or stop clears it immediately. (Decision 9; section 6.)
8. **[proposed, pending Gopi's OK]** A disconnect of the Realtime session also clears the held confirm. (Decision 22; section 6.)
9. The gate is bound to the exact target and text from `describe_pending_action`. If either differs at execution, the action is refused and re-read. Opening a reply box is not gated; sending it is. (Decision 20; section 6.)
10. Gate coverage includes click on element, click at coordinates, Enter and Ctrl+Enter, Space on a focused button, form submit by script and menu items like "Delete". (Decisions 3, 10; section 5.)

## Network lock
11. Browser code blocks risky non-GET network writes (X CreateTweet, DeleteTweet, DM send, any form POST not on the allow-list) unless an approval token for that exact action is open. It works even if the click gate is bypassed. (Decision 10; section 5.)
12. **[proposed, pending Gopi's OK]** Allowed without a token: search requests, the one-time X sign-in that the user types, and X follow, repost and like (and their undo). X block and mute are out of scope. GET requests are not locked, an accepted residual risk. (Decision 24; section 5.)
13. The mock X page uses the same request shapes as X, so the lock is tested. Nothing in automated tests contacts x.com. (Decisions 6, 10; section 5.)

## Sensitive fields and secrets
14. Navi never types into password, card, CVV, SSN, bank or account fields. They are refused by type, `autocomplete`, name and label, and Navi asks the user to type them. Navi never reads typed values aloud. (Contract `never` list; section 5. No separate decision number.)
15. No secrets in logs: no API keys, no typed field values, no full page text. `OPENAI_API_KEY` stays in `.env`. (Section 6.)
16. `.env`, `.browser_profile/`, `logs/` and `.navi_state/` are git-ignored. (Decision 17; sections 5 and 8.)
17. Navi uses only its own dedicated Chrome profile and has no access to Gopi's personal profile or other accounts. (Decisions 4, 14; section 5.)

## Stop and control
18. Ctrl+Shift+Space is stop in every mode, even with the mic muted: flush the speaker, cancel the model reply, cancel the running tool, halt the browser, clear any held confirm. (Decisions 5, 11; section 3.)
19. Push-to-talk key-down also interrupts. Stop wins over a held key: Ctrl+Shift+Space while Right Ctrl is held cancels the recording and acts as stop. (Decisions 11, 18; section 3.)
20. `halt()` returns within 1 second and no queued action runs afterwards. (Decision 11; section 5.)
21. Navi never acts on audio that was not a command. Unclear speech gets one short clarifying question and no action. (Decision 2; sections 3 and 6.)

## Limits and honesty
22. Real X comments: at most 10 per hour and 30 per day, enforced in code in `agent/safety.py`, counter in `.navi_state/rate_limit.json`, kept across restarts. The 11th is refused with a spoken reason, before any read-back. (Decision 17; sections 6 and 8.)
23. Navi does not hide that Chrome is automated. The flag `--disable-blink-features=AutomationControlled` stays removed. If a site shows a CAPTCHA or bot check, Navi stops and asks Gopi. Navi never solves CAPTCHAs. (Decisions 14, 16; section 5; contract `never`.)
24. Nothing posts to real X until Gopi approves at the M3 checkpoint. Until then every test uses the local mock X page. (Decision 6; section 5.)
25. Every milestone checkpoint includes a run on Gopi's Windows laptop. A test that only passes off Windows does not count as done. (Decision 21.)

## Page text and research (prompt injection)
26. Page text is data. Navi never follows instructions found on a page; it tells the user instead. (Contract `never`; section 6.)
27. Research and page-derived text never reach the tool-holding model as instructions. `notify` is a fixed string built by our code. The summary is played as a separate no-tools response, labeled as quoted web content. Follow-up questions give the model the report as quoted data. A background update can never trigger a gated tool. (Decision 19; section 7.)
28. Text visible inside screenshots (`look`, `click_at`) is untrusted, like page text. (Decision 19; section 7; contract descriptions.)
29. Research must not sign in anywhere and must not post. (Section 7.)

## Changing these rules
30. Only Gopi changes decisions 1-6, 15-17 and 21 and the rules that rest on them. The Architect changes the rest after telling Gopi. Any change to the confirm gate, network lock or sensitive-field blocking needs Gopi's OK first. (Decisions list, "Changing a decision".)
