"""Shared local server for the mock pages (Navi QA).

Serves every folder under tests/mockpages/ on 127.0.0.1 only, keeps one event log
per server, and answers the X-shaped mock API paths. Nothing here ever contacts
another host.

    python -m tests.mockpages.server          # serves on a free port, prints the url

Endpoints:
    GET  /<page>/...                static files from tests/mockpages/<page>/
    GET  /common/...                shared files (log.js)
    GET  /__log                     JSON list of every event so far
    POST /__event                   a client-side event {type, id, ...}
    POST /__reset                   clear the log
    GET  /__fixture                 known names for read-aloud tests
    POST /i/api/graphql/mock/CreateTweet   body {text, in_reply_to}
    POST /i/api/graphql/mock/DeleteTweet   body {id}
    POST /i/api/1.1/dm/new                 body {recipient, text}
The header X-Mock-Variant selects behaviour (post_error_toast makes CreateTweet fail with 500).
"""
from __future__ import annotations

import json
import mimetypes
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent

API_PATHS = {
    "/i/api/graphql/mock/CreateTweet": "CreateTweet",
    "/i/api/graphql/mock/DeleteTweet": "DeleteTweet",
    "/i/api/1.1/dm/new": "dm_new",
}

FIXTURE = {
    "account": "NASA",
    "reply_authors": [
        "Ada Lovelace", "Grace Hopper", "Katherine Johnson", "Margaret Hamilton",
        "Mae Jemison", "Sally Ride", "Carl Sagan", "Neil deGrasse Tyson",
    ],
    "virtualized_reply_count": 60,
}


class MockState:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = []
        self.t0 = time.monotonic()
        self.counter = 0

    def add(self, event: dict):
        with self.lock:
            self.counter += 1
            event = dict(event)
            event["seq"] = self.counter
            event["ms"] = round((time.monotonic() - self.t0) * 1000, 1)
            self.events.append(event)
        return event

    def snapshot(self):
        with self.lock:
            return list(self.events)

    def reset(self):
        with self.lock:
            self.events.clear()
            self.t0 = time.monotonic()


def _make_handler(state: MockState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "NaviMock/1"

        def log_message(self, *args):  # keep test output quiet
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode())

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(n) if n else b""
            try:
                return json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                return {"_raw": raw.decode(errors="replace")}

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/__log":
                return self._json(200, state.snapshot())
            if path == "/__fixture":
                return self._json(200, FIXTURE)
            if path.endswith("/"):
                path += "index.html"
            rel = Path(path.lstrip("/"))
            target = (ROOT / rel).resolve()
            if ROOT not in target.parents or not target.is_file() or target.suffix == ".py":
                return self._json(404, {"error": "not found"})
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            return self._send(200, target.read_bytes(), ctype)

        def do_POST(self):
            path = urlparse(self.path).path
            body = self._body()
            if path == "/__event":
                state.add({"source": "client", **body})
                return self._json(200, {"ok": True})
            if path == "/__reset":
                state.reset()
                return self._json(200, {"ok": True})
            if path in API_PATHS:
                kind = API_PATHS[path]
                variant = self.headers.get("X-Mock-Variant", "")
                state.add({"source": "server", "type": "request", "method": "POST",
                           "path": path, "kind": kind, "body": body, "variant": variant})
                if kind == "CreateTweet" and variant == "post_error_toast":
                    return self._json(500, {"errors": [{"message": "Something went wrong"}]})
                return self._json(200, {"ok": True, "kind": kind, "id": f"mock-{state.counter}"})
            return self._json(404, {"error": "not found"})

    return Handler


class MockServer:
    def __init__(self, port: int = 0):
        self.state = MockState()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _make_handler(self.state))
        self.port = self.httpd.server_address[1]
        self._thread = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self._thread:
            self._thread.join(timeout=5)

    def log(self):
        return self.state.snapshot()

    def requests(self, kind=None):
        return [e for e in self.log() if e.get("source") == "server" and (kind is None or e.get("kind") == kind)]

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()


if __name__ == "__main__":
    with MockServer() as s:
        print(s.url, flush=True)
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
