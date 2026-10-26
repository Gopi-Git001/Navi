# Browser gaps: reference/browser.py and reference/page_scripts.py

Owner: Navi Browser. Status: analysis only, no code. Written from reading `reference/browser.py` (434 lines),
`reference/page_scripts.py` (233), `reference/safety.py`, `reference/tools.py` and `reference/config.py`
on `main` at `612d925`. Nothing here has been run yet. Statements about how X, Amazon and YouTube behave come from
general knowledge of those sites, not from testing, and are marked "verify" where they matter. Each one becomes a
test on QA's mock pages before we rely on it.

Constraints I am keeping: no per-site selectors, never type into password/card/ID fields, every action returns
page state, and the element numbering scheme (1..N in visible order, renumbered on every snapshot) stays as it is
unless Gopi approves a change. Where a fix would touch numbering or add a dependency, it is marked **ASK GOPI**.
Safety gate logic belongs to someone else; I only list where the browser layer feeds it.

## 1. What works today

- **Persistent profile.** `launch_persistent_context(user_data_dir=...)` keeps logins between runs, which fits
  Gopi's decision to use one dedicated profile he signs into once.
- **Numbered visible elements.** Links, buttons, inputs, selects, textareas, ARIA roles and `[onclick]` elements that
  are in the viewport, not hidden, and not covered at their center point. It looks inside open shadow DOM. Labels
  fall back through aria-label, label text, inner text, alt, placeholder, title, name. Values, checked and disabled
  state are included. This is layout-independent, with no site selectors.
- **Annotated screenshot** with the same numbers drawn over the page, then removed.
- **Visible cursor** (red dot) injected on every page; moves in steps, shrinks on mouse down.
- **Actions return state:** navigate, search, back/forward/reload, tabs (list/new/switch/close), click, click_at,
  hover, type, select, fill_form, scroll, find_text, press_key, wait. Missing ids give a clear "page changed" message.
- **Popups** opened by a page are followed automatically (`popup` event).
- **Typing guard.** `type_text` and `fill_form` refuse password, `cc-*` autocomplete and fields whose name/label
  matches card/CVV/SSN/routing/PIN words. Password values are never put in the snapshot.
- **Gate hooks.** `click`, `click_at` and Enter-in-a-form call the confirmation gate before acting.
- **Text and structure helpers** for scraping: `PAGE_TEXT` (main/full), `TABLES`, `LINKS`.

## 2. What will break, and how I would handle it

Severity: **High** = can cause a wrong or unsafe action, **Med** = task fails or is slow, **Low** = annoying.

### 2.1 Wrong-click risk (the 2% benchmark)

