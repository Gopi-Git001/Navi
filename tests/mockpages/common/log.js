/* Shared logger for every mock page (Navi QA).
   Every click, key press, value change and request is pushed to window.__navi_log
   and posted to the local server at /__event. Nothing leaves localhost. */
(function () {
  window.__navi_log = window.__navi_log || [];
  var t0 = performance.now();
  function describe(el) {
    if (!el || !el.getAttribute) return {};
    var d = { id: el.id || null, tag: el.tagName ? el.tagName.toLowerCase() : null };
    var tid = el.getAttribute('data-testid'); if (tid) d.testid = tid;
    var risk = el.getAttribute('data-risk'); if (risk) d.risk = risk;
    var role = el.getAttribute('role'); if (role) d.role = role;
    return d;
  }
  function send(ev) {
    ev.ms = Math.round(performance.now() - t0);
    ev.page = location.pathname;
    window.__navi_log.push(ev);
    try {
      fetch('/__event', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(ev), keepalive: true });
    } catch (e) { /* the log must never break the page */ }
  }
  window.__navi_event = send;
  window.__navi_describe = describe;
  function target(e) {
    var el = e.target && e.target.closest ? e.target.closest('[id],[data-testid],button,a,[role=button],[role=menuitem]') : e.target;
    return el || e.target;
  }
  document.addEventListener('click', function (e) {
    var d = describe(target(e)); d.type = 'click'; d.x = Math.round(e.clientX); d.y = Math.round(e.clientY); send(d);
  }, true);
  document.addEventListener('keydown', function (e) {
    var d = describe(document.activeElement); d.type = 'key'; d.key = e.key;
    d.ctrl = e.ctrlKey; d.meta = e.metaKey; d.shift = e.shiftKey; send(d);
  }, true);
  document.addEventListener('input', function (e) {
    var el = e.target; var d = describe(el); d.type = 'input';
    d.value = el.value !== undefined ? el.value : el.textContent; send(d);
  }, true);
  window.__navi_fetch = function (path, body, headers) {
    send({ type: 'request_start', path: path, body: body });
    return fetch(path, { method: 'POST', headers: Object.assign({ 'Content-Type': 'application/json' }, headers || {}),
      body: JSON.stringify(body) }).then(function (r) {
      send({ type: 'response', path: path, status: r.status }); return r;
    });
  };
})();
