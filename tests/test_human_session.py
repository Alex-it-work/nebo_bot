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
