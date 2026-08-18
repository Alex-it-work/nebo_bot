"""Tests for the login flow, driven by a stubbed HTTP session."""

from __future__ import annotations

import pytest
import requests

from src.config import Config, Delays
from src.modules.auth import Auth

# Zero delays keep the suite fast; pacing is covered in test_human_like.
NO_DELAYS = Delays(min_seconds=0, max_seconds=0, page_load_min=0, page_load_max=0)


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, text="", url="https://nebo.mobi/", status_code=200, location=None):
        self.text = text
        self.url = url
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}

    @property
    def is_redirect(self):
        return self.status_code in (301, 302, 303, 307, 308)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


class FakeSession:
    """Records outgoing requests and replays scripted responses."""

    def __init__(self, get_responses=None, post_response=None):
        # Mapping of path substring -> response, or a list consumed in order.
        self.get_responses = get_responses or {}
        self.post_response = post_response
        self.headers = {}
        self.gets = []
        self.posts = []
        self.closed = False

    def get(self, url, timeout=None, allow_redirects=True):
        self.gets.append(url)
        for key, response in self.get_responses.items():
            if key in url:
                return response.pop(0) if isinstance(response, list) else response
        return FakeResponse(url=url)

    def post(self, url, data=None, timeout=None, allow_redirects=True):
        self.posts.append({"url": url, "data": data})
        return self.post_response or FakeResponse(url=url)

    def close(self):
        self.closed = True


@pytest.fixture
def config():
    return Config(username="Player", password="secret", delays=NO_DELAYS, timeout=5)


def make_auth(config, session):
    return Auth(config, session=session)


class TestLogin:
    def test_posts_to_the_action_url_including_the_routing_query(self, config, login_page):
        session = FakeSession(
            get_responses={
                "/login": FakeResponse(login_page, url="https://nebo.mobi/login"),
                "/home": FakeResponse(url="https://nebo.mobi/home", status_code=200),
            }
        )
        assert make_auth(config, session).login() is True

        # The regression that broke the bot: posting to a bare /login instead of
        # the action URL that tells Wicket which component was submitted.
        assert session.posts[0]["url"] == (
            "https://nebo.mobi/login;jsessionid=0000000000000000000000000000DEAD"
            "?0-1.-loginForm-loginForm"
        )

    def test_sends_credentials_and_the_submit_button(self, config, login_page):
        session = FakeSession(
            get_responses={
                "/login": FakeResponse(login_page, url="https://nebo.mobi/login"),
                "/home": FakeResponse(url="https://nebo.mobi/home", status_code=200),
            }
        )
        make_auth(config, session).login()

        assert session.posts[0]["data"] == {
            "login": "Player",
            "password": "secret",
            "p::submit": "",
        }

    def test_fails_when_the_session_is_not_authenticated_afterwards(self, config, login_page):
        session = FakeSession(
            get_responses={
                "/login": FakeResponse(login_page, url="https://nebo.mobi/login"),
                "/home": FakeResponse(
                    status_code=302, location="https://nebo.mobi/welcome"
                ),
            }
        )
        assert make_auth(config, session).login() is False

    def test_logs_the_servers_complaint(self, config, login_page, caplog):
        session = FakeSession(
            get_responses={
                "/login": FakeResponse(login_page, url="https://nebo.mobi/login"),
                "/home": FakeResponse(status_code=302, location="https://nebo.mobi/welcome"),
            },
            post_response=FakeResponse(
                '<span class="notify">Неверный пароль</span>', url="https://nebo.mobi/login"
            ),
        )
        assert make_auth(config, session).login() is False
        assert "Неверный пароль" in caplog.text

    def test_reports_changed_markup_instead_of_crashing(self, config):
        session = FakeSession(
            get_responses={"/login": FakeResponse("<html><body>redesign</body></html>")}
        )
        assert make_auth(config, session).login() is False

    def test_survives_a_network_error(self, config):
        class ExplodingSession(FakeSession):
            def get(self, url, timeout=None, allow_redirects=True):
                raise requests.ConnectionError("boom")

        assert make_auth(config, ExplodingSession()).login() is False


class TestIsAuthenticated:
    def test_true_when_home_renders(self, config):
        session = FakeSession(get_responses={"/home": FakeResponse(status_code=200)})
        assert make_auth(config, session).is_authenticated() is True

    def test_false_when_home_redirects_to_welcome(self, config):
        session = FakeSession(
            get_responses={
                "/home": FakeResponse(status_code=302, location="https://nebo.mobi/welcome")
            }
        )
        assert make_auth(config, session).is_authenticated() is False

    def test_does_not_follow_redirects(self, config):
        # Following them would turn the 302 into a 200 and hide the answer.
        session = FakeSession(get_responses={"/home": FakeResponse(status_code=200)})
        captured = {}

        def spy(url, timeout=None, allow_redirects=True):
            captured["allow_redirects"] = allow_redirects
            return FakeResponse(status_code=200)

        session.get = spy
        make_auth(config, session).is_authenticated()
        assert captured["allow_redirects"] is False


class TestLogout:
    def test_follows_the_sites_logout_link(self, config, home_page):
        session = FakeSession(
            get_responses={
                "/home": [
                    FakeResponse(status_code=200),  # is_authenticated -> yes
                    FakeResponse(home_page, url="https://nebo.mobi/home"),  # page fetch
                    FakeResponse(home_page, url="https://nebo.mobi/home"),  # logout link GET
                    FakeResponse(status_code=302, location="https://nebo.mobi/welcome"),
                ]
            }
        )
        assert make_auth(config, session).logout() is True
        assert "https://nebo.mobi/home?4-1.-logoutLink" in session.gets

    def test_is_a_no_op_when_already_logged_out(self, config):
        session = FakeSession(
            get_responses={
                "/home": FakeResponse(status_code=302, location="https://nebo.mobi/welcome")
            }
        )
        assert make_auth(config, session).logout() is True
        assert len(session.gets) == 1

    def test_fails_when_the_link_is_missing(self, config, login_page):
        session = FakeSession(
            get_responses={
                "/home": [
                    FakeResponse(status_code=200),
                    FakeResponse(login_page, url="https://nebo.mobi/home"),
                ]
            }
        )
        assert make_auth(config, session).logout() is False
