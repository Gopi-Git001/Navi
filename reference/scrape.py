"""Scraping: structured extraction with an LLM, plus cheap table/link scrapers."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

import page_scripts as JS


def _name(save_as: str | None, default: str) -> str:
    base = re.sub(r"[^\w-]+", "_", (save_as or default)).strip("_") or default
    return f"{datetime.now():%Y%m%d-%H%M%S}-{base}"


def _write(out_dir: Path, name: str, rows: list[dict]) -> tuple[Path, Path]:
    out_dir = Path(out_dir) / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    jp, cp = out_dir / f"{name}.json", out_dir / f"{name}.csv"
    jp.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    with cp.open("w", newline="", encoding="utf-8-sig") as f:  # utf-8-sig opens cleanly in Excel
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                        for k, v in r.items()})
    return jp, cp


async def extract_data(browser, llm, out_dir, instruction: str, save_as: str | None = None) -> str:
    page = browser.page
    r = await page.evaluate(JS.PAGE_TEXT, "full")
    links = await page.evaluate(JS.LINKS, 200)
    link_block = "\n".join(f"- {l['text']} -> {l['url']}" for l in links)
    data = await llm.json(
        "Extract structured data from a web page. Return {\"items\": [ {...}, ... ]} with consistent keys "
        "across items. Use only information present on the page; use null when missing. Include URLs when relevant.",
        f"What to extract: {instruction}\nPage: {r['title']} ({r['url']})\n\nPAGE TEXT:\n{r['text'][:45000]}\n\n"
        f"LINKS:\n{link_block[:12000]}")
    items = [i for i in data.get("items", []) if isinstance(i, dict)]
    if not items:
        return "Found nothing matching that on this page."
    jp, cp = _write(out_dir, _name(save_as, "extract"), items)
    preview = json.dumps(items[:3], ensure_ascii=False)[:800]
    return f"Extracted {len(items)} items. Saved {cp} and {jp}.\nFirst items: {preview}"


async def scrape_tables(browser, out_dir, save_as: str | None = None) -> str:
    tables = await browser.page.evaluate(JS.TABLES)
    if not tables:
        return "No HTML tables on this page. Try extract_data instead."
    saved = []
    for i, t in enumerate(tables):
        header = [h or f"col{j + 1}" for j, h in enumerate(t[0])]
        rows = [dict(zip(header, row)) for row in t[1:]]
        _, cp = _write(out_dir, _name(save_as, "table") + f"_{i + 1}", rows)
        saved.append(f"table {i + 1}: {len(rows)} rows x {len(header)} cols -> {cp}")
    return "Saved:\n" + "\n".join(saved)


async def scrape_links(browser, out_dir, contains: str | None = None, save_as: str | None = None,
                       limit: int = 300) -> str:
    links = await browser.page.evaluate(JS.LINKS, limit)
    if contains:
        c = contains.lower()
        links = [l for l in links if c in l["text"].lower() or c in l["url"].lower()]
    if not links:
        return "No matching links."
    _, cp = _write(out_dir, _name(save_as, "links"), links)
    sample = "\n".join(f"- {l['text'][:60]} -> {l['url'][:90]}" for l in links[:15])
    return f"{len(links)} links saved to {cp}. First ones:\n{sample}"
