# Dottie Product Plan (formerly Navi)

Status: Confirmed by Gopi ("yes, that's it"). Owner: Planner. Next owner: Navi Architect.

## Goal

Dottie is a voice-controlled browser agent for Gopi's Chrome on Windows. He talks normally, it sees the browser, answers questions about what is on screen, and acts on websites (search, click, scroll, type, fill forms, scrape, deep research, comment on X posts). It must be fast and reliable, with X as the main site and acting on websites as the top v1 priority. Users: Gopi first, other people later.

## Features

### Must-have

- Always-listening voice input with no wake word: everything Gopi says is treated as addressed to Dottie.
- Spoken responses for every reply; first spoken response in under 1 second after he stops speaking.
- Sees the Chrome page and answers questions about what is on screen.
- Acts on websites: search, click, scroll, type, fill forms, open links/replies (X first).
- Works inside Gopi's real, already signed-in Chrome profile.
- Comment on X posts with confirmation gate.
- Confirmation gate: before any post, comment, form submit, DM, email, or delete, Dottie reads the exact action aloud and waits for the fixed phrase "confirm". A plain "yes" does not count.
- Stop control: saying "stop" or pressing a hotkey halts the current action immediately.
- Never enters passwords or payment info.
- Unclear or misheard speech gets a short spoken clarifying question, never a guess.
- Scraping: results read aloud.
- Deep research: spoken summary only.

### Nice-to-have

(None requested by Gopi for v1; the Architect may propose, Gopi decides.)

### Out of scope for v1

- Browsers other than Chrome
- Non-Windows platforms and mobile
- Multi-user accounts
- Controlling apps outside the browser
- Saving research or scrape output to files (spoken only)

## Example voice commands (no wake word)

1. "Search X for Cloudflare Workers news." -> Opens results, reads the top 3 posts aloud.
2. "Scroll down." -> Page scrolls one screen; Dottie says "Done."
3. "What is this post about?" -> Spoken one or two sentence summary.
4. "Open the first reply." -> Opens the reply and confirms aloud.
5. "Comment 'Great thread, thanks for sharing' on this post." -> Reads the comment aloud; posts only after Gopi says "confirm".
6. "Stop." -> Current action halts; Dottie confirms aloud.
7. "Scrape the names of everyone who replied to this post." -> Names read aloud.
8. "Research the latest news on open-source voice models." -> Spoken summary only, no file.
9. "Fill in this form with my name and email, then submit it." -> Fills fields, reads the submit action aloud, submits only after "confirm".
10. "Click the thing on the left." -> Unclear, so Dottie asks a short question such as "Do you mean the menu or the profile picture?"

## Success criteria (numbers)

- Task success: at least 95% of test commands succeed.
- Speed: first spoken response in under 1 second after Gopi stops speaking.
- Unintended actions: zero in 1 hour of normal use (unclear speech must produce a clarifying question instead of an action).
- Confirmation gate: 100% of post/comment/form-submit/DM/email/delete actions wait for "confirm"; 0 executed without it.
- Stop: "stop" or hotkey halts the current action immediately (target latency to be set by the Architect).

## Safety rules (what always needs the user's spoken "confirm")

- Posting or commenting, submitting forms, sending DMs or email, deleting anything: always read aloud first, wait for "confirm".
- Never enter passwords or payment info.
- "stop" or hotkey always wins and halts immediately.
- Unclear speech: ask, don't act.

## Open risks

- With no wake word, background audio (TV, other people) could be heard as commands; mitigated by clarifying questions and the confirm gate, but needs Architect/Voice design and testing.
- Under-1-second first response is demanding for a screen-reading plus speech pipeline; the Architect must set a latency budget.
- Using the real signed-in Chrome profile means mistakes affect real accounts; the confirm gate and stop control are critical.
- Automating X may hit site rules or rate limits; Research should check.
