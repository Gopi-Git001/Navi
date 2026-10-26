# Browser

Browser control used by Navi to open pages and act on the web. Owner: Navi Browser.

## Files (M0, issue #10)

- `api.py`: `BrowserController` Protocol. One async method per browser-owned tool in `docs/TOOL_CONTRACT.json` (same name, same arguments), plus `halt()`, `describe_pending_action(call) -> ActionDescription`, and `approve(description)`. Shared types (`ToolResult`, `ActionDescription`, `Gated`) come from `common/types.py`.
- `fake.py`: `FakeBrowser`, an in-memory implementation for Agent Brain and QA. No Playwright. Scripted pages (`FakePage`, `FakeElement`), canned `ToolResult`s, records every call in `calls`, records risky actions that really ran in `executed`.
- `tests/unit/test_browser_api.py`: fails if a browser-owned tool is missing from the Protocol (or the fake), or has different argument names, required-ness, enum values or types.

Run: `python -m pytest` (needs `requirements-dev.txt`).

## What the browser does with risk

`browser/` never decides approval. For any call whose described risk is not `none` it raises `Gated(description)` **instead of acting**. The gate (`agent/safety.py`, Agent Brain) reads it back, waits for the spoken "confirm", then calls `approve(description)` and re-issues the held call itself, once.

- **`target` format:** always `"<page url> | <element ref>"`, optionally followed by `" | <label>"`, e.g. `https://x.test/post/123 | composer ref 14 | reply to @alice's post`. A send button reports the ref of the composer it sends. Different posts (different URL) or different composers on one page (different ref) never share a target. The gate may split on `" | "`; the URL is not meant to be spoken.
- **`approve()` rules:** it is bound to the exact `risk`, `verb`, `target` and `text`.
  - Consumed only by the next matching action; then the same action is gated again until a fresh approve.
  - Approving twice replaces the earlier approval; it never stacks two uses.
  - A different or changed action (other target, edited composer text, other key) raises `Gated` with the new description and leaves the approval alone (decision 20: refused and re-read).
  - An unused approval expires after 30 s (`approval_ttl`, injectable clock in the fake; the real browser takes the same arguments).
  - Navigation, tab change, history and `halt()` clear it.
  - Only `approve()` approves. The contract has no `confirmed` argument, so passing one to any tool is a `TypeError` and never approves. The dispatcher must not forward such an argument.
- `ActionDescription.readback` is left empty by the browser; the gate fills it in.
- Every result is a `ToolResult` whose `text` starts with the status word and carries the updated page state (title, URL, numbered elements).

## Gated and not gated

| Action | Gated? |
|---|---|
| Click on an element whose risk is not `none` (Post / Reply send button, Send DM, Submit, Buy) | **Yes**, raises `Gated` |
| `click_at` landing on such an element | **Yes** (decision 10 adds the network lock underneath) |
| Menu item "Delete" (risk `delete`) | **Yes** |
| `press_key` Enter, Ctrl+Enter, Meta/Cmd+Enter in a composer **with text** | **Yes** |
| `press_key` Space or Enter on a focused risky button | **Yes** |
| `type_text(..., press_enter=true)` into a risky composer | Types, then **Yes** for the Enter |
| Form submit by script | **Yes**, via the network lock (real browser; not simulated in the fake) |
| Opening a reply box (the Reply icon), opening a menu | No (decision 20) |
| Clicking or typing into a text box, `fill_form` (never submits), `select_option`, scroll, hover, find, tabs, history, wait, observe, look, read_page, navigate, search_web | No |
| Enter or Ctrl+Enter on an **empty** composer | No (nothing to send) |

## Gate-coverage cases (docs/TECH_SPEC.md section 5)

Each must be a test, first in the fake, then on QA's mock X page:

1. click on element
2. click at coordinates
3. Enter and Ctrl+Enter
4. Space on a focused button
5. form submit by script (network lock alone stops the mock post with the click gate disabled)
6. menu items like "Delete"

The fake covers 1 to 4 and 6. Case 5 needs the real network lock.

## Sensitive-field rules

Password, card, CVV, SSN, bank, account and ID fields are never typed into and their values never reach the model.

- Detected by `type`, `autocomplete` (all `cc-*` and other credential values, not only `cc-`), `name`, `id`, and label/aria-label text, in any frame (fields in payment-looking iframes count as sensitive; an unknown target is treated as unsafe, not safe).
- `type_text` into one returns `blocked`; Navi asks Gopi to type it himself.
- `fill_form` skips them and says so; it never submits.
- `press_key` of a printing character while a sensitive field has focus returns `blocked` (also after a `click_at` into it).
- `observe` shows `(hidden)` instead of the value.
- Typed values are never read aloud or logged.

## Page facts the gate needs

The browser supplies facts, never rules. For every possibly risky action `describe_pending_action` must report, read from the page and not from what the model said it typed:

1. the verb (click, press Enter, press Ctrl+Enter, press Space) and the key chord;
2. the element's role and its accessible name from several sources (aria-label, label, text, title, placeholder), because many X controls are icon-only;
3. whether it sits inside a dialog or composer, and whether that composer has non-empty editable content;
4. the exact composer text or field values that will be sent (read back from the page after typing);
5. the target: page URL plus a stable element ref (and a label such as which post or recipient), in the `target` format above;
6. the button or key that will actually fire;
7. the focused element, and whether it is sensitive;
8. whether it is in an iframe, and the form's action and method for form submits;
9. the page URL.

Items 1, 3, 4, 5 are in `ActionDescription` today (`verb`, `risk`, `text`, `target`). The rest are used inside the browser to compute `risk`.

## Halt

Every wait and typing loop checks a halt flag at least every 10 ms and types in chunks. `halt()` cancels the running action and every queued one, clears any approval, and returns within 1 s; the page stays as it is. Actions started after `halt()` returns run normally.
