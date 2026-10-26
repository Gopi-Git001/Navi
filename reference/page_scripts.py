"""JavaScript injected into pages. Kept in one place so it's easy to tweak."""

# Visible red cursor that follows Playwright's (normally invisible) mouse.
CURSOR_INIT = r"""
(() => {
  if (window.__agentCursorInstalled) return;
  window.__agentCursorInstalled = true;
  const make = () => {
    if (document.getElementById('__agent_cursor')) return;
    const c = document.createElement('div');
    c.id = '__agent_cursor';
    c.style.cssText = 'position:fixed;left:-50px;top:-50px;width:18px;height:18px;margin:-9px 0 0 -9px;' +
      'border-radius:50%;background:rgba(235,40,40,.8);border:2px solid #fff;' +
      'box-shadow:0 0 8px rgba(0,0,0,.55);z-index:2147483647;pointer-events:none;transition:transform .08s';
    document.documentElement.appendChild(c);
    const last = window.__agentCursorPos;
    if (last) { c.style.left = last[0] + 'px'; c.style.top = last[1] + 'px'; }
    addEventListener('mousemove', e => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', () => { c.style.transform = 'scale(.55)'; }, true);
    addEventListener('mouseup', () => { c.style.transform = 'scale(1)'; }, true);
  };
  if (document.documentElement) make(); else document.addEventListener('DOMContentLoaded', make);
})();
"""

SET_CURSOR = r"""
([x, y]) => {
  window.__agentCursorPos = [x, y];
  const c = document.getElementById('__agent_cursor');
  if (c) { c.style.left = x + 'px'; c.style.top = y + 'px'; }
}
"""

# Numbered snapshot of visible, un-occluded interactive elements (pierces open shadow DOM).
SNAPSHOT = r"""
(maxItems) => {
  const SEL = ['a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea', 'summary',
    '[role=button]', '[role=link]', '[role=checkbox]', '[role=radio]', '[role=tab]', '[role=menuitem]',
    '[role=option]', '[role=switch]', '[role=combobox]', '[role=textbox]', '[role=searchbox]',
    '[contenteditable=""]', '[contenteditable=true]', '[onclick]'].join(',');
  const txt = s => (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim();
  const found = [];
  const walk = root => {
    root.querySelectorAll('[data-agent-id]').forEach(e => e.removeAttribute('data-agent-id'));
    root.querySelectorAll(SEL).forEach(e => found.push(e));
    root.querySelectorAll('*').forEach(e => { if (e.shadowRoot) walk(e.shadowRoot); });
  };
  walk(document);
  const composedContains = (a, b) => {
    let n = b;
    while (n) { if (n === a) return true; n = n.parentNode || (n.host || null); }
    return false;
  };
  const topAt = (x, y) => {
    let t = document.elementFromPoint(x, y);
    while (t && t.shadowRoot) {
      const inner = t.shadowRoot.elementFromPoint(x, y);
      if (!inner || inner === t) break;
      t = inner;
    }
    return t;
  };
  const labelOf = el => {
    let l = el.getAttribute('aria-label');
    const lb = el.getAttribute('aria-labelledby');
    if (!l && lb) l = lb.split(/\s+/).map(id => { const n = document.getElementById(id); return n ? n.innerText : ''; }).join(' ');
    if (!l && el.labels && el.labels.length) l = el.labels[0].innerText;
    const isField = ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName);
    if (!l && !isField) l = el.innerText;
    if (!l) {
      const img = el.querySelector && el.querySelector('img[alt]');
      l = el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') ||
          (img && img.getAttribute('alt')) || el.getAttribute('name') ||
          (['submit', 'button', 'reset'].includes(el.type) ? el.value : '');
    }
    return txt(l).slice(0, 80);
  };
  const vw = innerWidth, vh = innerHeight;
  const out = [], els = [];
  const seen = new Set();
  for (const el of found) {
    if (seen.has(el)) continue; seen.add(el);
    const r = el.getBoundingClientRect();
    if (r.width < 3 || r.height < 3) continue;
    if (r.bottom <= 0 || r.top >= vh || r.right <= 0 || r.left >= vw) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) === 0) continue;
    const cx = Math.min(Math.max(r.left + r.width / 2, 1), vw - 2);
    const cy = Math.min(Math.max(r.top + r.height / 2, 1), vh - 2);
    const t = topAt(cx, cy);
    if (t && t !== el && !composedContains(el, t) && !composedContains(t, el)) continue;
    const id = out.length + 1;
    el.setAttribute('data-agent-id', String(id));
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || el.getAttribute('role') || '').toLowerCase();
    const item = { id, tag, type, label: labelOf(el), x: Math.round(cx), y: Math.round(cy) };
    if (tag === 'a') item.href = (el.getAttribute('href') || '').slice(0, 90);
    if (tag === 'select') item.value = txt(el.options[el.selectedIndex] && el.options[el.selectedIndex].text).slice(0, 40);
    else if ((tag === 'input' || tag === 'textarea') && type !== 'password') {
      if (type === 'checkbox' || type === 'radio') item.checked = el.checked;
      else if (el.value) item.value = txt(el.value).slice(0, 40);
    }
    if (el.disabled) item.disabled = true;
    out.push(item); els.push(el);
    if (out.length >= maxItems) break;
  }
  window.__agentEls = els;
  const de = document.documentElement;
  return { url: location.href, title: document.title, scrollY: Math.round(scrollY),
           maxScroll: Math.max(0, de.scrollHeight - vh), viewport: [vw, vh],
           truncated: out.length >= maxItems, elements: out };
}
"""