| # | Problem | Where | Sites | Sev | Fix |
|---|---------|-------|-------|-----|-----|
| 1 | **Stale ids can hit the wrong element.** Ids are stored as a `data-agent-id` attribute on the live DOM node and looked up with a selector at click time. Nothing checks that the node still means what it did when the model saw it. Pages that recycle DOM nodes (virtualized timelines, React re-renders that reuse nodes) can put a different tweet or button under the same id. A missing node is reported safely; a recycled node is not. | `_loc`, `SNAPSHOT` | X (virtualized timeline), YouTube (feeds), Amazon (live result updates) | High | Save a fingerprint per id at snapshot time (tag, label, href, rounded position, parent chain hash). Before click/type, recompute it for the resolved node. If it differs, do not act; return "page changed, here is the new state". Numbering is untouched. |
| 2 | **Page can change between the snapshot and the click** (async content, banners sliding in, layout shift). The click uses coordinates taken just before the move; the page can shift during the 40-step cursor move. | `click`, `_move` | all | Med | After the cursor move and before the mouse down, re-check that `elementFromPoint` at the click point is still the target (or inside it). If not, re-resolve once, else abort and return state. |
| 3 | **Ids live in DOM attributes.** Frameworks can strip them on re-render (safe but causes "no longer exists" churn), and sites can detect the mutation. | `SNAPSHOT` | X, YouTube | Low | Keep the same numbers but hold the element references in a JS-side list (already half there as `window.__agentEls`) and resolve through that, not an attribute. Same numbering, so no sign-off needed. |
| 4 | **Center-point occlusion test only.** An element covered everywhere except its center, or a sticky header covering part of it, passes or fails wrongly. Clicking the center of a tall element (a whole tweet card, a video tile) can hit a nested link instead of the intended target. | `SNAPSHOT` `topAt` | X, YouTube, Amazon | Med | Sample 3 to 5 points; require most to hit the element. For container elements, prefer the element with its own href/role over the wrapper. |
| 5 | **Clickable things the selector list misses:** `div`/`span` made clickable by JS listeners with no role or onclick, custom checkboxes/radios, `tabindex` elements, `[role=treeitem]`, `gridcell`, `menuitemcheckbox`, `label`, `input[type=file]`. They never get an id, so the model falls back to `click_at` from a screenshot, which is the least accurate path. | `SEL` list | X (many controls are `div[role=button]`, mostly covered), YouTube, Amazon (some widgets) | Med | Add those roles and `[tabindex]:not([tabindex="-1"])`, plus a `cursor:pointer` check limited to elements whose parent is not already pointer. Measure how many ids that adds on the QA pages so the list does not bloat. |
| 6 | **List cut off in DOM order.** `limit` (60 in `state`, 80 in `look`) stops at the first N in DOM order. On a big page the nav, sidebar and cookie links can use the whole budget and the content you want never gets an id. | `SNAPSHOT` `maxItems` | X (left nav, trends column), Amazon (mega nav, filters), YouTube (sidebar) | High for task success | Rank before cutting: elements inside `dialog`/`[role=dialog]`/`main`/`[role=main]` first, then the rest, and keep the final ids in visible order. Return a short "in dialog / in main / elsewhere" count. Needs a rule for how ids are assigned after ranking: **ASK GOPI** (it changes which number an element gets, even though the scheme stays 1..N). |

### 2.2 Iframes

`SNAPSHOT`, `PAGE_TEXT`, `DESCRIBE_AT_POINT` and `_loc` all run in the top frame only (`page.evaluate`,
`page.locator`). They do not enter iframes. Same-origin or cross-origin makes no difference today; frames are invisible.

- **Breaks:** YouTube embedded players and some comment/consent frames, Amazon checkout payment fields and some
  review/ad widgets, X login/OAuth and embedded tweets, most CAPTCHA and cookie-consent widgets.
- **Safety side effect (High):** a card number field inside a payment iframe is not recognised as sensitive, because
  the browser cannot see into the frame. `type_text` cannot target it, but `click_at` followed by `press_key` can
  type digits into it one key at a time. See 2.7.
- **Fix:** iterate `page.frames`, run the snapshot in each visible frame, and add the frame's offset to coordinates.
  Ids stay one list in visible order. Tag each element with its frame, resolve through the frame, and mark the
  element as "in iframe". Run the sensitive-field check inside each frame, and mark fields inside payment-looking
  frames (name/src contains card/pay/stripe/braintree etc.) as sensitive. Cross-origin frames work with Playwright
  frame handles but not with plain `document.querySelector`, which is why it has to be Playwright-side.
  Add a test page with a same-origin and a cross-origin iframe with a button and a fake card field.

### 2.3 Infinite scroll and lazy loading

- **No wait for new content (Med).** `scroll` scrolls, sleeps 0.5 s, then `state()` sleeps 0.6 s and waits for
  `domcontentloaded`, which already fired. On X and YouTube feeds the next batch often arrives after that, so the model
  sees an unchanged page and scrolls again, or reads half-loaded items.
- **Scroll percent is wrong on endless pages.** `maxScroll` grows as content loads, so "Scrolled 40%" has no meaning.
- **Virtualized lists drop items that scroll away (High for reading).** On X, tweets above the viewport are removed from
  the DOM (verify). `read_page` then returns only what is currently mounted, so "read the top 3 posts" can miss
  items, and ids from before the scroll are gone.
- **Scroll target (Med).** `mouse.wheel` scrolls whatever is under the cursor. Default is the viewport center, which
  can be the wrong panel on pages with inner scrollers (X reply modal, YouTube comments or description panel, Amazon
  filter sidebar). `scroll bottom` only scrolls the window, not an inner container.
