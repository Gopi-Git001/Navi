import asyncio, sys, tempfile, pathlib, base64
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from browser import Browser
from safety import ConfirmationGate
from tools import ToolRegistry, TOOL_SCHEMAS
import scrape

HTML = """<!doctype html><html><head><title>Test Shop</title></head><body style="font-family:sans-serif">
<h1>Checkout form</h1>
<form onsubmit="event.preventDefault(); document.getElementById('res').textContent='SUBMITTED ' + document.getElementById('name').value">
 <label for=name>Full name</label> <input id=name name=name><br>
 <label for=pw>Password</label> <input id=pw type=password><br>
 <label for=cc>Card number</label> <input id=cc autocomplete=cc-number><br>
 <label for=size>Size</label> <select id=size><option>Small</option><option>Large</option></select><br>
 <label><input type=checkbox id=news> Newsletter</label><br>
 <button type=submit>Place order</button>
</form>
<div id=res></div>
<button onclick="document.getElementById('res').textContent='clicked more'">Show more</button>
<a href="https://example.com" target="_blank">Open example</a>
<my-widget></my-widget>
<table><tr><th>Item</th><th>Price</th></tr><tr><td>Hat</td><td>$10</td></tr><tr><td>Mug</td><td>$7</td></tr></table>
<div style="height:3000px"></div><p id=far>Deep text marker</p>
<script>customElements.define('my-widget', class extends HTMLElement{constructor(){super();
 const s=this.attachShadow({mode:'open'}); s.innerHTML='<button id=sb>Shadow button</button>';
 s.getElementById('sb').onclick=()=>document.getElementById('res').textContent='shadow clicked';}});</script>
</body></html>"""

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
    f = Cfg.output_dir / "t.html"; f.write_text(HTML)
    reg = ToolRegistry(b, None, None, Cfg.output_dir)
    s = (await reg.call("navigate", {"url": f.as_uri()})).text
    print(s); print("----")
    nm = find(s, '"Full name"')
    r = (await reg.call("type_text", {"element_id": nm, "text": "Revanth"})).text
    assert "Typed" in r, r
    r = (await reg.call("type_text", {"element_id": nm, "text": "Revanth", "press_enter": True})).text
    assert "Did NOT press Enter" in r, r
    assert "SUBMITTED" not in (await b.read_page("full")), "Enter submitted without confirmation"
    pw = find(s, '"Password"')
    r = (await reg.call("type_text", {"element_id": pw, "text": "secret"})).text
    assert r.startswith("BLOCKED"), r
    s = (await reg.call("observe", {})).text
    r = (await reg.call("fill_form", {"fields": [
        {"element_id": find(s, '"Size"'), "value": "Large"},
        {"element_id": find(s, '"Newsletter"'), "value": "yes"},
        {"element_id": find(s, '"Card number"'), "value": "4111"}]})).text
    print(r.split("\n")[0:4]); assert "SKIPPED" in r and "Large" in r
    s = (await reg.call("observe", {})).text
    order = find(s, '"Place order"')
    r = (await reg.call("click", {"element_id": order})).text
    assert r.startswith("CONFIRMATION_REQUIRED"), r
    r = (await reg.call("click", {"element_id": order, "confirmed": True})).text
    assert r.startswith("CONFIRMATION_REQUIRED"), "model self-confirm must not pass: " + r[:80]
    gate.note_user("yes, place it")
    r = (await reg.call("click", {"element_id": order, "confirmed": True})).text
    assert r.startswith("Clicked"), r
    assert "SUBMITTED Revanth" in (await b.read_page("full")), "form not submitted"
    s = (await reg.call("observe", {})).text
    r = (await reg.call("click", {"element_id": find(s, '"Show more"')})).text
    assert r.startswith("Clicked"), r
    r = (await reg.call("click", {"element_id": find(s, '"Shadow button"')})).text
    assert "shadow clicked" in (await b.read_page("full")), "shadow click failed"
    r = (await reg.call("scroll", {"direction": "down", "amount": 2})).text
    print(r.splitlines()[2]); assert "Scrolled" in r
    r = (await reg.call("find_text", {"text": "Deep text marker"})).text
    assert r.startswith("Found"), r
    res = await reg.call("look", {})
    assert res.image_b64 and len(base64.b64decode(res.image_b64)) > 1000
    (Cfg.output_dir / "shot.jpg").write_bytes(base64.b64decode(res.image_b64))
    r = (await reg.call("scrape_tables", {"save_as": "prices"})).text
    print(r); assert "2 rows" in r
    r = (await reg.call("scrape_links", {})).text
    assert "example.com" in r, r
    r = (await reg.call("tabs", {"action": "list"})).text
    print(r)
    r = (await reg.call("click_at", {"x": 5, "y": 5})).text
    assert r.startswith("Clicked at"), r
    r = (await reg.call("nonexistent", {})).text
    assert "Unknown" in r
    r = (await reg.call("click", {"element_id": 999})).text
    assert "no longer exists" in r, r
    print("schemas:", len(TOOL_SCHEMAS), "screenshot:", Cfg.output_dir / "shot.jpg")
    await b.close()
    print("ALL SMOKE TESTS PASSED")

asyncio.run(main())
