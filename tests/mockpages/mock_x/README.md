# Mock X page

A local stand-in for X, used so no automated test ever touches real X (Gopi's rule: nothing posts to real X before the M3 checkpoint).

Run it by hand: `python -m tests.mockpages.mock_x.server`, then open the printed URL (`.../mock_x/index.html?variant=<name>`).
In tests use the `open_page("mock_x", variant=...)` and `server` fixtures from `tests/conftest.py`.

## Routes (hash based)
`#/home` (post composer, own posts m1/m2), `#/NASA` (3 posts with photos), `#/search?q=Cloudflare+Workers+news` (4 results),
`#/status/<id>` (post plus replies, inline reply composer), `#/messages/mockfriend` (DM), `#/login`.

## What mimics X
- Reply icon (`data-testid=reply`, id `reply-btn-<post id>`) opens a modal composer; the status page has an inline one. Both use `#composer-box` (a contenteditable `div role=textbox`).
- `Post`/`Reply` is `#post-button`, a `div role=button` (not a form submit). Disabled (`aria-disabled=true`) while empty. Ctrl+Enter also posts. Plain Enter only adds a line.
- DM: `#dm-box` and `#dm-send`. Enter sends (like X).
- Delete: `#more-<id>` menu, then `#menu-delete`, then dialog `#delete-confirm`. Only the user's own posts (m1, m2) have Delete.
- Irreversible controls carry `data-risk`: `post`, `comment`, `dm`, `delete`.

## Requests (server side, all POST)
`/i/api/graphql/mock/CreateTweet` (kind `CreateTweet`), `/i/api/graphql/mock/DeleteTweet` (`DeleteTweet`), `/i/api/1.1/dm/new` (`dm_new`).
Browser's network lock can match these paths. `server.requests(kind)` returns them; nothing leaves 127.0.0.1.

## Log
Every click, key, input change, route change, toast, request and response is in `window.__navi_log` and at `GET /__log` (survives reload).
`POST /__reset` clears it. `GET /__fixture` gives the account and the known reply authors.

## Variants (`?variant=`)
| Variant | What it does |
|---|---|
| `pinned_first` | a pinned post is first |
| `repost_first` | first item is a repost by another account |
| `no_photo` | the newest post has no photo |
| `signed_out` | no composer, reply shows a login wall, DM gated |
| `replies_limited` | reply icons disabled, "Who can reply?" notice |
| `feed_shifts_after_observe` | ids are positions, a new post is inserted 2 s after `#/NASA` loads |
| `post_error_toast` | CreateTweet returns 500, page shows an error toast |
| `ctrl_enter_only` | no Post button, only Ctrl+Enter |
| `virtualized` | 60 replies in a list that recycles 10 nodes (same element id shows a different reply after scrolling) |
| `rate_limit_reached` | not enforced by the page; the harness preloads Navi's limiter state (10/hour, 30/day) |


## Approval scenarios for the real controller (asked by the Architect, M1)
These run against the real BrowserController on this page once it exists. The pass condition is always read from the server log, never from what the controller says: `server.requests("CreateTweet")`, `DeleteTweet`, `dm_new`.

| # | Steps | Must happen |
|---|---|---|
| G1 | Open `#/status/n1`, type "Amazing photo" into `#composer-box`, `describe_pending_action` for `#post-button`, `approve()` it. Then click the DM send on `#/messages/mockfriend` (a different gated action). | `Gated` is raised, 0 requests of any kind. Going back and clicking Post for the first action raises `Gated` again, 0 `CreateTweet`. A fresh `approve()` then lets exactly 1 `CreateTweet` through. |
| G2 | Same start, `approve()`, then change the composer text (type more, clear, `fill_form`, press a printing key). Click Post. | `Gated` is raised, 0 `CreateTweet`. After a fresh `approve()` of the new text, exactly 1 `CreateTweet` with the new text. |
| G3 | `approve()`, then navigate away and back, switch tabs, history back, or `halt()`. Click Post. | `Gated`, 0 `CreateTweet`. |
| G4 | `approve()`, wait 30.1 s (fake clock), click Post. | `Gated`, 0 `CreateTweet`. At 29.9 s it is still allowed. |
| G5 | `approve()`, then `observe`, scroll, hover, open the more menu, focus the composer. Click Post. | Not cleared: exactly 1 `CreateTweet`. |
| G6 | Approve a reply to post n1, then open the composer on post n2 and click its Post. | `Gated` (target differs by post url), 0 `CreateTweet`. |
| G7 | Use `?variant=ctrl_enter_only`, approve, press Ctrl+Enter after editing the text. | `Gated`, 0 `CreateTweet`. Without the edit, exactly 1. |
| G8 | Delete: approve the dialog's Delete for post m1, then click the menu Delete on m2. | `Gated`, 0 `DeleteTweet`. |

Until the real controller lands the same rules are covered against the fake in PR #20 (`tests/unit/test_browser_api.py`).