# Draw numbered boxes over the snapshot elements (for screenshots the model looks at).
ANNOTATE = r"""
() => {
  const els = window.__agentEls || [];
  let box = document.getElementById('__agent_marks');
  if (box) box.remove();
  box = document.createElement('div');
  box.id = '__agent_marks';
  box.style.cssText = 'position:fixed;inset:0;pointer-events:none;z-index:2147483646';
  els.forEach((el, i) => {
    const r = el.getBoundingClientRect();
    const b = document.createElement('div');
    b.style.cssText = `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;height:${r.height}px;` +
      'outline:2px solid rgba(255,0,140,.85);';
    const t = document.createElement('div');
    t.textContent = String(i + 1);
    t.style.cssText = `position:fixed;left:${Math.max(0, r.left)}px;top:${Math.max(0, r.top - 14)}px;` +
      'background:#ff008c;color:#fff;font:bold 11px/14px sans-serif;padding:0 3px;border-radius:2px';
    box.appendChild(b); box.appendChild(t);
  });
  document.documentElement.appendChild(box);
}
"""

CLEAR_ANNOTATE = "() => { const b = document.getElementById('__agent_marks'); if (b) b.remove(); }"

# Facts about one element, used for safety gating and routing.
DESCRIBE = r"""
(el) => {
  const txt = s => (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim();
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  let label = el.getAttribute('aria-label') || (el.labels && el.labels[0] && el.labels[0].innerText) ||
    (['input', 'textarea', 'select'].includes(tag) ? '' : el.innerText) || el.value ||
    el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('alt') || el.getAttribute('name') || '';
  label = txt(label).slice(0, 120);
  const ac = (el.getAttribute('autocomplete') || '').toLowerCase();
  const hay = ((el.getAttribute('name') || '') + ' ' + (el.id || '') + ' ' + label + ' ' +
               (el.getAttribute('placeholder') || '')).toLowerCase();
  const sensitive = type === 'password' || ac.startsWith('cc-') || ac.includes('password') ||
    /(card.?number|credit.?card|cvv|cvc|security.?code|\bssn\b|social.?security|routing.?number|account.?number|\bpin\b)/.test(hay);
  const btnType = tag === 'button' ? (el.getAttribute('type') || 'submit').toLowerCase() : type;
  const isSubmit = (tag === 'input' && type === 'submit') || (tag === 'button' && btnType === 'submit' && !!el.form);
  const bt = el.closest('[role=button],button,[type=submit]');
  const tid = x => (x && x.getAttribute('data-testid')) || '';
  const ctx = bt ? txt([bt.getAttribute('aria-label') || bt.innerText || '', tid(bt)].join(' ')).slice(0, 160) : '';
  return { tag, type, role: el.getAttribute('role') || '', label, sensitive, isSubmit,
           testid: tid(el), ctx,
           editable: el.isContentEditable || ['input', 'textarea'].includes(tag) };
}
"""

