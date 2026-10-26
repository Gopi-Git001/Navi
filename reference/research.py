"""Background deep research: plan queries -> search -> visit sources -> synthesize a cited report.

Runs in its own browser tab so you can keep driving the main tab while it works.
"""
from __future__ import annotations

import asyncio
import base64
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

import page_scripts as JS

SKIP_HOSTS = ("duckduckgo.com", "bing.com", "google.com", "youtube.com", "facebook.com", "instagram.com",
              "tiktok.com", "x.com", "twitter.com", "pinterest.com", "linkedin.com")


def _decode_bing(href: str) -> str:
    try:
        q = parse_qs(urlparse(href).query)
        u = q.get("u", [""])[0]
        if u.startswith("a1"):
            raw = u[2:] + "=" * (-len(u[2:]) % 4)
            return base64.urlsafe_b64decode(raw).decode()
    except Exception:
        pass
    return href


class ResearchManager:
    def __init__(self, browser, llm, out_dir: Path, notify):
        self.browser = browser
        self.llm = llm
        self.out_dir = Path(out_dir) / "research"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.notify = notify  # async callable(str)
        self.tasks: dict[int, dict] = {}
        self._n = 0

    def start(self, question: str, max_sources: int = 6) -> str:
        self._n += 1
        tid = self._n
        info = {"question": question, "status": "running", "log": [], "started": time.time(), "path": None}
        self.tasks[tid] = info
        info["task"] = asyncio.create_task(self._run(tid, question, max(2, min(int(max_sources), 12))))
        return (f"Started research task #{tid} in a background tab. It usually takes 1-3 minutes; "
                "I'll be told when it finishes. You can keep giving other commands.")

    def status(self) -> str:
        if not self.tasks:
            return "No research tasks yet."
        out = []
        for tid, t in self.tasks.items():
            mins = (time.time() - t["started"]) / 60
            last = t["log"][-1] if t["log"] else ""
            out.append(f"#{tid} [{t['status']}, {mins:.1f} min] {t['question'][:80]} — {last}"
                       + (f" — saved {t['path']}" if t["path"] else ""))
        return "\n".join(out)

    async def _search(self, page, query: str) -> list[tuple[str, str]]:
        links: list[tuple[str, str]] = []
        try:
            await page.goto("https://html.duckduckgo.com/html/?q=" + quote_plus(query),
                            wait_until="domcontentloaded", timeout=20000)
            raw = await page.eval_on_selector_all("a.result__a", "els => els.map(e => [e.href, e.innerText])")
            for href, title in raw:
                u = parse_qs(urlparse(href).query).get("uddg", [href])[0]
                links.append((u, title))
        except Exception:
            pass
        if not links:
            try:
                await page.goto("https://www.bing.com/search?q=" + quote_plus(query),
                                wait_until="domcontentloaded", timeout=20000)
                raw = await page.eval_on_selector_all("li.b_algo h2 a", "els => els.map(e => [e.href, e.innerText])")
                links = [(_decode_bing(h), t) for h, t in raw]
            except Exception:
                pass
        return [(u, t) for u, t in links if u.startswith("http") and not any(h in urlparse(u).netloc for h in SKIP_HOSTS)]

    async def _run(self, tid: int, question: str, max_sources: int) -> None:
        t = self.tasks[tid]
        log = t["log"].append
        page = None
        try:
            log("planning searches")
            plan = await self.llm.json(
                "You plan web research. Produce 3-4 diverse, specific search queries that together answer the question.",
                f"Question: {question}\nReturn {{\"queries\": [\"...\"]}}")
            queries = [q for q in plan.get("queries", []) if isinstance(q, str)][:4] or [question]

            page = await self.browser.ctx.new_page()
            try:
                await self.browser.page.bring_to_front()
            except Exception:
                pass

            candidates: list[tuple[str, str]] = []
            seen = set()
            for q in queries:
                log(f"searching: {q}")
                for u, title in await self._search(page, q):
                    key = re.sub(r"[#?].*$", "", u)
                    if key not in seen:
                        seen.add(key)
                        candidates.append((u, title))

            sources = []
            for u, title in candidates:
                if len(sources) >= max_sources:
                    break
                log(f"reading {urlparse(u).netloc}")
                try:
                    await page.goto(u, wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(1.0)
                    r = await page.evaluate(JS.PAGE_TEXT, "main")
                    if len(r["text"]) < 400:
                        continue
                    sources.append({"url": r["url"], "title": r["title"] or title, "text": r["text"][:9000]})
                except Exception:
                    continue
            if not sources:
                raise RuntimeError("couldn't read any sources (search blocked or no results)")

            log(f"writing report from {len(sources)} sources")
            block = "\n\n".join(f"[{i + 1}] {s['title']}\nURL: {s['url']}\n{s['text']}" for i, s in enumerate(sources))
            res = await self.llm.json(
                "You are a careful research analyst. Answer the question using ONLY the numbered sources. "
                "Cite claims inline like [2]. Note disagreements between sources and gaps. "
                "Return {\"spoken_summary\": \"3-4 plain sentences suitable to read aloud, no citations\", "
                "\"report_markdown\": \"full report in Markdown with headings and inline [n] citations\"}",
                f"Question: {question}\n\nSources:\n{block}")
            report = res.get("report_markdown") or "(no report produced)"
            summary = res.get("spoken_summary") or "The report is ready."
            refs = "\n".join(f"{i + 1}. [{s['title']}]({s['url']})" for i, s in enumerate(sources))
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            slug = re.sub(r"[^a-z0-9]+", "-", question.lower())[:50].strip("-")
            path = self.out_dir / f"{stamp}-{slug}.md"
            path.write_text(f"# {question}\n\n{report}\n\n## Sources\n{refs}\n", encoding="utf-8")
            t.update(status="done", path=str(path))
            log("done")
            await self.notify(f"Research task #{tid} finished. Report saved to {path}. Summary: {summary}")
        except Exception as e:
            t["status"] = "failed"
            log(f"failed: {e}")
            await self.notify(f"Research task #{tid} failed: {e}")
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass
