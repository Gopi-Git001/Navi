"""Playwright browser controller: perception + actions with a visible cursor."""
from __future__ import annotations

import asyncio
import base64
import math
import re
from urllib.parse import quote_plus

from playwright.async_api import Page, async_playwright

import page_scripts as JS
from safety import RISKY, ConfirmationGate

DOMAIN_RE = re.compile(r"^[\w-]+(\.[\w-]+)+(:\d+)?(/.*)?$")
SEARCH_URLS = {
    "google": "https://www.google.com/search?q={}",
    "bing": "https://www.bing.com/search?q={}",
    "duckduckgo": "https://duckduckgo.com/?q={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "amazon": "https://www.amazon.com/s?k={}",
}

_CAMEL = re.compile(r"([a-z0-9])([A-Z])")


def _widen(info: dict | None) -> dict | None:
    """Fold data-testid / enclosing role=button text into the label when it reads risky
    (e.g. an icon-only div role=button with data-testid="tweetButton"), so the gate sees it."""
    if not info or RISKY.search(info.get("label") or ""):
        return info
    extra = " ".join(x for x in (info.get("testid"), info.get("ctx")) if x)
    extra = re.sub(r"[^A-Za-z0-9]+", " ", _CAMEL.sub(r"\1 \2", extra)).strip()
    if extra and RISKY.search(extra):
        return {**info, "label": f"{info.get('label') or ''} ({extra})".strip()}
    return info


