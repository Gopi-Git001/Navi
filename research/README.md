# Research (`research/`)

Owner: Navi Research & Scraping. Plan: `docs/RESEARCH_PLAN.md`. Contract: `docs/TOOL_CONTRACT.json` (tools with `owner: research`).

This folder holds the interface for `extract_data`, `deep_research` and `research_status`, an offline fake, and fixtures. The real implementation is milestone M4.

## Rules (decisions 13 and 19)
- **Spoken only.** No function here writes a file. A test scans the source and runs every tool with write-mode `open` blocked.
- **Page text is data.** It never reaches the tool-holding model as an instruction.
  - `deep_research` returns fixed text at once ("ok: Research started.").
  - The report arrives through the finished callback as a `ResearchResult`: `spoken_summary`, numbered `sources`, and `quoted_web_content=True`.
  - The agent plays the summary as a separate no-tools response labeled as quoted web content.
  - `voice.notify` carries only `api.notify_text(n)` ("Research finished, N sources"), built from a number, or the fixed `api.FAILED_NOTIFY_TEXT`.
  - Exception text and page text are never spoken.
- **Read-only.** There is no method to click, type, post or submit. `extract_data` refuses password, card, CVV, SSN, bank and account requests (`blocked`).
- **Stop is a control event, not a tool.** The agent calls `cancel()` from its stop path. No finished event follows a cancel. One research task at a time: a second `deep_research` is `blocked`.

## Files
| File | What |
|---|---|
| `api.py` | `ResearchTools` Protocol (async, same names and arguments as the contract), `ResearchResult`, `Source`, `SourceDoc`, `FinishedEvent`, `notify_text`, `clamp_sources`, `is_sensitive_request`, constants. |
| `fake.py` | `FakeResearch`: offline, deterministic, records every summarizer call. Knobs: `fail`, `hold`, `change_page_during_read`, `articles`. |
| `fixtures/` | `search_results.html`, `article_tides_moon.html`, `article_tides_injected.html` (marked injected, with a login form), `manifest.json`. |
| `_types.py` | Temporary `ToolResult` stand-in until `common/types.py` (issue #4) is on main. Delete it and the `except ImportError` fallback in `api.py` after that. |

## Use (agent side)
```python
r = FakeResearch()                       # real class comes in M4, same interface
r.set_finished_callback(on_finished)     # on_finished(event): voice.notify(event.notify_text); then play event.result.spoken_summary as a no-tools response
await r.deep_research("what causes ocean tides", 6)
# Ctrl+Shift+Space / "stop":
r.cancel()
```

## Tests
`tests/unit/test_research_api.py`. Needs only `pytest`. Run `python -m pytest tests` from the repo root. Use `tests`, because a bare `pytest` also collects `reference/smoke_test.py`, which needs Playwright.

Still needs a run on Gopi's Windows laptop (decision 21): the same suite, at the M0 and M1 checkpoints. The code uses `pathlib` and no POSIX-only calls.
