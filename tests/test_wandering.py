"""Tests for idle browsing between useful actions."""

from __future__ import annotations

import requests

from src.config import Config, Delays
from src.utils.human_like import HumanBehavior
from src.utils.wandering import IDLE_PAGES, Wanderer

NO_DELAYS = Delays(min_seconds=0, max_seconds=0, page_load_min=0, page_load_max=0)


class RecordingSession:
    """A session that remembers what was asked for and never touches a network."""

    def __init__(self, fail: bool = False):
        self.urls: list[str] = []
        self.fail = fail

    def get(self, url, **kwargs):
        self.urls.append(url)
        if self.fail:
            raise requests.ConnectionError("no network")
        response = requests.Response()
        response.status_code = 200
        response._content = b"<html></html>"
        response.url = url
        return response


def make_wanderer(chance: float = 1.0, fail: bool = False):
    config = Config(username="u", password="p", delays=NO_DELAYS, wander_chance=chance)
    session = RecordingSession(fail=fail)
    return Wanderer(session, config, HumanBehavior(NO_DELAYS)), session


class TestWandering:
    def test_opens_something_when_the_dice_say_so(self):
        wanderer, session = make_wanderer(chance=1.0)
        assert wanderer.maybe_wander() >= 1
        assert session.urls

    def test_never_wanders_when_disabled(self):
        wanderer, session = make_wanderer(chance=0.0)
        assert wanderer.maybe_wander() == 0
        assert session.urls == []

    def test_only_visits_pages_that_cost_nothing(self):
        # The whole point is that a wasted request is the worst case.
        wanderer, session = make_wanderer(chance=1.0)
        for _ in range(40):
            wanderer.maybe_wander()
        visited = {url.replace("https://nebo.mobi", "") for url in session.urls}
        assert visited <= set(IDLE_PAGES)

    def test_never_opens_the_maze_or_spends_anything(self):
        wanderer, session = make_wanderer(chance=1.0)
        for _ in range(40):
            wanderer.maybe_wander()
        assert not any("doors" in url or "payment" in url for url in session.urls)

    def test_visits_vary_rather_than_repeating_one_page(self):
        wanderer, session = make_wanderer(chance=1.0)
        for _ in range(30):
            wanderer.maybe_wander()
        assert len(set(session.urls)) > 1

    def test_a_network_failure_is_shrugged_off(self):
        # Idle browsing must never be able to break a run.
        wanderer, _ = make_wanderer(chance=1.0, fail=True)
        assert wanderer.maybe_wander() == 0


class TestWrongTurns:
    def test_comes_back_home_afterwards(self):
        # Rarer than plain wandering by design, so give it plenty of rolls.
        # Forty of them failed about once every seventy runs, which is a test
        # that cries wolf rather than one that catches anything.
        wanderer, session = make_wanderer(chance=1.0)
        assert any(wanderer.maybe_wrong_turn() for _ in range(300))
        assert session.urls[-1].endswith("/home")

    def test_takes_two_requests_and_no_more(self):
        wanderer, session = make_wanderer(chance=1.0)
        while not wanderer.maybe_wrong_turn():
            pass
        assert len(session.urls) == 2

    def test_never_happens_when_disabled(self):
        wanderer, session = make_wanderer(chance=0.0)
        assert wanderer.maybe_wrong_turn() is False
        assert session.urls == []

    def test_is_much_rarer_than_ordinary_wandering(self):
        # A misclick is an event, not a habit: a third of the wander rate was
        # still frequent enough to be noticed while watching.
        wanderer, _ = make_wanderer(chance=0.3)
        turns = sum(wanderer.maybe_wrong_turn() for _ in range(600))
        assert turns < 600 * 0.3 / 5


class TestFrequency:
    def test_roughly_matches_the_configured_chance(self):
        wanderer, _ = make_wanderer(chance=0.2)
        wandered = sum(1 for _ in range(600) if wanderer.maybe_wander())
        assert 0.13 <= wandered / 600 <= 0.28

    def test_a_low_chance_leaves_most_actions_alone(self):
        wanderer, _ = make_wanderer(chance=0.05)
        wandered = sum(1 for _ in range(400) if wanderer.maybe_wander())
        assert wandered < 400 * 0.15
