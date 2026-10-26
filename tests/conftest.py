"""Shared fixtures: a local mock-page server and a headless Chrome page.

Uses Playwright with the installed Chrome (`channel="chrome"`); `playwright` is already
in requirements.txt. Every request the page makes is recorded so tests can prove that
nothing leaves localhost.
"""
import pytest
from playwright.sync_api import sync_playwright

from tests.mockpages.server import MockServer


@pytest.fixture(scope="session")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch(channel="chrome", headless=True, args=["--no-sandbox"])
        yield b
        b.close()


@pytest.fixture()
def server():
    with MockServer() as s:
        yield s


@pytest.fixture()
def open_page(browser, server):
    """open_page('mock_x', variant='pinned_first') -> Playwright page. Records all requests in page.requested_urls."""
    pages = []

    def _open(name, variant="", extra=""):
        ctx = browser.new_context(viewport={"width": 1000, "height": 800})
        page = ctx.new_page()
        page.requested_urls = []
        page.on("request", lambda r: page.requested_urls.append(r.url))
        q = f"?variant={variant}" if variant else ""
        page.goto(f"{server.url}/{name}/index.html{q}{extra}")
        pages.append(ctx)
        return page

    yield _open
    for c in pages:
        c.close()
