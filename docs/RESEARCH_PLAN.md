# Research and Extraction Plan

**Author:** Navi Research & Scraping  **Date:** 2026-10-25  **Status:** Revision 2, for Gopi and Navi Architect. Plan only, no code.
**Based on:** `origin/main` at `612d925`. I read `reference/research.py`, `scrape.py`, `tools.py`, `safety.py`, `config.py`, `browser.py`, `page_scripts.py`, `llm.py`, `voice.py` (notify and gate paths), `main.py`, `smoke_test.py` and the reference README. Nothing was run. Gopi's six decisions (room, 2026-10-25) are treated as part of the Product Plan, because `docs/DECISIONS.md` is not on `main` yet.

**Repo docs are stubs.** On `main`, `docs/TECH_SPEC.md`, `BENCHMARKS.md`, `SAFETY_RULES.md`, `TOOL_CONTRACT.json` (`"tools": []`) and `COMMAND_SCRIPT.csv` (header only) have no content. So I cannot align to them yet. Targets below follow Gopi's decisions plus `PRODUCT_PLAN.md`, and anything else is marked **[PROPOSED]** until the Architect writes `BENCHMARKS.md`.

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

**Keep (works and fits):** background tab and instant "started" reply (matches the under-1 s spoken response); `research_status` polling; plan-search-read-synthesize flow; the 2..12 source clamp; numbered inline citations and a source list; DDG-HTML then Bing as the no-new-dependency search path; read-only behaviour (no clicks, so the confirm gate is never in play); `page_scripts.PAGE_TEXT`, `TABLES`, `LINKS` as the base extractors; sanitised file-name helper `_name`; `UNTRUSTED` wording in `llm.py`; the `TOOL_SCHEMAS` names, so the agent prompt stays stable.

**Change (in `research/` only, after approval):**
1. Replace the substring skip check with an exact host or suffix match (R1), plus an editable deny list and a URL policy gate: `http(s)` only, no private or local addresses, per-domain cap, no PDFs unless we choose to handle them (R9).
2. Search: detect block pages (captcha, 429, "unusual traffic") and say which engine answered. Log, do not swallow (R2).
3. One overall deadline, a candidate cap, and 3-4 pages read in parallel. Every LLM call gets a timeout (R3, R11).
4. `cancel_research`, wired to "stop" and the hotkey, plus a concurrency limit of 1 active task (R5).
5. Create the research tab through the browser module and exclude it from `tabs` and `_ensure_page` (R6).
6. Treat research output as untrusted: wrap sources in clearly delimited blocks with random delimiters, strip instruction-like text from the summary, and have `notify` deliver it as plain data with a fixed template (not raw model text as system role), with the exception text replaced by a fixed phrase (R7, R8).
7. Add a support check and a "not enough evidence" path (fewer than 3 readable sources) (R8).
8. Extraction: schema first, scroll or paginate up to a cap, verify each cell appears in the page text (or mark it inferred), return `{items, rows_found, warnings, truncated}`, and say "partial, N of M" aloud instead of success. Bad model JSON becomes an error, not "found nothing" (R10, R11, R13, R14).
9. **Output goes to speech, not files** (R12). Return a structured result in memory plus a short spoken form (at most about 4 sentences, no URLs; names read aloud, at most 3 items before offering more). Drop `save_as`. File writing stays only as a test artifact in `benchmarks/results/`. If Gopi later wants files, add CSV formula escaping first.

**Reuse as-is:** `PAGE_TEXT`, `LINKS` (with the fixes in R14), `ResearchManager.start` and `status` shape, `_decode_bing`'s `a1` path.

## 4. Targets

Gopi's decisions have no research or scrape numbers. Research and scrape rows below are **[PROPOSED]**; the Architect should copy them into `docs/BENCHMARKS.md` or change them. Tests use the local mock X page and local fixtures. Nothing touches the real X.