DESCRIBE_AT_POINT = r"""
([x, y]) => {
  let el = document.elementFromPoint(x, y);
  while (el && el.shadowRoot) { const i = el.shadowRoot.elementFromPoint(x, y); if (!i || i === el) break; el = i; }
  if (!el) return null;
  const c = el.closest('a,button,input,select,textarea,summary,label,[role=button],[role=link],[onclick]');
  if (!c) return { tag: el.tagName.toLowerCase(), type: '', label: '', isSubmit: false, sensitive: false };
  const txt = s => (s == null ? '' : String(s)).replace(/\s+/g, ' ').trim();
  const tag = c.tagName.toLowerCase();
  const type = (c.getAttribute('type') || '').toLowerCase();
  const label = txt(c.getAttribute('aria-label') || c.innerText || c.value || c.getAttribute('title') || '').slice(0, 120);
  const isSubmit = (tag === 'input' && type === 'submit') ||
    (tag === 'button' && (c.getAttribute('type') || 'submit') === 'submit' && !!c.form);
  return { tag, type, label, isSubmit, sensitive: false, testid: c.getAttribute('data-testid') || '' };
}
"""

# Facts about the focused element (for keyboard activation of buttons).
FOCUSED_DESCRIBE = "() => { let a = document.activeElement; " \
    "while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement; " \
    "return a ? (" + DESCRIBE.strip() + ")(a) : null; }"

FOCUSED_FORM_SUBMIT = r"""
() => {
  let a = document.activeElement;
  while (a && a.shadowRoot && a.shadowRoot.activeElement) a = a.shadowRoot.activeElement;
  const f = a && a.form;
  if (!f || a.tagName === 'TEXTAREA') return null;
  const b = f.querySelector('button[type=submit], input[type=submit], button:not([type])');
  const label = b ? (b.innerText || b.value || b.getAttribute('aria-label') || '') : '';
  return { tag: 'form', type: 'submit', label: label.replace(/\s+/g, ' ').trim().slice(0, 120) || 'submit form',
           isSubmit: true, sensitive: false };
}
"""

PAGE_TEXT = r"""
(mode) => {
  let root = document.body;
  if (mode === 'main') {
    const m = document.querySelector('article, main, [role=main], #content, .content');
    if (m && (m.innerText || '').length > 500) root = m;
  }
  const t = (root ? root.innerText : '') || '';
  return { title: document.title, url: location.href,
           text: t.replace(/[ \t ]+/g, ' ').replace(/\n\s*\n\s*\n+/g, '\n\n').trim() };
}
"""

TABLES = r"""
() => Array.from(document.querySelectorAll('table')).slice(0, 25).map(tb =>
  Array.from(tb.rows).slice(0, 1000).map(r => Array.from(r.cells).map(c => c.innerText.replace(/\s+/g, ' ').trim()))
).filter(t => t.length > 1)
"""

LINKS = r"""
(limit) => {
  const seen = new Set(), out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    const href = a.href;
    if (!href || href.startsWith('javascript:') || seen.has(href)) continue;
    const text = (a.innerText || a.getAttribute('aria-label') || a.title || '').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    seen.add(href); out.push({ text: text.slice(0, 120), url: href });
    if (out.length >= limit) break;
  }
  return out;
}
"""

FLASH = r"""
(el) => {
  const o = el.style.outline;
  el.style.outline = '3px solid #ff008c';
  setTimeout(() => { el.style.outline = o; }, 1200);
}
"""