class Browser:
    def __init__(self, cfg, gate: ConfirmationGate):
        self.cfg = cfg
        self.gate = gate
        self.pw = None
        self.ctx = None
        self._page: Page | None = None
        self.mx = 0.0
        self.my = 0.0

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.pw = await async_playwright().start()
        kwargs = dict(
            user_data_dir=str(self.cfg.profile_dir),
            headless=self.cfg.headless,
            no_viewport=not self.cfg.headless,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )
        if self.cfg.headless:
            kwargs["viewport"] = {"width": 1366, "height": 850}
        if self.cfg.browser_channel:
            kwargs["channel"] = self.cfg.browser_channel
        self.ctx = await self.pw.chromium.launch_persistent_context(**kwargs)
        await self.ctx.add_init_script(JS.CURSOR_INIT)
        for p in self.ctx.pages:
            self._watch(p)
        self._page = self.ctx.pages[0] if self.ctx.pages else await self._new_page()
        if self.cfg.start_url:
            await self.navigate(self.cfg.start_url)

    async def close(self) -> None:
        try:
            if self.ctx:
                await self.ctx.close()
        finally:
            if self.pw:
                await self.pw.stop()

    def _watch(self, page: Page) -> None:
        page.on("popup", self._on_popup)

    def _on_popup(self, popup: Page) -> None:
        # Links with target=_blank: follow them, like a person would.
        self._watch(popup)
        self._page = popup

    async def _new_page(self) -> Page:
        p = await self.ctx.new_page()
        self._watch(p)
        return p

    @property
    def page(self) -> Page:
        return self._page

    async def _ensure_page(self) -> Page:
        if self._page is None or self._page.is_closed():
            open_pages = [p for p in self.ctx.pages if not p.is_closed()]
            self._page = open_pages[-1] if open_pages else await self._new_page()
        return self._page

    # ------------------------------------------------------------------ perception
    async def snapshot(self, limit: int = 80) -> dict:
        page = await self._ensure_page()
        return await page.evaluate(JS.SNAPSHOT, limit)

    @staticmethod
    def format_snapshot(s: dict) -> str:
        lines = [f"Page: {s['title'] or '(untitled)'}", f"URL: {s['url']}"]
        if s["maxScroll"] > 0:
            pct = round(100 * s["scrollY"] / s["maxScroll"])
            lines.append(f"Scrolled {pct}% (more content {'below' if pct < 98 else 'above'}).")
        lines.append("Visible interactive elements [id] (ids valid until the page changes):")
        for e in s["elements"]:
            kind = e["tag"] + (f":{e['type']}" if e.get("type") else "")
            line = f"[{e['id']}] {kind} \"{e['label']}\""
            if e.get("href"):
                line += f" -> {e['href']}"
            if "value" in e:
                line += f" value=\"{e['value']}\""
            if "checked" in e:
                line += " (checked)" if e["checked"] else " (unchecked)"
            if e.get("disabled"):
                line += " (disabled)"
            lines.append(line)
        if not s["elements"]:
            lines.append("(none visible — try look, scroll, or wait)")
        if s.get("truncated"):
            lines.append("(list truncated)")
        return "\n".join(lines)

    async def state(self, limit: int = 60) -> str:
        await self._settle()
        try:
            return self.format_snapshot(await self.snapshot(limit))
        except Exception as e:  # page mid-navigation
            await asyncio.sleep(1.0)
            try:
                return self.format_snapshot(await self.snapshot(limit))
            except Exception:
                return f"(page still loading: {e.__class__.__name__}; call observe again)"

    async def look(self, annotate: bool = True, limit: int = 80) -> tuple[str, str]:
        page = await self._ensure_page()
        snap = await self.snapshot(limit)
        if annotate:
            await page.evaluate(JS.ANNOTATE)
        try:
            img = await page.screenshot(type="jpeg", quality=60, scale="css")
        finally:
            if annotate:
                await page.evaluate(JS.CLEAR_ANNOTATE)
        vw, vh = snap["viewport"]
        text = (f"Screenshot attached ({vw}x{vh} CSS px; pink numbers = element ids). "
                "Coordinates in the image are valid for click_at/move_cursor.\n" + self.format_snapshot(snap))
        return text, base64.b64encode(img).decode()

    async def read_page(self, mode: str = "main", start: int = 0, max_chars: int = 6000) -> str:
        page = await self._ensure_page()
        r = await page.evaluate(JS.PAGE_TEXT, mode)
        text = r["text"]
        chunk = text[start:start + max_chars]
        more = len(text) - (start + len(chunk))
        tail = f"\n[...{more} more chars; call read_page with start={start + len(chunk)}]" if more > 0 else "\n[end of page text]"
        return f"Page: {r['title']}\nURL: {r['url']}\n---\n{chunk}{tail}"

    # ------------------------------------------------------------------ cursor
    async def _viewport(self) -> tuple[int, int]:
        page = await self._ensure_page()
        return tuple(await page.evaluate("() => [innerWidth, innerHeight]"))

    async def _move(self, x: float, y: float) -> None:
        page = await self._ensure_page()
        dist = math.hypot(x - self.mx, y - self.my)
        steps = max(6, min(40, int(dist / 30)))
        await page.mouse.move(x, y, steps=steps)
        self.mx, self.my = x, y
        try:
            await page.evaluate(JS.SET_CURSOR, [x, y])
        except Exception:
            pass

    def _loc(self, element_id: int):
        return self.page.locator(f'[data-agent-id="{int(element_id)}"]').first

    async def _center(self, element_id: int):
        loc = self._loc(element_id)
        if await loc.count() == 0:
            return None, None
        try:
            await loc.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        box = await loc.bounding_box()
        if not box:
            return loc, None
        return loc, (box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)

    @staticmethod
    def _missing(eid) -> str:
        return f"Element [{eid}] no longer exists (page changed). Call observe and use the new ids."

    async def _settle(self) -> None:
        page = await self._ensure_page()
        await asyncio.sleep(0.35)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=8000)
        except Exception:
            pass
        await asyncio.sleep(0.25)

    # ------------------------------------------------------------------ navigation
    async def navigate(self, url: str) -> str:
        page = await self._ensure_page()
        url = url.strip()
        if not re.match(r"^[a-z]+:", url, re.I):
            url = ("https://" + url) if DOMAIN_RE.match(url) else SEARCH_URLS["google"].format(quote_plus(url))
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            return f"Navigation problem: {e.__class__.__name__}: {str(e)[:200]}\n" + await self.state()
        return await self.state()

    async def search_web(self, query: str, engine: str = "google") -> str:
        tpl = SEARCH_URLS.get(engine.lower(), SEARCH_URLS["google"])
        return await self.navigate(tpl.format(quote_plus(query)))

    async def history(self, action: str) -> str:
        page = await self._ensure_page()
        if action == "back":
            await page.go_back(wait_until="domcontentloaded")
        elif action == "forward":
            await page.go_forward(wait_until="domcontentloaded")
        else:
            await page.reload(wait_until="domcontentloaded")
        return await self.state()

    async def tabs(self, action: str = "list", index: int | None = None, url: str | None = None) -> str:
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        if action == "new":
            self._page = await self._new_page()
            await self._page.bring_to_front()
            return await self.navigate(url) if url else "Opened a new blank tab."
        if action in ("switch", "close"):
            if index is None or not (0 <= index < len(pages)):
                return f"Invalid tab index. There are {len(pages)} tabs (0-based)."
            if action == "switch":
                self._page = pages[index]
                await self._page.bring_to_front()
                return await self.state()
            target = pages[index]
            await target.close()
            await self._ensure_page()
            await self.page.bring_to_front()
            return "Closed tab.\n" + await self.state()
        lines = []
        for i, p in enumerate(pages):
            mark = " (active)" if p is self._page else ""
            try:
                title = await p.title()
            except Exception:
                title = "?"
            lines.append(f"{i}: {title[:70]} — {p.url[:90]}{mark}")
        return "Tabs:\n" + "\n".join(lines)

    # ------------------------------------------------------------------ actions
    async def click(self, element_id: int, confirmed: bool = False, double: bool = False,
                    button: str = "left") -> str:
        loc, center = await self._center(element_id)
        if loc is None:
            return self._missing(element_id)
        info = _widen(await loc.evaluate(JS.DESCRIBE))
        blocked = await self.gate.check(f"{self.page.url}|{info['label']}", info, confirmed)
        if blocked:
            return blocked
        if center:
            await self._move(*center)
            await asyncio.sleep(0.12)
            await self.page.mouse.click(*center, button=button, click_count=2 if double else 1)
        else:
            await loc.click(timeout=5000, button=button, click_count=2 if double else 1)
        return f"Clicked [{element_id}] {info['tag']} \"{info['label']}\".\n" + await self.state()

    async def click_at(self, x: float, y: float, confirmed: bool = False, double: bool = False) -> str:
        page = await self._ensure_page()
        info = _widen(await page.evaluate(JS.DESCRIBE_AT_POINT, [x, y]))
        blocked = await self.gate.check(f"{page.url}|{(info or {}).get('label', '')}", info, confirmed)
        if blocked:
            return blocked
        await self._move(x, y)
        await asyncio.sleep(0.12)
        await page.mouse.click(x, y, click_count=2 if double else 1)
        what = f"{info['tag']} \"{info['label']}\"" if info else "empty area"
        return f"Clicked at ({int(x)},{int(y)}) on {what}.\n" + await self.state()

    async def move_cursor(self, element_id: int | None = None, x: float | None = None,
                          y: float | None = None) -> str:
        if element_id is not None:
            loc, center = await self._center(element_id)
            if not center:
                return self._missing(element_id)
            x, y = center
        if x is None or y is None:
            return "Give element_id or x and y."
        await self._move(x, y)
        return f"Cursor at ({int(x)},{int(y)})."

    async def hover(self, element_id: int) -> str:
        r = await self.move_cursor(element_id=element_id)
        if r.startswith("Element"):
            return r
        await asyncio.sleep(0.6)
        return f"Hovering [{element_id}].\n" + await self.state()

    async def type_text(self, element_id: int, text: str, press_enter: bool = False,
                        clear: bool = True) -> str:
        loc, center = await self._center(element_id)
        if loc is None:
            return self._missing(element_id)
        info = await loc.evaluate(JS.DESCRIBE)
        if info["sensitive"]:
            return ("BLOCKED: this looks like a password/payment/ID field. Ask the user to type it themselves, "
                    "then continue once they say it's done.")
        if info["tag"] == "select":
            return "That's a dropdown — use select_option instead."
        if center:
            await self._move(*center)
            await self.page.mouse.click(*center)
        else:
            await loc.click(timeout=5000)
        if clear:
            try:
                await loc.fill("")
            except Exception:
                await self.page.keyboard.press("Control+A")
                await self.page.keyboard.press("Backspace")
        await self.page.keyboard.type(text, delay=15)
        msg = f"Typed into [{element_id}] \"{info['label']}\"."
        if press_enter:
            r = await self.press_key("Enter")
            if r.startswith("CONFIRMATION_REQUIRED"):
                return msg + " Did NOT press Enter because it would submit the form. " + r
            return msg + " Pressed Enter.\n" + r.split("\n", 1)[-1]
        return msg + "\n" + await self.state()

    async def select_option(self, element_id: int, option: str) -> str:
        loc = self._loc(element_id)
        if await loc.count() == 0:
            return self._missing(element_id)
        info = await loc.evaluate(JS.DESCRIBE)
        if info["tag"] != "select":
            return ("Not a native dropdown. Click it to open it, observe, then click the option you want.")
        try:
            await loc.select_option(label=option, timeout=3000)
        except Exception:
            try:
                await loc.select_option(value=option, timeout=3000)
            except Exception:
                opts = await loc.evaluate("el => Array.from(el.options).map(o => o.text.trim()).slice(0, 40)")
                return f"Option '{option}' not found. Available: {opts}"
        return f"Selected '{option}' in [{element_id}].\n" + await self.state()

    async def fill_form(self, fields: list[dict]) -> str:
        results = []
        for f in fields:
            eid, value = f.get("element_id"), str(f.get("value", ""))
            loc = self._loc(eid)
            if await loc.count() == 0:
                results.append(f"[{eid}] missing")
                continue
            info = await loc.evaluate(JS.DESCRIBE)
            if info["sensitive"]:
                results.append(f"[{eid}] \"{info['label']}\" SKIPPED (sensitive — user must type it)")
                continue
            try:
                await loc.scroll_into_view_if_needed(timeout=2000)
                box = await loc.bounding_box()
                if box:
                    await self._move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                if info["tag"] == "select":
                    try:
                        await loc.select_option(label=value, timeout=3000)
                    except Exception:
                        await loc.select_option(value=value, timeout=3000)
                elif info["type"] in ("checkbox", "radio") or info["role"] in ("checkbox", "radio", "switch"):
                    want = value.strip().lower() in {"true", "yes", "on", "checked", "1", "check", "select"}
                    await loc.set_checked(want, timeout=3000)
                else:
                    await loc.fill(value, timeout=4000)
                results.append(f"[{eid}] \"{info['label']}\" = {value!r}")
            except Exception as e:
                results.append(f"[{eid}] \"{info['label']}\" FAILED ({e.__class__.__name__})")
        return "Form fields (NOT submitted):\n" + "\n".join(results) + "\n" + await self.state()

    async def scroll(self, direction: str = "down", amount: float = 1.0, element_id: int | None = None) -> str:
        page = await self._ensure_page()
        if direction in ("top", "bottom"):
            await page.evaluate("d => scrollTo(0, d === 'top' ? 0 : document.documentElement.scrollHeight)", direction)
            await page.keyboard.press("Home" if direction == "top" else "End")
            return await self.state()
        vw, vh = await self._viewport()
        if element_id is not None:
            _, c = await self._center(element_id)
            if c:
                await self._move(*c)
        elif not (0 < self.mx < vw and 0 < self.my < vh):
            await self._move(vw / 2, vh / 2)
        dy = vh * 0.8 * max(0.1, float(amount))
        dx = 0
        if direction == "up":
            dy = -dy
        elif direction in ("left", "right"):
            dx, dy = (dy if direction == "right" else -dy), 0
        await page.mouse.wheel(dx, dy)
        await asyncio.sleep(0.5)
        return await self.state()

    async def find_text(self, text: str) -> str:
        page = await self._ensure_page()
        loc = page.get_by_text(text, exact=False)
        n = await loc.count()
        if n == 0:
            return f"'{text}' not found on this page."
        first = loc.first
        try:
            await first.scroll_into_view_if_needed(timeout=3000)
            await first.evaluate(JS.FLASH)
            box = await first.bounding_box()
            if box:
                await self._move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        except Exception:
            pass
        return f"Found {n} match(es) for '{text}'; scrolled to the first.\n" + await self.state()

    async def press_key(self, key: str, confirmed: bool = False) -> str:
        page = await self._ensure_page()
        parts = [p.strip().lower() for p in key.split("+")] if len(key) > 1 else [key.lower()]
        base, mods = (parts[-1] or "+"), set(parts[:-1])
        is_enter = base in ("enter", "return", "numpadenter")
        if is_enter and mods & {"control", "ctrl", "meta", "cmd", "command", "controlormeta"}:
            # Ctrl/Cmd+Enter is the "send/post" shortcut in composers (X etc.): always gate it.
            f = await page.evaluate(JS.FOCUSED_DESCRIBE) or {}
            info = {"tag": "composer", "type": "submit", "isSubmit": True, "sensitive": False,
                    "label": f"send/post via {key} (" + (f.get("label") or f.get("tag") or "focused element") + ")"}
            blocked = await self.gate.check(f"{page.url}|{info['label']}", info, confirmed)
            if blocked:
                return blocked
        elif is_enter:
            # Enter inside a form submits it: gate it like clicking that form's submit button.
            info = await page.evaluate(JS.FOCUSED_FORM_SUBMIT)
            if info:
                blocked = await self.gate.check(f"{page.url}|{info['label']}", info, confirmed)
                if blocked:
                    return blocked
        if (is_enter or base in ("space", " ")) and not mods:
            # Enter/Space on a focused button activates it: gate like a click on it.
            info = _widen(await page.evaluate(JS.FOCUSED_DESCRIBE))
            if info and (info["tag"] == "button" or info["role"] == "button"):
                blocked = await self.gate.check(f"{page.url}|{info['label']}", info, confirmed)
                if blocked:
                    return blocked
        await page.keyboard.press(key)
        return f"Pressed {key}.\n" + await self.state()

    async def wait(self, seconds: float = 2.0) -> str:
        await asyncio.sleep(max(0.2, min(float(seconds), 20)))
        return await self.state()
