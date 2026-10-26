"""Gate tests: Ctrl/Cmd+Enter in a composer and div role=button Post/Reply/Delete. Local page only.
Run: python test_gate_buttons.py   (or pytest test_gate_buttons.py)"""
import asyncio, sys, tempfile, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from browser import Browser
from safety import ConfirmationGate
from tools import ToolRegistry

HTML = """<!doctype html><html><head><title>Mock composer</title></head><body style="font-family:sans-serif">
<div id=composer contenteditable=true role=textbox aria-label="Compose text" style="border:1px solid #888;width:400px;height:60px"></div>
<div role=button tabindex=0 data-testid="tweetButton" id=post>Post</div>
<div role=button tabindex=0 data-testid="dmComposerSendButton" id=icon style="width:40px;height:30px;background:#ccc"></div>
<div role=button tabindex=0 aria-label="Delete" id=del>Del</div>
<div role=button tabindex=0 id=more>Show more</div>
<pre id=log></pre>
<script>
window.sent = 0;
const hit = n => { window.sent++; document.getElementById('log').textContent += n + ';'; };
post.onclick = () => hit('post'); icon.onclick = () => hit('icon'); del.onclick = () => hit('del');
more.onclick = () => document.getElementById('log').textContent += 'more;';
for (const b of [post, icon, del, more]) b.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') b.click(); };
composer.onkeydown = e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); hit('ctrl-enter'); } };
</script></body></html>"""

HTML2 = """<!doctype html><html><head><title>Mock form</title></head><body>
<form id=f onsubmit="event.preventDefault(); window.sent++">
 <input id=q name=q aria-label="Note"><textarea id=ta aria-label="Long note"></textarea>
 <input type=submit id=sub value="Place order">
</form>
<form onsubmit="event.preventDefault(); window.sent++"><input id=q2 aria-label="Other"><input type=image id=img alt="Send" width=20 height=20
 src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"></form>
<input type=button id=ib value="Post" onclick="window.sent++">
<div role=menuitem tabindex=0 id=mi aria-label="Delete" onclick="window.sent++" onkeydown="if(event.key=='Enter'||event.key==' ')this.click()">Del</div>
<script>window.sent = 0;</script></body></html>"""


class Cfg:
    headless = True
    browser_channel = None
    start_url = ""
    profile_dir = pathlib.Path(tempfile.mkdtemp())
    output_dir = pathlib.Path(tempfile.mkdtemp())


def find(state, label):
    for line in state.splitlines():
        if label in line and line.startswith("["):
            return int(line[1:line.index("]")])
    raise AssertionError(f"{label!r} not in:\n{state}")


async def main():
    gate = ConfirmationGate()
    b = Browser(Cfg, gate)
    await b.start()
    try:
        f = Cfg.output_dir / "composer.html"; f.write_text(HTML)
        reg = ToolRegistry(b, None, None, Cfg.output_dir)
        call = lambda n, a: reg.call(n, a)
        sent = lambda: b.page.evaluate("window.sent")
        gated = lambda r: r.text.startswith("CONFIRMATION_REQUIRED")
        s = (await call("navigate", {"url": f.as_uri()})).text
        comp, post, mo = find(s, "Compose text"), find(s, '"Post"'), find(s, "Show more")

        # 1. click on div role=button "Post": gated, self-confirm not enough, user yes lets it through
        assert gated(await call("click", {"element_id": post})) and await sent() == 0
        assert gated(await call("click", {"element_id": post, "confirmed": True})) and await sent() == 0
        gate.note_user("confirm")
        r = await call("click", {"element_id": post, "confirmed": True})
        assert r.text.startswith("Clicked") and await sent() == 1, r.text[:80]

        # 2. icon-only role=button with data-testid=dmComposerSendButton: gated via testid, by id and by point
        icon = (await b.page.evaluate("(() => { const r = icon.getBoundingClientRect(); return [r.x + r.width/2, r.y + r.height/2]; })()"))
        s = (await call("observe", {})).text
        assert gated(await call("click_at", {"x": icon[0], "y": icon[1]})) and await sent() == 1
        assert gated(await call("click", {"element_id": find(s, "div:button \"\"")})) and await sent() == 1
        # 3. aria-label Delete
        assert gated(await call("click", {"element_id": find(s, '"Delete"')})) and await sent() == 1

        # 4. Ctrl+Enter / Meta+Enter / aliases inside the composer: gated, nothing sent
        await call("type_text", {"element_id": comp, "text": "hello"})
        for k in ("Control+Enter", "Meta+Enter", "Ctrl+Enter", "ControlOrMeta+Enter", "Control+Shift+Enter"):
            r = await call("press_key", {"key": k})
            assert gated(r), (k, r.text[:80])
            assert await sent() == 1, k
        r = await call("press_key", {"key": "Control+Enter", "confirmed": True})
        assert gated(r) and await sent() == 1, "self-confirm must not pass"
        # 5. plain Enter in the composer is not a submit (newline) and Ctrl+A is not gated
        r = await call("press_key", {"key": "Enter"})
        assert not gated(r) and await sent() == 1, r.text[:80]
        assert not gated(await call("press_key", {"key": "Control+A"}))
        # 6. after the user confirms, Ctrl+Enter goes through exactly once
        await call("press_key", {"key": "Control+Enter"})
        gate.note_user("confirm")
        r = await call("press_key", {"key": "Control+Enter", "confirmed": True})
        assert r.text.startswith("Pressed") and await sent() == 2, r.text[:80]

        # 7. keyboard activation of a focused Post button is gated; safe button is not
        await b.page.evaluate("post.focus()")
        r = await call("press_key", {"key": "Enter"})
        assert gated(r) and await sent() == 2, r.text[:80]
        await b.page.evaluate("more.focus()")
        assert not gated(await call("press_key", {"key": "Enter"}))
        r = await call("click", {"element_id": mo})
        assert r.startswith("Clicked") if isinstance(r, str) else r.text.startswith("Clicked")
        print("GATE BUTTON TESTS PASSED")
    finally:
        await b.close()


