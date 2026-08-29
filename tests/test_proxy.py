"""Tests for serving the game through the panel.

Every link in a page has to come back pointing at the panel. One that slips
through sends the browser to the game's own address, where its cookie decides
which profile it is — which is the whole bug this exists to avoid.
"""

from __future__ import annotations

import requests

from src.proxy import GameProxy, prefix_for

BASE = "https://nebo.mobi"


def make_proxy(session=None):
    return GameProxy(BASE, lambda account: session or requests.Session())


class TestPrefix:
    def test_a_plain_name(self):
        assert prefix_for("Justdo") == "/play/Justdo/"

    def test_a_space_is_encoded(self):
        assert prefix_for("Grizly Bear") == "/play/Grizly%20Bear/"

    def test_cyrillic_is_encoded(self):
        assert prefix_for("сосочки").startswith("/play/%")

    def test_a_slash_in_a_name_cannot_escape_the_prefix(self):
        assert prefix_for("a/b") == "/play/a%2Fb/"


class TestRewritingOneUrl:
    def setup_method(self):
        self.proxy = make_proxy()

    def rewrite(self, url):
        return self.proxy.rewrite_url(url, "Justdo")

    def test_a_root_relative_path(self):
        assert self.rewrite("/js/t.js") == "/play/Justdo/js/t.js"

    def test_an_absolute_site_url(self):
        assert self.rewrite("https://nebo.mobi/doors") == "/play/Justdo/doors"

    def test_the_site_root_itself(self):
        assert self.rewrite("https://nebo.mobi") == "/play/Justdo/"

    def test_the_query_survives(self):
        assert self.rewrite("/doors?3-1.-doorLink1&action=17") == (
            "/play/Justdo/doors?3-1.-doorLink1&action=17"
        )

    def test_http_as_well_as_https(self):
        assert self.rewrite("http://nebo.mobi/home") == "/play/Justdo/home"

    def test_a_relative_url_is_left_to_the_base_tag(self):
        # Prefixing it here as well would prefix it twice.
        assert self.rewrite("./doors?0-1.-x") == "./doors?0-1.-x"

    def test_an_anchor_is_left_alone(self):
        assert self.rewrite("#top") == "#top"

    def test_a_script_url_is_left_alone(self):
        assert self.rewrite("javascript:void(0)") == "javascript:void(0)"

    def test_another_site_is_left_alone(self):
        assert self.rewrite("https://example.com/x") == "https://example.com/x"

    def test_a_protocol_relative_url_is_left_alone(self):
        assert self.rewrite("//cdn.example.com/x.js") == "//cdn.example.com/x.js"

    def test_an_already_rewritten_url_is_not_rewritten_twice(self):
        assert self.rewrite("/play/Justdo/home") == "/play/Justdo/home"

    def test_an_empty_value(self):
        assert self.rewrite("") == ""


class TestRewritingAPage:
    def setup_method(self):
        self.proxy = make_proxy()

    def test_the_base_tag_is_injected_into_the_head(self):
        page = "<html><head><title>x</title></head><body></body></html>"
        out = self.proxy.rewrite(page, "Justdo")
        assert '<base href="/play/Justdo/">' in out
        assert out.index("<base") < out.index("<title>")

    def test_a_page_without_a_head_still_gets_one(self):
        out = self.proxy.rewrite("<p>привет", "Justdo")
        assert out.startswith('<base href="/play/Justdo/">')

    def test_links_are_rewritten(self):
        out = self.proxy.rewrite('<a href="/home">дом</a>', "Justdo")
        assert 'href="/play/Justdo/home"' in out

    def test_images_are_rewritten(self):
        out = self.proxy.rewrite('<img src="/img/key.png">', "Justdo")
        assert 'src="/play/Justdo/img/key.png"' in out

    def test_form_actions_are_rewritten(self):
        out = self.proxy.rewrite('<form action="/login?0-1.-loginForm">', "Justdo")
        assert 'action="/play/Justdo/login?0-1.-loginForm"' in out

    def test_single_quoted_attributes_too(self):
        out = self.proxy.rewrite("<a href='/home'>x</a>", "Justdo")
        assert "href='/play/Justdo/home'" in out

    def test_nothing_points_at_the_game_afterwards(self):
        page = (
            '<head></head><a href="https://nebo.mobi/doors">d</a>'
            '<img src="/img/k.png"><form action="/quests"></form>'
        )
        out = self.proxy.rewrite(page, "Justdo")
        assert "nebo.mobi" not in out

    def test_relative_links_are_untouched(self):
        page = '<head></head><a href="./doors?3-1.-doorLink1">1</a>'
        out = self.proxy.rewrite(page, "Justdo")
        assert 'href="./doors?3-1.-doorLink1"' in out