- **Fix:** replace the fixed sleeps with a "quiet" wait: a `MutationObserver` that resolves after about 150 ms with no
  DOM changes, capped at about 1.5 s, plus a check that image/network activity has not just started. After a scroll,
  report "N new items loaded" (compare item identity before/after) and "reached end" when two scrolls in a row add
  nothing. For feeds, offer a `read_feed` style call that records items as they pass through the viewport with a
  stable de-dup key (link href), so reading 3 posts does not depend on what is mounted at the end. Detect the scroll
  container under the cursor (nearest ancestor with `overflow:auto|scroll` and scrollable height) and scroll that.

### 2.4 Popups, dialogs, new tabs, banners

- **Popup hijack (Med).** Every `popup` event makes the new page the active page. Right for OAuth and "open link in
  new tab", wrong for ad popups. The model is not told the active tab changed. **Fix:** report "a new tab opened:
  title/URL, now active" in the action result, keep the opener reachable via `tabs`, and auto-close popups that
  load nothing useful (about:blank for more than a few seconds is a common ad pattern).
- **Pages created without `popup` (Low/Med).** Only pages that already have `page.on("popup")` are watched. Ctrl-click or
  context-level page creation may not trigger it (verify). **Fix:** also listen on `ctx.on("page")`.
- **JavaScript dialogs (High).** There is no `page.on("dialog")` handler. Playwright dismisses dialogs by default, so a
  site's "Discard post?" or "Delete?" `confirm()` is silently answered "cancel" and the model is never told. That is
  the safe direction, but it makes tasks fail for no stated reason. **Fix:** add a handler that records the dialog
  type and text, never auto-accepts, and returns it in the next state. Accepting must go through the confirm gate.
- **Browser permission prompts** (notifications, location, mic) are Chrome UI, not page content, so the model cannot
  see them. **Fix:** set permissions explicitly on the dedicated profile (deny by default) instead of relying on
  prompts.
- **Cookie and consent banners and sign-in walls (Med).** They are ordinary elements, so they show up, but they often
  cover the page, and some live in iframes (2.2). The prompt already says not to solve bot checks. **Fix:** report
  "modal/overlay present: <label>" at the top of the state so the model deals with it first.
- **Dedicated-profile specifics.** If the user closes the browser window, `_ensure_page` raises on a dead context
  and nothing relaunches it. If a second process opens the same profile dir, launch fails. **Fix:** listen for
  `ctx.on("close")`, relaunch with the same profile, and return a clear "browser was closed, reopened" state. Add a
  lock check with a clear error.

### 2.5 Layout changes and SPA behavior

- **`state()` is time-based, not state-based (Med).** Single-page apps (X, YouTube) change the page without a load event,
  so 0.6 s of sleeping can capture a half-rendered view, an empty list, or a skeleton screen. Every action also
  pays at least 0.6 s plus a full snapshot, which counts against the 25 s X comment task and 1 s stop targets.
  **Fix:** same MutationObserver quiet-wait as 2.3, skipping the wait entirely for actions that cannot change the page
  (move_cursor, hover without effect). I will measure the per-action overhead on the mock page before and after.
- **Snapshot cost (Med, unmeasured).** The snapshot walks every node (`querySelectorAll('*')` for shadow roots), then
  calls `getComputedStyle`, `getBoundingClientRect` and `elementFromPoint` on every candidate. On pages with tens of
  thousands of nodes (YouTube home, Amazon search) this may take hundreds of ms per call. **Fix:** measure first. If
  it is slow, find shadow hosts with a `TreeWalker` only on custom elements (tag contains `-`), batch layout reads,
  and skip off-screen subtrees early.
- **Site redesigns.** The element finder uses roles, labels and geometry only, so a redesign usually changes which
  labels appear, not whether it works. The weak spot is that the confirm gate and sensitive-field detection are
  keyword based (2.7), so a renamed or non-English label changes safety behavior silently.

### 2.6 Typing, forms and keyboard

- **Rich text editors (Med, verify).** The X compose box and most comment boxes are `contenteditable` editors
  (Draft.js/Lexical style). `loc.fill("")` for clearing and `keyboard.type` for entering are usually fine, but a
  clear that bypasses the editor's own state can leave stale text or an enabled Post button on empty text. **Fix:**
  clear with select-all and delete via real key events, then type, then read the box back and compare to the intended
  text before reporting success. The same read-back is what the "read it out before confirm" step needs (2.7).
