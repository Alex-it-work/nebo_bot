"""Tests for pacing being a property of the session rather than a convention.

Every request the bot makes goes through one session, so pausing there covers
every action in the game — including the ones nobody remembered to pace, and
the ones not written yet.
"""

from __future__ import annotations

import pathlib
import re

import requests

from src.config import Config, Delays
from src.modules.auth import Auth
from src.utils.human_like import HumanBehavior, HumanSession

NO_DELAYS = Delays(min_seconds=0, max_seconds=0, page_load_min=0, page_load_max=0)

SOURCE_ROOT = pathlib.Path(__file__).resolve().parent.parent / "src"


class Recorder(HumanSession):
    """A paced session that records the order of pauses and requests."""

    def __init__(self):
        super().__init__(HumanBehavior(NO_DELAYS))
        self.events: list[str] = []
        self.human.pause = lambda multiplier=1.0: self.events.append("think")
        self.human.pause_page_load = lambda: self.events.append("read")

    def send(self, request, **kwargs):  # noqa: D102 - lowest hookable layer
        self.events.append(f"{request.method} {request.url}")
        response = requests.Response()
        response.status_code = 200
        response._content = b"<html></html>"
        response.url = request.url
        response.request = request
        return response


class TestPacingSurroundsEveryRequest:
    def test_a_get_is_preceded_and_followed_by_a_pause(self):
        session = Recorder()
        session.get("https://nebo.mobi/home")
        assert session.events == ["think", "GET https://nebo.mobi/home", "read"]

    def test_a_post_is_paced_too(self):
        # Logging in is a request like any other and used to be paced by hand.
        session = Recorder()
        session.post("https://nebo.mobi/login", data={"login": "u"})
        assert session.events[0] == "think" and session.events[-1] == "read"

    def test_every_request_is_paced_not_only_the_first(self):
        session = Recorder()
        for _ in range(3):
            session.get("https://nebo.mobi/doors")
        assert session.events.count("think") == 3
        assert session.events.count("read") == 3


class TestUnpaced:
    def test_pacing_can_be_lifted_for_a_block(self):
        session = Recorder()
        with session.unpaced():
            session.get("https://nebo.mobi/home")
        assert session.events == ["GET https://nebo.mobi/home"]

    def test_pacing_comes_back_afterwards(self):
        session = Recorder()
        with session.unpaced():
            session.get("https://nebo.mobi/home")
        session.get("https://nebo.mobi/home")
        assert session.events[-3:] == ["think", "GET https://nebo.mobi/home", "read"]

    def test_pacing_comes_back_after_a_failure(self):
        session = Recorder()
        try:
            with session.unpaced():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert session.paced is True


class TestAuthSuppliesAPacedSession:
    def test_the_default_session_paces_itself(self):
        auth = Auth(Config(username="u", password="p", delays=NO_DELAYS, live_view=False))
        assert isinstance(auth.session, HumanSession)

    def test_the_pacing_uses_the_configured_delays(self):
        delays = Delays(min_seconds=2, max_seconds=5)
        auth = Auth(Config(username="u", password="p", delays=delays, live_view=False))
        assert auth.session.human.delays is delays

    def test_an_injected_session_is_left_alone(self):
        # Tests inject their own; forcing pacing on it would make them crawl.
        plain = requests.Session()
        auth = Auth(
            Config(username="u", password="p", delays=NO_DELAYS, live_view=False), session=plain
        )
        assert auth.session is plain


