"""Offline fake of `ResearchTools` for tests. Reads only `research/fixtures/`; never opens
a socket, never writes a file, never follows anything it reads.

The fake "summarizer" is deterministic: it quotes the first sentence of up to three pages.
It receives page text as `SourceDoc` data with the fixed `SUMMARIZER_INSTRUCTIONS`, and
records every call so tests can prove page text never travelled as instructions.
"""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Optional, Sequence

from research import api
from research.api import (
    FinishedEvent,
    ResearchResult,
    Source,
    SourceDoc,
    ToolResult,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"

_SKIP = {"script", "style", "noscript", "template", "head", "textarea", "select", "option", "svg"}
_BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "br", "article", "section", "main", "ol", "ul"}


class _PageParser(HTMLParser):
    """Visible text only. Form fields (input, textarea, select) never contribute values."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.links: list[str] = []
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "title":
            self._in_title = True
        if tag in _SKIP:
            self._skip += 1
        if tag in _BLOCK:
            self.parts.append("\n")
        if tag == "a" and "result" in (a.get("class") or "").split() and a.get("href"):
            self.links.append(a["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag in _SKIP and self._skip:
            self._skip -= 1
        if tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _parse(html: str) -> _PageParser:
    p = _PageParser()
    p.feed(html)
    return p


def _clean(parts: Sequence[str]) -> str:
    lines = (re.sub(r"[ \t]+", " ", ln).strip() for ln in "".join(parts).split("\n"))
    return "\n".join(ln for ln in lines if ln)


@dataclass(frozen=True)
class Fixture:
    name: str
    url: str
    title: str
    kind: str  # "search" | "article"
    injected: bool  # True when the page contains text that tries to instruct the agent
    text: str  # visible text only, paragraphs on separate lines
    links: tuple[str, ...] = field(default=())


def load_manifest() -> list[dict]:
    raw = (FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8")
    return json.loads(raw)["fixtures"]


def load_fixture(name: str) -> Fixture:
    for entry in load_manifest():
        if entry["name"] == name:
            html = (FIXTURE_DIR / entry["file"]).read_text(encoding="utf-8")
            p = _parse(html)
            return Fixture(
                name=name, url=entry["url"], title=p.title.strip(), kind=entry["kind"],
                injected=bool(entry["injected"]), text=_clean(p.parts), links=tuple(p.links),
            )
    raise KeyError(name)


def _first_sentence(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text).strip()
    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0].strip()
    return first if first.endswith((".", "!", "?")) else first + "."


@dataclass(frozen=True)
class SummarizerCall:
    instructions: str
    data: tuple[SourceDoc, ...]


class RecordingSummarizer:
    """Stand-in for the text model. Instructions are fixed; pages arrive only as data."""

    def __init__(self) -> None:
        self.calls: list[SummarizerCall] = []

    def summarize(self, instructions: str, data: Sequence[SourceDoc]) -> str:
        self.calls.append(SummarizerCall(instructions, tuple(data)))
        if not data:
            return "I found nothing I could read."
        n = len(data)
        out = [f"I read {n} source" + ("" if n == 1 else "s") + "."]
        for d in list(data)[:3]:
            out.append(f"Source {d.number} says: {_first_sentence(d.text)}")
        return " ".join(out)


class FakeResearch:
    """Fake research tools.

    Test knobs: `articles` (default: the manifest articles), `extra_articles`,
    `fail` ("network" | "timeout" | None) with `fail_detail` (must never be spoken),
    `hold` (research waits for `release()`), `change_page_during_read` (a Fixture the
    current page switches to while `extract_data` reads).
    """

    def __init__(
        self,
        articles: Optional[Sequence[Fixture]] = None,
        extra_articles: Sequence[Fixture] = (),
        fail: Optional[str] = None,
        fail_detail: str = "",
        hold: bool = False,
        change_page_during_read: Optional[Fixture] = None,
    ) -> None:
        if articles is None:
            articles = [load_fixture(e["name"]) for e in load_manifest() if e["kind"] == "article"]
        self._articles = list(articles) + list(extra_articles)
        self._search = load_fixture("search_results")
        self.fail = fail
        self.fail_detail = fail_detail
        self._gate = asyncio.Event()
        if not hold:
            self._gate.set()
        self._change_to = change_page_during_read
        self.summarizer = RecordingSummarizer()
        self._page: Optional[Fixture] = None
        self._state = "idle"  # idle | running | finished | failed | cancelled
        self._task: Optional[asyncio.Task] = None
        self._result: Optional[ResearchResult] = None
        self._callback: Optional[Callable[[FinishedEvent], None]] = None

    # ----- test helpers
    def set_page(self, page: Optional[Fixture]) -> None:
        self._page = page

    def release(self) -> None:
        self._gate.set()

    async def wait_done(self) -> None:
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ----- ResearchTools
    def set_finished_callback(self, callback):
        self._callback = callback

    def last_result(self) -> Optional[ResearchResult]:
        return self._result

    def cancel(self) -> None:
        if self._state == "running" and self._task is not None:
            self._state = "cancelled"
            self._task.cancel()

    async def research_status(self) -> ToolResult:
        if self._state == "idle":
            return ToolResult("ok", "ok: No research has been started.")
        if self._state == "running":
            return ToolResult("ok", "ok: Research is running.")
        if self._state == "cancelled":
            return ToolResult("cancelled", "cancelled: Research was stopped.")
        if self._state == "failed":
            return ToolResult("error", "error: Research did not finish.")
        n = len(self._result.sources) if self._result else 0
        return ToolResult("ok", f"ok: {api.notify_text(n)}.")

    async def deep_research(self, question: str, max_sources: int = api.DEFAULT_MAX_SOURCES) -> ToolResult:
        if not isinstance(question, str) or not question.strip():
            return ToolResult("error", "error: I need a question to research.")
        if self._state == "running":
            return ToolResult("blocked", "blocked: Research is already running.")
        limit = api.clamp_sources(max_sources)
        self._state = "running"
        self._result = None
        self._task = asyncio.ensure_future(self._run(limit))
        return ToolResult("ok", "ok: Research started.")

    async def extract_data(self, instruction: str) -> ToolResult:
        if not isinstance(instruction, str) or not instruction.strip():
            return ToolResult("error", "error: I need to know what to extract.")
        if api.is_sensitive_request(instruction):
            return ToolResult("blocked", "blocked: I don't read password, card, ID or account fields.")
        page = self._page
        if page is None:
            return ToolResult("error", "error: No page is open.")
        try:
            self._maybe_fail()
            await asyncio.sleep(0)  # the page can change here, as it can mid-read for real
            if self._change_to is not None:
                self._page, self._change_to = self._change_to, None
            if self._page is not page:
                return ToolResult("error", "error: The page changed while I was reading it. Nothing was extracted.")
        except TimeoutError:
            return ToolResult("error", "error: The page took too long to read.")
        except Exception:  # fixed phrase only, never the exception text
            return ToolResult("error", "error: I could not read the page.")
        paragraphs = [ln for ln in page.text.split("\n") if ln.strip()]
        if not paragraphs:
            return ToolResult("ok", f"ok: {api.QUOTED_LABEL}.\nI found nothing matching that on this page.")
        doc = SourceDoc(1, page.title, page.url, page.text)
        self.summarizer.calls.append(
            SummarizerCall(api.SUMMARIZER_INSTRUCTIONS, (doc,))
        )
        items = [f"{i}. {_first_sentence(p)}" for i, p in enumerate(paragraphs[:3], 1)]
        more = len(paragraphs) - 3
        if more > 0:
            items.append(f"There are {more} more.")
        text = f"ok: {api.QUOTED_LABEL}.\n" + "\n".join(items)
        return ToolResult("ok", text[: api.MAX_SPOKEN_CHARS])

    # ----- internals
    def _maybe_fail(self) -> None:
        if self.fail == "network":
            raise ConnectionError(self.fail_detail or "network down")
        if self.fail == "timeout":
            raise TimeoutError(self.fail_detail or "timed out")

    async def _run(self, limit: int) -> None:
        try:
            await self._gate.wait()
            self._maybe_fail()
            hrefs = list(self._search.links)
            by_url = {a.url: a for a in self._articles}
            chosen = [by_url[h] for h in hrefs if h in by_url]
            chosen += [a for a in self._articles if a not in chosen]
            chosen = chosen[:limit]
            docs = [SourceDoc(i, a.title, a.url, a.text) for i, a in enumerate(chosen, 1)]
            summary = self.summarizer.summarize(api.SUMMARIZER_INSTRUCTIONS, docs)
            sources = tuple(Source(d.number, d.title, d.url) for d in docs)
            self._result = ResearchResult(summary, sources, quoted_web_content=True)
            self._state = "finished"
            event = FinishedEvent(api.notify_text(len(sources)), self._result)
        except asyncio.CancelledError:
            self._state = "cancelled"
            raise
        except Exception:  # fixed phrase only; the detail is dropped on purpose
            self._result = ResearchResult("Research did not finish.", (), quoted_web_content=True)
            self._state = "failed"
            event = FinishedEvent(api.FAILED_NOTIFY_TEXT, self._result)
        if self._callback is not None:
            self._callback(event)
