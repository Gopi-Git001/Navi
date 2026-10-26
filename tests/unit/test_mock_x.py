"""Tests for the mock X page (issue #7). The page must behave like X for the comment
task, log every event, and never contact another host."""
import json
import re
import urllib.request
from pathlib import Path

import pytest

from tests.mockpages.server import MockServer

HTML = Path(__file__).resolve().parents[1] / "mockpages" / "mock_x" / "index.html"


def go(page, selector, route):
    """Click a navigation link and wait until the page has switched to that route."""
    page.click(selector)
    page.wait_for_selector(f"body[data-route='{route}']")


def get_json(url):
    with urllib.request.urlopen(url) as r:
        return json.load(r)


def post_json(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def open_reply_on_first_nasa_post(page):
    go(page, "#nav-nasa", "/NASA")
    page.click("[data-testid=tweet] >> nth=0 >> [data-testid=reply]")
    page.fill("#composer-box", "Amazing photo")  # contenteditable: fill sets text and fires input


def type_reply(page, text="Amazing photo"):
    page.click("#composer-box")
    page.keyboard.type(text)


# ---------- server ----------

def test_server_starts_and_stops():
    s = MockServer().start()
    port = s.port
    assert get_json(f"{s.url}/__log") == []
    s.stop()
    with pytest.raises(Exception):
        urllib.request.urlopen(f"http://127.0.0.1:{port}/__log", timeout=1)


def test_server_only_listens_on_localhost(server):
    assert server.httpd.server_address[0] == "127.0.0.1"


def test_post_to_create_tweet_is_logged_exactly_once(server):
    status, body = post_json(f"{server.url}/i/api/graphql/mock/CreateTweet", {"text": "hi", "in_reply_to": "n1"})
    assert status == 200
    reqs = server.requests("CreateTweet")
    assert len(reqs) == 1
    assert reqs[0]["body"]["text"] == "hi"
    assert reqs[0]["method"] == "POST"


def test_x_shaped_paths_are_logged_by_kind(server):
    post_json(f"{server.url}/i/api/graphql/mock/DeleteTweet", {"id": "m1"})
    post_json(f"{server.url}/i/api/1.1/dm/new", {"recipient": "mockfriend", "text": "hello"})
    assert len(server.requests("DeleteTweet")) == 1
    assert len(server.requests("dm_new")) == 1


def test_unknown_path_is_404_and_not_logged(server):
    status, _ = post_json(f"{server.url}/i/api/graphql/mock/NoSuchThing", {})
    assert status == 404
    assert server.requests() == []


def test_server_does_not_serve_python_files_or_escape_the_folder(server):
    for path in ["/server.py", "/../server.py", "/mock_x/server.py", "/%2e%2e/server.py"]:
        try:
            urllib.request.urlopen(f"{server.url}{path}")
            raise AssertionError(f"{path} was served")
        except urllib.error.HTTPError as e:
            assert e.code == 404


def test_reset_clears_the_log(server):
    post_json(f"{server.url}/__event", {"type": "x"})
    assert get_json(f"{server.url}/__log")
    post_json(f"{server.url}/__reset", {})
    assert get_json(f"{server.url}/__log") == []


# ---------- nothing leaves localhost ----------

def test_html_has_no_external_urls():
    text = HTML.read_text()
    urls = re.findall(r"https?://[^\s\"'<>)]+", text)
    for u in urls:
        assert re.match(r"https?://(127\.0\.0\.1|localhost)", u) or u.startswith("http://www.w3.org/2000/svg"), u


@pytest.mark.parametrize("flow", ["browse", "reply", "dm", "delete"])
def test_page_makes_zero_requests_to_any_other_host(open_page, server, flow):
    page = open_page("mock_x")
    if flow == "browse":
        for sel in ["#nav-nasa", "#nav-search", "#nav-dm", "#nav-home"]:
            page.click(sel)
    elif flow == "reply":
        open_reply_on_first_nasa_post(page)
        page.click("#post-button")
    elif flow == "dm":
        go(page, "#nav-dm", "/messages/mockfriend")
        page.click("#dm-box"); page.keyboard.type("hello"); page.click("#dm-send")
    elif flow == "delete":
        page.click("[data-testid=caret] >> nth=0"); page.click("#menu-delete"); page.click("#delete-confirm")
    page.wait_for_timeout(300)
    assert page.requested_urls, "page made no requests at all"
    for u in page.requested_urls:
        assert u.startswith(server.url) or u.startswith("data:"), f"request left localhost: {u}"


# ---------- the comment task ----------

def test_reply_button_opens_a_composer(open_page):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA")
    assert page.locator("#composer-box").count() == 0
    page.click("[data-testid=tweet] >> nth=0 >> [data-testid=reply]")
    assert page.locator("#reply-modal #composer-box").count() == 1


def test_post_button_is_a_plain_div_role_button_not_a_form_submit(open_page):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA")
    page.click("[data-testid=tweet] >> nth=0 >> [data-testid=reply]")
    btn = page.locator("#post-button")
    assert btn.evaluate("e => e.tagName.toLowerCase()") == "div"
    assert btn.get_attribute("role") == "button"
    assert page.locator("form").count() == 0
    assert btn.get_attribute("data-risk") == "comment"


def test_post_button_is_disabled_until_there_is_text(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA")
    page.click("[data-testid=tweet] >> nth=0 >> [data-testid=reply]")
    assert page.get_attribute("#post-button", "aria-disabled") == "true"
    page.click("#post-button", force=True)  # aria-disabled: Playwright would otherwise wait forever
    page.wait_for_timeout(200)
    assert server.requests("CreateTweet") == []
    type_reply(page)
    assert page.get_attribute("#post-button", "aria-disabled") == "false"


def test_clicking_post_sends_one_create_tweet_with_text_and_target(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA")
    page.click("[data-testid=tweet] >> nth=0 >> [data-testid=reply]")
    type_reply(page)
    assert server.requests("CreateTweet") == [], "nothing may be sent before Post is pressed"
    page.click("#post-button")
    page.wait_for_selector("#toast:visible")
    reqs = server.requests("CreateTweet")
    assert len(reqs) == 1
    assert reqs[0]["body"]["text"] == "Amazing photo"
    assert reqs[0]["body"]["in_reply_to"] == "n1"


def test_double_click_on_post_does_not_send_twice(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA")
    page.click("[data-testid=tweet] >> nth=0 >> [data-testid=reply]")
    type_reply(page)
    page.dblclick("#post-button")
    page.wait_for_timeout(400)
    assert len(server.requests("CreateTweet")) == 1


def test_ctrl_enter_also_posts(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA")
    page.click("[data-testid=tweet] >> nth=0 >> [data-testid=reply]")
    type_reply(page)
    page.keyboard.press("Control+Enter")
    page.wait_for_timeout(300)
    reqs = server.requests("CreateTweet")
    assert len(reqs) == 1
    submit = [e for e in server.log() if e.get("type") == "submit"]
    assert submit[0]["via"] == "ctrl_enter"


def test_plain_enter_does_not_post(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA")
    page.click("[data-testid=tweet] >> nth=0 >> [data-testid=reply]")
    type_reply(page)
    page.keyboard.press("Enter")
    page.wait_for_timeout(300)
    assert server.requests("CreateTweet") == []


def test_home_composer_post_is_risk_post(open_page, server):
    page = open_page("mock_x")
    assert page.get_attribute("#post-button", "data-risk") == "post"
    page.click("#composer-box"); page.keyboard.type("Hello from Navi")
    page.click("#post-button"); page.wait_for_timeout(300)
    assert server.requests("CreateTweet")[0]["body"]["in_reply_to"] in (None, "")


def test_dm_send_posts_to_dm_new_once_and_enter_sends(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-dm", "/messages/mockfriend")
    assert page.get_attribute("#dm-send", "data-risk") == "dm"
    page.click("#dm-box"); page.keyboard.type("See you at five")
    page.click("#dm-send"); page.wait_for_timeout(300)
    reqs = server.requests("dm_new")
    assert len(reqs) == 1 and reqs[0]["body"] == {"recipient": "mockfriend", "text": "See you at five"}
    page.click("#dm-box"); page.keyboard.type("again"); page.keyboard.press("Enter"); page.wait_for_timeout(300)
    assert len(server.requests("dm_new")) == 2


def test_delete_needs_menu_then_dialog_confirm(open_page, server):
    page = open_page("mock_x")
    page.click("#more-m1")
    assert page.locator("#menu-delete").count() == 1
    page.click("#menu-delete")
    assert server.requests("DeleteTweet") == [], "menu item alone must not delete"
    assert page.get_attribute("#delete-confirm", "data-risk") == "delete"
    page.click("#delete-cancel"); page.wait_for_timeout(200)
    assert server.requests("DeleteTweet") == []
    page.click("#more-m1"); page.click("#menu-delete"); page.click("#delete-confirm")
    page.wait_for_timeout(300)
    reqs = server.requests("DeleteTweet")
    assert len(reqs) == 1 and reqs[0]["body"]["id"] == "m1"
    assert page.locator("#tweet-m1").count() == 0


def test_other_peoples_posts_have_no_delete_item(open_page):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA")
    page.click("[data-testid=caret] >> nth=0")
    assert page.locator("#menu-delete").count() == 0


def test_every_irreversible_control_carries_data_risk(open_page):
    page = open_page("mock_x")
    assert page.get_attribute("#post-button", "data-risk") == "post"
    go(page, "#nav-dm", "/messages/mockfriend")
    assert page.get_attribute("#dm-send", "data-risk") == "dm"
    go(page, "#nav-home", "/home"); page.click("#more-m1"); page.click("#menu-delete")
    assert page.get_attribute("#delete-confirm", "data-risk") == "delete"
    page.click("#delete-cancel")
    assert page.locator("[data-testid=reply]").first.get_attribute("data-risk") is None


# ---------- logging ----------

def test_clicks_keys_and_value_changes_are_logged_with_element_ids(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA")
    page.click("#reply-btn-n1")
    page.click("#composer-box"); page.keyboard.type("ab")
    page.wait_for_timeout(300)
    log = server.log()
    assert any(e.get("type") == "click" and e.get("id") == "reply-btn-n1" for e in log)
    assert any(e.get("type") == "key" and e.get("key") == "a" and e.get("id") == "composer-box" for e in log)
    assert any(e.get("type") == "input" and e.get("id") == "composer-box" and e.get("value") == "ab" for e in log)
    page_log = page.evaluate("window.__navi_log")
    assert any(e.get("type") == "click" and e.get("id") == "reply-btn-n1" for e in page_log)


def test_requests_are_logged_on_the_page_and_on_the_server(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA"); page.click("#reply-btn-n1"); type_reply(page); page.click("#post-button")
    page.wait_for_timeout(300)
    assert any(e.get("type") == "request_start" for e in page.evaluate("window.__navi_log"))
    assert len(server.requests("CreateTweet")) == 1


def test_log_survives_a_reload(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA"); page.click("#reply-btn-n1"); type_reply(page); page.click("#post-button")
    page.wait_for_timeout(300)
    before = len(get_json(f"{server.url}/__log"))
    page.reload(); page.wait_for_timeout(300)
    after = get_json(f"{server.url}/__log")
    assert len(after) >= before
    assert len([e for e in after if e.get("kind") == "CreateTweet"]) == 1


def test_log_events_are_ordered_and_timestamped(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA"); go(page, "#nav-home", "/home"); page.wait_for_timeout(300)
    log = get_json(f"{server.url}/__log")
    seqs = [e["seq"] for e in log]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert all("ms" in e for e in log)


# ---------- replies list and fixtures ----------

def test_replies_have_the_known_names_in_order(open_page, server):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA"); go(page, "#link-n1", "/status/n1")
    names = page.locator("#replies .author").all_inner_texts()
    assert names == get_json(f"{server.url}/__fixture")["reply_authors"]


def test_virtualized_list_renders_fewer_nodes_than_replies_and_recycles_them(open_page):
    page = open_page("mock_x", variant="virtualized")
    go(page, "#nav-nasa", "/NASA"); go(page, "#link-n1", "/status/n1")
    assert page.locator("[data-testid=reply-row]:visible").count() <= 10
    first_before = page.inner_text("#reply-row-0")
    page.evaluate("document.getElementById('replies').scrollTop = 80*30")
    page.wait_for_timeout(200)
    first_after = page.inner_text("#reply-row-0")
    assert first_before != first_after, "the same element id must now show a different reply"
    assert "Replier 31" in first_after


def test_default_list_is_not_virtualized(open_page):
    page = open_page("mock_x")
    go(page, "#nav-nasa", "/NASA"); go(page, "#link-n1", "/status/n1")
    assert page.locator("#replies article").count() == 8


# ---------- variants (the contract with docs/COMMAND_SCRIPT.csv) ----------

def first_post_text(page):
    go(page, "#nav-nasa", "/NASA")
    return page.inner_text("[data-testid=tweet] >> nth=0")


def test_variant_pinned_first(open_page):
    page = open_page("mock_x", variant="pinned_first")
    text = first_post_text(page)
    assert "Pinned" in text


def test_variant_repost_first(open_page):
    page = open_page("mock_x", variant="repost_first")
    assert "NASA reposted" in first_post_text(page)


def test_variant_no_photo(open_page):
    page = open_page("mock_x", variant="no_photo")
    go(page, "#nav-nasa", "/NASA")
    assert page.locator("[data-testid=tweet] >> nth=0 >> [data-testid=tweetPhoto]").count() == 0
    assert page.locator("[data-testid=tweet] >> nth=1 >> [data-testid=tweetPhoto]").count() == 1


def test_variant_signed_out_has_no_composer_and_reply_shows_login(open_page, server):
    page = open_page("mock_x", variant="signed_out")
    assert page.locator("#composer-box").count() == 0
    go(page, "#nav-nasa", "/NASA"); page.click("#reply-btn-n1")
    assert page.locator("#login-wall").count() == 1
    assert page.locator("#composer-box").count() == 0
    assert server.requests() == []


def test_variant_replies_limited(open_page):
    page = open_page("mock_x", variant="replies_limited")
    go(page, "#nav-nasa", "/NASA")
    assert page.get_attribute("#reply-btn-n1", "aria-disabled") == "true"
    page.click("#reply-btn-n1", force=True)
    assert page.locator("#composer-box").count() == 0
    go(page, "#link-n1", "/status/n1")
    assert page.locator("#reply-limited").count() == 1


def test_variant_post_error_toast_returns_500_and_shows_toast(open_page, server):
    page = open_page("mock_x", variant="post_error_toast")
    go(page, "#nav-nasa", "/NASA"); page.click("#reply-btn-n1"); type_reply(page); page.click("#post-button")
    page.wait_for_selector("#toast:visible")
    assert "Something went wrong" in page.inner_text("#toast")
    assert len(server.requests("CreateTweet")) == 1
    assert any(e.get("type") == "response" and e.get("status") == 500 for e in page.evaluate("window.__navi_log"))


def test_variant_ctrl_enter_only_has_no_post_button(open_page, server):
    page = open_page("mock_x", variant="ctrl_enter_only")
    go(page, "#nav-nasa", "/NASA"); page.click("#reply-btn-n1")
    assert page.locator("#post-button").count() == 0
    type_reply(page)
    page.keyboard.press("Control+Enter"); page.wait_for_timeout(300)
    assert len(server.requests("CreateTweet")) == 1


def test_variant_feed_shift_makes_old_positional_ids_point_at_a_new_post(open_page, server):
    page = open_page("mock_x", variant="feed_shifts_after_observe")
    go(page, "#nav-nasa", "/NASA")
    before = page.inner_text("#tweet-0")
    assert "Webb captured" in before
    page.wait_for_selector("text=Breaking: a brand new post", timeout=5000)
    after = page.inner_text("#tweet-0")
    assert "Breaking" in after and before != after
    assert any(e.get("type") == "feed_shift" for e in get_json(f"{server.url}/__log"))