| Metric | Target |
|---|---|
| Spoken "started" reply after the command (inherits Gopi's first-spoken-word target) | median <= 700 ms, p95 <= 1000 ms |
| "stop" or `Ctrl+Shift+Space` during research: tabs closed and work cancelled | <= 1 s (matches the browser-action halt) |
| Deep research, question to spoken summary **[PROPOSED]** | p95 <= 180 s, hard stop at 240 s with what is verified so far |
| Quick fact (search then read, no deep task) **[PROPOSED]** | p95 <= 60 s |
| Scrape of one page (mock X replies, tables, links) **[PROPOSED]** | p95 <= 15 s after first baseline run |
| Research or scrape actions that click, post, submit, DM, email or delete | exactly 0 (read-only) |
| Injection test pages: runs where Navi followed a page's instruction (including through the summary and `notify`) | exactly 0 |
| Citation validity (claims supported by cited text) | >= 95%, 0 invented sources |
| Quote accuracy / cited link still valid or snapshotted | 100% |
| Coverage of hand-written key facts | >= 80% |
| Thin-evidence questions answered with "not enough evidence" | >= 90% |
| Extraction: column consistency over 3 runs; silent-failure rate | 100% identical; exactly 0 |
| Extraction: row completeness; cell accuracy | >= 98%; >= 98% |
| Block, empty page, slow page, never-loading page: run ends with a report or a clear spoken reason | 100%, no hangs |
| Spoken form: no URLs, at most about 4 sentences | 100% |

Report numerator and denominator, p50/p95/max for times, and the environment (commit, browser, model, machine). Live-web questions (about 5) are reported separately and are never a pass/fail gate.

## 5. Test material (shared with Navi QA, no second set)

Local fixtures: article, JS-rendered page, cookie banner, paywall, login wall, infinite scroll, paginated table, merged-cell table, duplicate-header table, iframe page, shadow-DOM page, slow page, never-ending page, PDF link, **mock X thread with a virtualised reply list**, and several prompt-injection pages (including text aimed at the summary and at `notify`). About 20 fixed research questions with key-fact lists, covering factual, comparison, recent, how-to, ambiguous and no-answer. A stub for the text model lets the fixtures run offline and deterministically.

## 6. Build order (after sign-off, `research/` only)
1. Fixtures and answer keys with Navi QA.
2. URL policy gate, host matching fix, page fetch with timeouts, content checks.
3. Extraction with schema, completeness and verification; structured in-memory result.
4. Search with block detection and the overall deadline; cancel and concurrency limit.
5. Untrusted-source wrapping, safe `notify` template, support check, "not enough evidence".
6. Spoken-form output; benchmarks report.

## 7. Open questions (answered ones removed)
1. **Reference gaps for the Architect.** Does the tool contract keep `deep_research`, `research_status`, `extract_data`, `scrape_tables`, `scrape_links` by name, and add `cancel_research`? I propose dropping `save_as`.
2. **Files.** The Product Plan says spoken only. Confirm that no research or scrape file goes to disk in v1 (my plan assumes none).
3. **Search source.** Is DDG-HTML then Bing, with no search API or new dependency, acceptable? Both are scraped HTML and can break or get blocked; a paid API needs Gopi's approval.
4. **Research and scrape time targets** in section 4 (Architect to confirm in `BENCHMARKS.md`).
5. **Notify path.** Agent Brain owns `voice.notify` and the realtime message role. Can research results arrive as tool output or a fixed template instead of a system-role message (R7)?
6. **Browser module.** Research needs: open a tab the user cannot switch to by mistake, page text, scroll, wait for load, close, cancel. Architect to put these in the tool contract (R6).
7. **Scraping X.** Gopi to decide whether scraping real X pages (reply names) is allowed after M3, given X's rules on automated collection. Until then it runs only on the mock page.
8. **Deny list.** I propose starting with banks, mail, social feeds and any logged-in site, editable by Gopi.
9. **Model and cost.** Reference uses `gpt-4.1-mini` through `TEXT_MODEL`. Is that the choice, and is there a cost cap per report?
