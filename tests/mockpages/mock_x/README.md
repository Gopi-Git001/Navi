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