- **Typing speed (Low/Med).** `keyboard.type(delay=15)` costs about 15 ms per character, so a 280-character comment is
  over 4 s. Use `insert_text` for long text and keep per-key typing for short fields where sites need key events.
- **Native dropdowns only.** `select_option` handles `<select>`. Custom dropdowns need click-open, observe, click-option,
  which the error message already says. Fine for now; add a combined "choose from custom dropdown" helper only if QA
  shows it is a real source of failures.
- **No file upload, drag and drop, or key-hold** tools. Not in the must-haves; noted so nobody assumes them.

### 2.7 Safety touch points in the browser layer

The gate logic is not mine and I will not change it. These are places where the browser layer decides what the gate
gets to see, which are mine to fix, or to flag.

1. **Ungated Enter and chord submits (High).** `press_key` only checks plain Enter when the focused element is inside a
   `<form>` (and not a textarea). Many sites send on Enter or Ctrl/Cmd+Enter with no form: X reply and DM boxes,
   chat boxes, Gmail-style composers. Those go straight through. **Fix (browser side):** describe the focused element
   for every Enter / Ctrl+Enter / Meta+Enter and pass a "possible send" description to the gate, whether or not a
   form exists. The gate decides what counts as risky.
2. **Gate keys on label words (Med/High).** `is_risky` matches English words in the button label. Icon-only buttons
   with no label, non-English UI, or a changed label ("Post" becomes "Send") are not caught. Also the X "Reply"
   icon opens a composer (not a send), and the real send button in the composer is a `div` with role=button, not a
   submit button. **Fix (browser side):** give the gate better facts, not rules: the element's role, whether it is
   inside a dialog/composer with non-empty editable content, the composer text, the target (which tweet), and its
   accessible name from several sources. Rule changes stay with the owner of `safety.py`.
3. **Read-back needs the exact text and target (High for the confirm decision).** The gate message today is generic. For
   Gopi's rule that Navi reads back exactly what it will do, the browser must expose: the text in the composer
   (read from the page, not from what the model says it typed), the post being replied to, and the button that
   will be pressed. **Fix:** add a `describe_pending_action` result that comes from the page and is attached to
   `CONFIRMATION_REQUIRED`. Needs a tool-contract change, so Architect decides.
4. **Sensitive values can reach the model (High).** The snapshot hides values only for `type=password`. A card or ID
   field with `type=text`, such as `name=cardnumber`, has its typed value put in the snapshot and so into the model's
   context. **Fix:** use the same sensitivity test as `DESCRIBE` inside `SNAPSHOT`, and replace the value with
   "(hidden)". Also extend the keyword list for the plan's "ID fields" (passport, driver licence, tax id, national
   id, date of birth if Gopi wants it), and match on `autocomplete` values beyond `cc-`.
5. **`press_key` can type into a sensitive field (High).** Only `type_text` and `fill_form` check for sensitive
   fields. Single-character `press_key` calls after a `click_at` into a password field or card iframe go through.
   **Fix:** in `press_key`, if the focused element is sensitive (or inside a payment frame) and the key prints a
   character, refuse with the same BLOCKED message. Also check the focused element after `click_at`.
6. **`click_at` can never be flagged sensitive.** `DESCRIBE_AT_POINT` hard-codes `sensitive:false` and does not see
   iframes. **Fix:** run the same description as `DESCRIBE`, and treat points inside iframes as unknown, not safe.
7. **Page text is untrusted.** `read_page` and labels flow straight to the model. The prompt tells the model to treat
   page text as data; the browser layer can help by marking the text as page content and truncating hidden text
   (`display:none`, off-screen, zero-size) so it cannot carry hidden instructions. The current `innerText` read
   already skips `display:none` but not off-screen or tiny text.