class FakeSession:
    """Answers proxied requests from a script."""

    def __init__(self, status=200, headers=None, text="", content=b""):
        self.status, self.text, self.content = status, text, content
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs.get("data")))
        response = requests.Response()
        response.status_code = self.status
        response.headers.update(self.headers)
        response._content = self.content or self.text.encode("utf-8")
        response.url = url
        return response


class TestFetching:
    def test_the_path_is_rebuilt_onto_the_site(self):
        session = FakeSession(text="<html></html>")
        GameProxy(BASE, lambda a: session).fetch("Justdo", "doors?3-1.-x")
        assert session.calls[0][1] == "https://nebo.mobi/doors?3-1.-x"

    def test_html_comes_back_rewritten(self):
        session = FakeSession(text='<head></head><a href="/home">x</a>')
        _, _, body = GameProxy(BASE, lambda a: session).fetch("Justdo", "doors")
        assert b"/play/Justdo/home" in body

    def test_an_image_is_passed_through_untouched(self):
        session = FakeSession(headers={"Content-Type": "image/png"}, content=b"PNG-data")
        _, headers, body = GameProxy(BASE, lambda a: session).fetch("Justdo", "img/k.png")
        assert body == b"PNG-data" and headers["Content-Type"] == "image/png"

    def test_a_redirect_is_rewritten_rather_than_followed(self):
        # Following it here would land the browser on the game's own address.
        session = FakeSession(status=302, headers={"Location": "https://nebo.mobi/welcome"})
        status, headers, _ = GameProxy(BASE, lambda a: session).fetch("Justdo", "home")
        assert status == 302 and headers["Location"] == "/play/Justdo/welcome"

    def test_a_post_carries_its_body(self):
        session = FakeSession(text="<html></html>")
        GameProxy(BASE, lambda a: session).fetch(
            "Justdo",
            "login",
            method="POST",
            body=b"login=x",
            content_type="application/x-www-form-urlencoded",
        )
        assert session.calls[0][0] == "POST" and session.calls[0][2] == b"login=x"

    def test_no_session_is_reported_rather_than_raised(self):
        status, _, body = GameProxy(BASE, lambda a: "не удалось войти").fetch("Justdo", "home")
        assert status == 502 and "не удалось войти" in body.decode("utf-8")

    def test_a_network_failure_is_reported_rather_than_raised(self):
        class Broken:
            headers: dict = {}

            def request(self, *args, **kwargs):
                raise requests.ConnectionError("нет сети")

        status, _, body = GameProxy(BASE, lambda a: Broken()).fetch("Justdo", "home")
        assert status == 502 and "нет сети" in body.decode("utf-8")

    def test_the_content_length_of_the_game_is_not_passed_on(self):
        # It describes the game's body, not the rewritten one.
        session = FakeSession(
            headers={"Content-Type": "text/html", "Content-Length": "5"},
            text='<head></head><a href="/home">x</a>',
        )
        _, headers, _ = GameProxy(BASE, lambda a: session).fetch("Justdo", "home")
        assert "Content-Length" not in headers