class TestNothingBypassesTheSession:
    """The guarantee only holds while every module fetches through Auth's session."""

    def test_no_module_builds_its_own_session(self):
        offenders = [
            path.relative_to(SOURCE_ROOT).as_posix()
            for path in SOURCE_ROOT.rglob("*.py")
            if "requests.Session()" in path.read_text(encoding="utf-8")
            and path.name != "human_like.py"
        ]
        assert offenders == [], f"these would fetch unpaced: {offenders}"

    def test_no_module_uses_the_module_level_requests_helpers(self):
        # requests.get() opens a fresh unpaced connection every time.
        pattern = re.compile(r"\brequests\.(get|post|head|put)\(")
        offenders = [
            path.relative_to(SOURCE_ROOT).as_posix()
            for path in SOURCE_ROOT.rglob("*.py")
            if pattern.search(path.read_text(encoding="utf-8"))
        ]
        assert offenders == [], f"these would fetch unpaced: {offenders}"

    def test_the_key_count_between_mazes_goes_through_the_session(self):
        # This one really was unpaced: it ran after every finished maze.
        source = (SOURCE_ROOT / "bot.py").read_text(encoding="utf-8")
        assert "self.auth.session.get(" in source


class Scripted(HumanSession):
    """A paced session that answers from a script and records the order.

    Hooking at ``send`` means the whole of ``requests`` above it runs for
    real, so what is recorded is what the pacing actually does to a live
    code path rather than to a stub of one.
    """

    def __init__(self, pages: dict[str, str]):
        super().__init__(HumanBehavior(NO_DELAYS))
        self.pages = pages
        self.events: list[str] = []
        self.human.pause = lambda multiplier=1.0: self.events.append(f"pause x{multiplier:g}")
        self.human.pause_page_load = lambda: self.events.append("read")

    def send(self, request, **kwargs):  # noqa: D102 - lowest hookable layer
        body = next((html for key, html in self.pages.items() if key in request.url), "<html>")
        self.events.append(request.method + " " + request.url.split("nebo.mobi")[-1])
        response = requests.Response()
        response.status_code = 200
        response._content = body.encode("utf-8")
        response.url = request.url
        response.request = request
        return response

    @property
    def requests_made(self) -> list[str]:
        return [e for e in self.events if e[:3] in ("GET", "POS")]


class TestLoggingInIsPacedToo:
    """Login used to run at full speed: the check straight after the form was
    submitted had no pause in front of it at all, and neither did the first
    page the bot opened afterwards."""

    def _login(self, login_page: str):
        session = Scripted({"/login": login_page, "/home": "<html>дом</html>"})
        config = Config(username="u", password="p", delays=NO_DELAYS, live_view=False)
        auth = Auth(config, session=session)
        # An injected session brings its own pacing, so Auth's deliberate
        # pauses are recorded on the same timeline to see the whole order.
        auth.human.pause = session.human.pause
        assert auth.login() is True
        return session

    def test_every_request_of_the_login_is_paced(self, login_page):
        session = self._login(login_page)
        for position, event in enumerate(session.events):
            if event.startswith(("GET", "POST")):
                assert session.events[position - 1].startswith("pause"), (
                    f"{event} was sent with no pause before it: {session.events}"
                )
                assert session.events[position + 1] == "read", (
                    f"{event} was followed straight on: {session.events}"
                )

    def test_the_form_submission_is_not_instant(self, login_page):
        # The page has to be read before anything is typed into it.
        session = self._login(login_page)
        submitted = session.events.index(next(e for e in session.events if e.startswith("POST")))
        assert session.events[submitted - 1].startswith("pause")

    def test_the_check_after_submitting_is_not_instant(self, login_page):
        # This was the instant one: POST, then /home in the same breath.
        session = self._login(login_page)
        submitted = next(i for i, e in enumerate(session.events) if e.startswith("POST"))
        following = next(i for i, e in enumerate(session.events[submitted + 1:], submitted + 1)
                         if e.startswith("GET"))
        assert following - submitted > 1, f"nothing between them: {session.events}"

    def test_arriving_is_still_dwelt_on(self, login_page):
        # The settling pause after a successful login stays on top of it.
        session = self._login(login_page)
        assert any(e.startswith("pause x4") for e in session.events)

    def test_three_requests_and_no_more(self, login_page):
        session = self._login(login_page)
        assert len(session.requests_made) == 3
