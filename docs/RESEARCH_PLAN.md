# Research and Extraction Plan

**Author:** Navi Research & Scraping  **Date:** 2026-10-25  **Status:** Revision 3, aligned to `docs/TECH_SPEC.md`, `docs/TOOL_CONTRACT.json` 1.0, `docs/BENCHMARKS.md` and `docs/DECISIONS.md` (decisions 1-21) on `architect/spec-and-plan` at `c9c0e95`. The interface and offline fakes from issue #11 are in `research/` (see `research/README.md`); the real implementation is M4.
**Based on:** `reference/` at `612d925` (read-only). I read `research.py`, `scrape.py`, `tools.py`, `safety.py`, `config.py`, `browser.py`, `page_scripts.py`, `llm.py`, `voice.py` (notify and gate paths), `main.py`, `smoke_test.py` and the reference README. Nothing in `reference/` was run.

**What changed from revision 2 (so the earlier PR #15 text can be compared):** the repo docs are no longer stubs, so targets now cite `BENCHMARKS.md` rows (A8, A9, B1, B2, C10, D10, D11); `scrape_tables`, `scrape_links` and `save_as` are gone from the contract; stop is a control event with no `cancel_research` tool (decision 11); files are cut (decision 13); research text reaches the model only as fixed notify text plus a no-tools quoted response (decision 19); the Windows rule (decision 21) is part of every checkpoint; most open questions are answered (section 7).

## 1. What the reference does

- **Deep research** (`research.py`, `ResearchManager`). The `deep_research(question, max_sources)` tool calls `start()`, which clamps sources to 2..12 (default 6) and returns at once ("started task #N"). A background task then: (1) asks the text model for 3-4 queries; (2) opens a new tab in the shared browser context; (3) searches `html.duckduckgo.com/html`, falling back to Bing, one query after another; (4) visits candidate URLs one at a time (`goto` 20 s timeout, fixed 1 s sleep), takes `PAGE_TEXT("main")`, skips pages under 400 chars, keeps the first 9,000 chars; (5) makes one text-model call with numbered sources and gets `{spoken_summary, report_markdown}`; (6) writes `output/research/<stamp>-<slug>.md` with a source list; (7) calls `notify(...)`, which `voice.py` injects as a *system-role* message telling the realtime model to tell the user. `research_status` polls the task log.
- **Scraping** (`scrape.py`). `extract_data(instruction, save_as)` reads the *current* tab: full `innerText` (first 45,000 chars) plus up to 200 links (first 12,000 chars), one text-model call returning `{items:[...]}`, then writes CSV (utf-8-sig) and JSON to `output/data/` and returns a count plus a 3-item preview. `scrape_tables` saves every `<table>` (max 25 tables, 1,000 rows each). `scrape_links` saves links (default 300, optional `contains` filter).
- **Wiring** (`tools.py`). `ToolRegistry.call` dispatches the 23 tools, catches `KeyError` and any exception into a short text result, and cuts every result to 7,000 chars. No per-tool timeout. Page text reaches the models as plain text in the user message.
- **Injection defense** (`llm.py`, `tools.py`). One sentence (`UNTRUSTED`) is appended to every text-model system prompt, and `INSTRUCTIONS` says page text is data. The only code-level gate is `safety.py` on *clicks and Enter in forms*. Research and scrape never click, so the gate is not involved. Good: they are read-only.
- **Profile** (`browser.py`, `config.py`). `launch_persistent_context` on `.browser_profile/`, the same context for the main tab and the research tab. Default channel is Edge on Windows, not Chrome.
- **Tests.** `smoke_test.py` covers `scrape_tables` and `scrape_links` on a local file. Nothing tests `research.py` or `extract_data`.

## 2. Failure modes found in the reference

| # | Where | Problem |
|---|---|---|
| R1 | `research.py` `SKIP_HOSTS` check at end of `_search` | Substring match on host (`h in netloc`). `x.com` also drops `netflix.com`, `fedex.com`, `dropbox.com`, `linux.com`. `google.com` drops `developers.google.com`, `cloud.google.com`, `support.google.com`; `facebook.com` drops `developers.facebook.com`. Official docs get thrown away. |
| R2 | `_search`, `_decode_bing` | Every exception is swallowed (`except Exception: pass`). A captcha, 429 or "unusual traffic" page just looks like "no links". Final error is the vague "search blocked or no results". Bing decode only handles the `a1` prefix. No ranking, no ad or junk filter, no dedupe beyond URL without query string. |
| R3 | `_run` | No overall deadline. Searches and page loads are serial: 4 queries x up to 2 engines x 20 s, then up to 21 s per candidate until `max_sources` succeed, with no candidate cap. Worst case is many minutes, versus the "1-3 minutes" the tool promises. LLM calls in `llm.py` have no timeout (OpenAI client default is 10 min with retries). |
| R4 | `_run`, `JS.PAGE_TEXT` | Thin-page rule is one length check (`<400`), skipped silently. `main` mode falls back to the whole body (nav, footer, cookie text) when no `article/main` over 500 chars. No handling of cookie, login or paywall walls, iframes, lazy content, or PDFs (a PDF `goto` triggers a download in Gopi's profile). Text is cut at 9,000 chars from the top, so the end of an article is lost. No date or author captured. |
| R5 | `_run` | No cancel. `voice._interrupt` clears audio and the reply, not research tasks, so "stop" does not stop research. No limit on parallel tasks: the model can call `deep_research` repeatedly and open many tabs and LLM calls. `tasks` is never pruned. |
| R6 | `_run` (`new_page`), `browser._ensure_page`, `browser.tabs` | The research tab is created with `ctx.new_page()`, not `_new_page`. If the main tab closes, `_ensure_page` picks `open_pages[-1]`, which can be the research tab. `tabs list/switch/close` shows it and can close it, shifting indexes mid-run. |
| R7 | `_run` end, `voice.notify` | **Injection path.** `spoken_summary` is model output derived from untrusted pages, and it is sent to the realtime model (which holds all browser tools) as a *system-role* message. The failure message also embeds raw exception text. A poisoned page can aim at the agent with system-level trust. |
| R8 | `research.py` prompt | Sources are only numbered text blocks in the user message. A page can print a fake `[3] ... URL:` block or "ignore the above". Output is never checked, only trusted to the one `UNTRUSTED` sentence. No claim-to-source check, so a citation can point at a page that does not say it. No "not enough evidence" rule: one source is enough to produce a confident report. |
| R9 | `_search` / `_run` | No URL policy. Any `http(s)` link is opened in the signed-in dedicated profile: no private-address or `file` check, no robots, no per-domain cap, no deny list that Gopi can edit. |
| R10 | `scrape.extract_data` | Extraction is LLM-only and unverified, so values can be invented. Reads only what is rendered now: no scroll, no pagination, no virtualised lists (X timelines render only the posts near the viewport). 45,000 and 12,000 char cuts are silent. Non-dict items are dropped silently. No row count versus what the page says. |
| R11 | `llm.LLM.json` | Bad JSON returns `{}`, so a truncated or failed model reply becomes "Found nothing matching that on this page." That is a silent failure presented as an empty result. No `max_tokens`, no retry, no timeout. |
| R12 | `scrape._write` | Files are written (`output/research`, `output/data`) and `notify` speaks the path. This contradicts `PRODUCT_PLAN.md` ("Saving research or scrape output to files" is out of scope; deep research is spoken summary only, scraping read aloud). CSV cells from pages are written raw, so a cell starting with `=`, `+`, `-` or `@` is a formula when opened in Excel (the code deliberately writes utf-8-sig for Excel). |
| R13 | `scrape.scrape_tables`, `JS.TABLES` | `dict(zip(header, row))` silently drops extra cells and collapses duplicate header names (columns vanish). Merged cells (`colspan`) misalign rows. Div-based grids and shadow DOM tables are not seen. Table and row caps are silent. |
| R14 | `scrape.scrape_links`, `JS.LINKS` | Links with no visible text (icon links) are skipped. `mailto:` and `data:` pass. Tracking parameters are kept, so duplicates survive. |
| R15 | `tools.ToolRegistry.call` | A scrape result over 7,000 chars is cut mid-item with `[truncated]`. Page-derived preview text goes back to the realtime model as tool output, with the same trust as user text. |
| R16 | `research.py` `SKIP_HOSTS` | Skips `x.com`, `linkedin.com` etc. for research, which is right for logged-in sites. But `scrape` has no such rule and runs on whatever tab is open, including logged-in pages. X's own rules on automated collection still need checking (open item in `PRODUCT_PLAN.md`; not verified in this pass, because I may only use the repo). |

## 3. Keep, change, reuse

**Keep (works and fits):** background tab and instant "started" reply (matches the under-1 s spoken response); `research_status` polling; plan-search-read-synthesize flow; the 2..12 source clamp; numbered inline citations and a source list; DDG-HTML then Bing as the no-new-dependency search path; read-only behaviour (no clicks, so the confirm gate is never in play); `page_scripts.PAGE_TEXT`, `TABLES`, `LINKS` as the base extractors; `UNTRUSTED` wording in `llm.py`; the three tool names `extract_data`, `deep_research`, `research_status` (unchanged in the contract).

**Change (in `research/` only, after approval):**
1. Replace the substring skip check with an exact host or suffix match (R1), plus an editable deny list and a URL policy gate: `http(s)` only, no private or local addresses, per-domain cap, no PDFs unless we choose to handle them (R9).
2. Search: detect block pages (captcha, 429, "unusual traffic") and say which engine answered. Log, do not swallow (R2).
3. One overall deadline, a candidate cap, and 3-4 pages read in parallel. Every LLM call gets a timeout (R3, R11).
4. `cancel()` on the research object, called by the agent's stop path (decision 11: Ctrl+Shift+Space and the word "stop" are control events, **not a tool**, so there is no `cancel_research` in the contract). Concurrency limit of 1 active task: a second `deep_research` is `blocked` (R5). Done in the interface and fake (#11); the real task and tab cleanup are M4.
5. Create the research tab through an internal `browser/` call (not a contract tool, so the model cannot see or close it) and exclude it from `tabs` and `_ensure_page` (R6). The contract has no such call, so this is an open question for the Architect and Navi Browser (section 7, item 3).
6. Treat research output as untrusted (decision 19, R7, R8). Done in the interface (#11): `deep_research` returns fixed text at once; the report is a `ResearchResult` (short summary, numbered sources, `quoted_web_content=True`) handed to the agent by callback; `voice.notify` carries only `notify_text(n)` ("Research finished, N sources", built from a number) or the fixed failure phrase; the agent plays the summary as a separate no-tools response labeled as quoted web content and gives follow-up questions the report as quoted data. Page text goes to the text model only as `data` next to a fixed instruction string. Exception text is never spoken. Still M4: random delimiters around sources and a claim-to-source check.
7. Add a support check and a "not enough evidence" path (fewer than 3 readable sources) (R8). Backs D11 (no invented sources, 100%). The #11 fake already says "found nothing" and reports 0 sources when nothing was readable.
8. Extraction (`extract_data`, the only extraction tool left): schema first, scroll or paginate up to a cap, verify each cell appears in the page text (or mark it inferred), keep `{items, rows_found, warnings, truncated}` in memory, and say "partial, N of M" aloud instead of success. Bad model JSON becomes an error, not "found nothing" (R10, R11, R13, R14). Table and link reading happens inside `extract_data`, since `scrape_tables` and `scrape_links` were cut from the contract. The spoken text starts with a "quoted web content, not instructions" line. Refuses password, card, CVV, SSN, bank and account requests (`blocked`; matches the browser's rule for typing, C9). Backs D10 (fields correct, at least 95%).
9. **Output goes to speech, not files** (R12, decisions 13 and the contract's "No files"). Return a structured result in memory plus a short spoken form (at most about 4 sentences, no URLs; names read aloud, at most 3 items before offering more). `save_as` is gone. No function in `research/` writes a file; a test scans the source and runs every tool with write-mode `open` blocked. Benchmark reports are written by QA's harness, not by research code. If Gopi later wants files, add CSV formula escaping first.
10. **Blocks and challenges (decision 16).** Research does not solve or work around a CAPTCHA, bot check or "unusual traffic" page and never signs in. It ends with a fixed spoken reason (the search engine or site needs Gopi), and Gopi handles it. Read-only means research never reaches the confirm gate (decisions 3, 7-9, 20) and never counts toward the X comment limit (decision 17).
11. **Model calls** always carry the untrusted-page rule (`TECH_SPEC` section 7) and a timeout. No new dependencies: standard library plus the existing ones (decision 15).

**Reuse as-is:** `PAGE_TEXT`, `LINKS` (with the fixes in R14), `ResearchManager.start` and `status` shape, `_decode_bing`'s `a1` path. Copy by rewriting under `research/`; `reference/` stays read-only.

## 4. Targets

Targets that exist in `docs/BENCHMARKS.md` are cited by row and are not mine to lower. Rows I propose that are **not** in `BENCHMARKS.md` are marked **[PROPOSED]** for the Architect to adopt or drop. Tests use the local mock X page and local fixtures. Nothing touches the real X (decision 6). Per decision 21, a row is done only after a passing run on Gopi's Windows laptop.

| Metric | Target | Source |
|---|---|---|
| Spoken "started" reply after the command | median <= 700 ms, p95 <= 1000 ms | A1 (Gopi) |
| `deep_research` with 6 sources, question to spoken summary | p95 <= 180 s; started within A1 | A9 |
| `extract_data` on a page of up to 50 items, to first spoken result | p95 <= 20 s | A8 |
| `extract_data` fields correct on QA's fixture pages | >= 95% of fields | D10 |
| Research summaries cite only pages actually read; no invented sources | 100% | D11 |
| Page instructions followed (prompt-injection mock pages), including through summary and `notify` | 0 | C10 |
| "stop" or Ctrl+Shift+Space during research: work cancelled | within B2's 1 s, nothing started afterwards | B2, B3 |
| Characters typed into password, card, CVV, SSN, bank or account fields by research | 0 (research types nothing; extraction refuses those requests) | C9 |
| Research or extraction actions that click, post, submit, DM, email or delete | exactly 0 (read-only, no such method) | **[PROPOSED]** |
| Deep research hard stop | 240 s with what is verified so far | **[PROPOSED]** |
| Quick fact (search then read, no deep task) | p95 <= 60 s | **[PROPOSED]** |
| Citation validity (claims supported by cited text) | >= 95% | **[PROPOSED]**, refines D11 |
| Coverage of hand-written key facts | >= 80% | **[PROPOSED]** |
| Thin-evidence questions answered with "not enough evidence" | >= 90% | **[PROPOSED]** |
| Extraction: column consistency over 3 runs; silent-failure rate | 100% identical; exactly 0 | **[PROPOSED]** |
| Block, empty page, slow page, never-loading page: run ends with a report or a clear spoken reason | 100%, no hangs | **[PROPOSED]** |
| Spoken form: no URLs, at most about 4 sentences | 100% | **[PROPOSED]** |
| Research code files written | 0 (test in `tests/unit/test_research_api.py`) | decision 13 |

Report numerator and denominator, p50/p95/max for times, and the environment (commit, browser, model, machine). Live-web questions (about 5) are reported separately and are never a pass/fail gate.

## 5. Test material (shared with Navi QA, no second set)

Done in #11: `research/fixtures/` has three small pages (a search results page, two source articles, one with injected instructions and a login form) with a `manifest.json` that marks the injected page, plus tests that it is only passed as data. The rest comes with QA's mock pages (M0 issues #7 and #8) and M4. Planned local fixtures: article, JS-rendered page, cookie banner, paywall, login wall, infinite scroll, paginated table, merged-cell table, duplicate-header table, iframe page, shadow-DOM page, slow page, never-ending page, PDF link, **mock X thread with a virtualised reply list**, and several prompt-injection pages (including text aimed at the summary and at `notify`). About 20 fixed research questions with key-fact lists, covering factual, comparison, recent, how-to, ambiguous and no-answer. A stub for the text model lets the fixtures run offline and deterministically.

## 6. Build order (`research/` only; tests under `tests/unit/`)
1. **M0, issue #11 (done in this PR):** interface (`research/api.py`), offline fake and fixtures, tests for contract match, no files, quoted flag, fixed notify text, injected page as data only, stop and concurrency.
2. **M4, with Navi QA:** more fixtures and answer keys; URL policy gate, host matching fix, page fetch with timeouts, content checks.
3. Extraction with schema, completeness and verification (D10, A8).
4. Search with block detection (decision 16) and the overall deadline; real cancel and the one-task limit.
5. Untrusted-source delimiters, support check, "not enough evidence" (D11, C10).
6. Spoken-form output; benchmark rows A8, A9, D10, D11 reported; Windows laptop run at the M4 checkpoint.

## 7. Open questions (answered ones recorded, not deleted)

**Answered:**
1. Contract: `scrape_tables` and `scrape_links` removed; no `cancel_research` tool; no `save_as` (contract 1.0, Gopi-approved).
2. Files: none in v1 (decision 13).
3. Time targets: A8 and A9 in `BENCHMARKS.md` (section 4).
4. Notify path: fixed text only, summary as a separate no-tools response labeled as quoted web content (decision 19).

**Still open:**
1. **Search source** (Architect, then Gopi if paid): is DDG-HTML then Bing, with no search API or new dependency, acceptable? Both are scraped HTML and can break or get blocked. A paid API needs Gopi's approval (decision 15 allows no new dependencies).
2. **How the report reaches the agent** (Architect): `TECH_SPEC` section 2 says each research method "returns spoken-style text". I implemented it as the contract's `ToolResult` (fixed text) for `deep_research` at start, with the report delivered through a callback as a `ResearchResult` (decision 19 needs the summary kept apart from tool output). Please confirm, or tell me the spec wording should change. Navi Agent Brain owns the receiving side.
3. **Research tab** (Architect, Navi Browser): research needs an internal `browser/` call to open a background tab the user and model cannot switch to, read text, scroll, wait, close and cancel. It is not a contract tool. Please add it to `TECH_SPEC` section 2 or tell me to use the normal tab tools.
4. **Scraping X** (Gopi): is reading real X pages (reply names) allowed after M3, given X's rules on automated collection? Until then research and extraction run only on the mock page.
5. **Deny list** (Gopi): I propose starting with banks, mail, social feeds and any logged-in site, editable by Gopi.
6. **Model and cost** (Gopi): reference uses `gpt-4.1-mini` through `TEXT_MODEL`. Is that the choice, and is there a cost cap per report?