async def main2():
    gate = ConfirmationGate()
    b = Browser(Cfg, gate)
    await b.start()
    try:
        f = Cfg.output_dir / "form.html"; f.write_text(HTML2)
        reg = ToolRegistry(b, None, None, Cfg.output_dir)
        call = lambda n, a: reg.call(n, a)
        sent = lambda: b.page.evaluate("window.sent")
        gated = lambda r: r.text.startswith("CONFIRMATION_REQUIRED")
        s = (await call("navigate", {"url": f.as_uri()})).text
        note, ta, other = find(s, '"Note"'), find(s, '"Long note"'), find(s, '"Other"')
        # newline characters in type_text must never act as Enter/submit
        for text in ("hello\n", "a\r\nb", "x\r"):
            for eid in (note, other):
                r = await call("type_text", {"element_id": eid, "text": text})
                assert await sent() == 0, (text, eid, r.text[:80])
        await call("type_text", {"element_id": ta, "text": "line1\nline2"})
        assert await b.page.evaluate("ta.value") == "line1\nline2" and await sent() == 0
        # press_key with a raw newline / carriage return is Enter (and Ctrl+Enter)
        await b.page.evaluate("q.focus()")
        for k in ("\n", "\r", "Control+\n"):
            r = await call("press_key", {"key": k})
            assert gated(r) and await sent() == 0, (k, r.text[:80])
        # Space/Enter on focused input[type=submit|button|image] and role=menuitem
        for sel in ("sub", "ib", "img", "mi"):
            for k in (" ", "Space", "Enter"):
                await b.page.evaluate(f"{sel}.focus()")
                r = await call("press_key", {"key": k})
                assert gated(r) and await sent() == 0, (sel, k, r.text[:80])
        print("GATE FORM TESTS PASSED")
    finally:
        await b.close()


def test_gate_newline_and_input_buttons():
    asyncio.run(main2())


def test_default_channel_is_chrome(monkeypatch=None):
    import os, config
    old = {k: os.environ.get(k) for k in ("BROWSER_CHANNEL", "OPENAI_API_KEY", "PROFILE_DIR", "OUTPUT_DIR")}
    try:
        os.environ.update(OPENAI_API_KEY="x", PROFILE_DIR=tempfile.mkdtemp(), OUTPUT_DIR=tempfile.mkdtemp())
        os.environ.pop("BROWSER_CHANNEL", None)
        assert config.Config.load().browser_channel == "chrome"
        os.environ["BROWSER_CHANNEL"] = "msedge"
        assert config.Config.load().browser_channel == "msedge"
        os.environ["BROWSER_CHANNEL"] = ""
        assert config.Config.load().browser_channel is None
    finally:
        for k, v in old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_gate_buttons():
    asyncio.run(main())


if __name__ == "__main__":
    test_default_channel_is_chrome()
    asyncio.run(main())
    asyncio.run(main2())