8. **Automation fingerprint on X.** Playwright adds `--enable-automation` and sets `navigator.webdriver`. The code
   passes `--disable-blink-features=AutomationControlled`, which helps with the flag but is not a promise X will not
   challenge a signed-in automated session (verify with Gopi's account only at the M3 checkpoint; nothing posts to the
   real X before that). Using the system Chrome channel and dropping the automation switch lowers the risk.
   Note: `config.py` defaults the channel to **msedge** on Windows; the plan and Gopi's decision say Chrome, so
   the default has to become `chrome` with a dedicated `PROFILE_DIR`. **ASK GOPI** only if he wants Edge.

### 2.8 Stop and cancel (decision: audio stops p95 500 ms, browser action halts within 1 s)

- Nothing in `browser.py` can be cancelled safely mid-action. If the caller cancels the running task, the
  `asyncio` cancel lands between awaits, which can leave the mouse button down after a mouse-down, a half-typed
  string, or a scroll in progress. Typing 280 characters at 15 ms is over 4 s, so without cancel it cannot meet 1 s.
- **Fix:** a single `abort()` that cancels the in-flight action task, releases mouse buttons and held keys,
  stops any scroll, and returns the current page state with "stopped at: <what was done so far>". Long actions
  type in chunks so cancel takes effect between chunks. I will measure abort latency in the benchmark.

## 3. Site notes (expectations to verify, not results)

**X (main site).**
- Virtualized timeline: reading and ids go stale as you scroll (2.1 #1, 2.3). Highest-priority test.
- Composer is a rich text editor in a modal dialog: needs the ranking fix (2.1 #6) so the modal gets ids first,
  the clear-and-read-back fix (2.6), and the gate facts (2.7 #1 to #3). Posting uses a `div role=button`, not a
  form submit.
- Many controls have only icons, so labels come from `aria-label`. Check that Like, Reply, Repost, Share, More, and
  the Post button each get a usable label; if not, add a text fallback (nearby count/handle text).
- Login, 2FA and challenge pages are for Gopi to complete himself; stop and say so.

**Amazon.**
- Layout and class names change often, which is fine for our approach. Huge DOM and mega nav make the 60/80 item cut
  matter (2.1 #6) and make snapshot speed worth measuring (2.5).
- Checkout and payment use iframes and sensitive fields (2.2, 2.7). Treat any payment page as: describe, never type.
- Dropdowns on product pages are often custom (size/quantity), so they need click-open then click-option.

**YouTube.**
- Feeds are infinite scroll with lazy thumbnails (2.3). Video tiles are one big link; the wrapper-versus-inner-link
  issue (2.1 #4) shows up here.
- The player and some panels sit in shadow DOM or iframes; the main player is not an automation target for us, but its
  controls are keyboard-driven (space, k, f), so `press_key` is the right tool and needs no id.
- Comments load lazily after scrolling and live in an inner scroller (2.3).
- Consent and "Are you still watching" overlays appear without a load event (2.4).

## 4. Proposed order of work (after Gopi approves the plan; no code before that)

1. Wrong-click: fingerprint check, JS-side element list, multi-point occlusion, widen selector list, then run the
   wrong-click benchmark against the 2% target.
2. Quiet-wait instead of fixed sleeps, scroll container detection, new-item reporting. Measure per-action overhead.
3. Safety-facing fixes in the browser layer: hide sensitive values in snapshots, `press_key` guard, `click_at`
   description, Enter/chord reporting, `describe_pending_action`.
4. Iframe support.
5. `abort()` and cancellation-safe typing.
6. Dialog handler, popup reporting, context-close recovery.
7. Snapshot speed, only if the measurement says it is needed.

## 5. Questions

- **Gopi:** may ids be assigned after ranking (dialog and main content first) rather than strictly top-to-bottom?
  This changes numbering, so I need your yes. Without it I can only raise the item limit, which costs tokens and time.
- **Architect:** `describe_pending_action` (2.7 #3) and the extra gate facts need a tool-contract entry. Also, who owns
  fixing `safety.py` for Gopi's "confirm"-only rule? The reference gate still accepts "yes", "okay" and "go ahead".
- **QA:** please include on the mock pages: a virtualized/infinite list that recycles DOM nodes, a modal composer with
  a contenteditable box, a same-origin and a cross-origin iframe, a fake card field inside an iframe and one with
  `type=text name=cardnumber`, a `div role=button` Post, a page that opens a popup and a `confirm()` dialog, and a
  slow SPA route change.
