"""Tests for maze page parsing.

The doors fixture mirrors the live page: a decoy ``<span class="amount">``, the
``action`` nonce on every door link, and the remaining key count.
"""

from __future__ import annotations

from src import wicket
from src.config import Config, Delays
from src.modules.auth import Auth
from src.modules.maze import MazeBot
from tests.test_auth import FakeSession

NO_DELAYS = Delays(min_seconds=0, max_seconds=0, page_load_min=0, page_load_max=0)


def make_maze():
    config = Config(username="u", password="p", delays=NO_DELAYS)
    return MazeBot(Auth(config, session=FakeSession()), config)


class TestCurrentLevel:
    def test_reads_the_room_counter(self, doors_page):
        assert make_maze().current_level(wicket.parse(doors_page)) == 4

    def test_ignores_the_decoy_span(self, doors_page):
        # A <span class="amount"> holding promo text precedes the counter; only
        # the <b> carries the room number.
        assert make_maze().current_level(wicket.parse(doors_page)) != 10

    def test_returns_zero_when_the_counter_is_absent(self, login_page):
        assert make_maze().current_level(wicket.parse(login_page)) == 0

    def test_returns_zero_for_an_unreadable_counter(self):
        soup = wicket.parse('<b class="amount">много</b>')
        assert make_maze().current_level(soup) == 0


class TestKeysLeft:
    def test_reads_the_remaining_keys(self, doors_page):
        assert make_maze().keys_left(wicket.parse(doors_page)) == 1707

    def test_handles_thousand_separators(self):
        soup = wicket.parse('<span class="small">Осталось ключей: 1’707</span>')
        assert make_maze().keys_left(soup) == 1707

    def test_reads_zero(self):
        soup = wicket.parse('<span class="small">Осталось ключей: 0</span>')
        assert make_maze().keys_left(soup) == 0

    def test_returns_none_when_not_reported(self, home_page):
        assert make_maze().keys_left(wicket.parse(home_page)) is None


class TestIsSolved:
    def test_detects_the_victory_screen(self, victory_page):
        assert make_maze().is_solved(wicket.parse(victory_page)) is True

    def test_reaching_the_last_room_is_not_yet_a_win(self, doors_page):
        # The final room still offers doors; one of them has to be opened
        # before the prize appears. Stopping here is how the prize was missed.
        assert make_maze().is_solved(wicket.parse(doors_page)) is False

    def test_a_dead_end_is_not_a_win(self, dead_end_page):
        assert make_maze().is_solved(wicket.parse(dead_end_page)) is False

    def test_the_victory_screen_has_no_room_counter(self, victory_page):
        # Hence the win must be recognised by its text, not by the counter.
        assert make_maze().current_level(wicket.parse(victory_page)) == 0


class TestReward:
    def test_reads_the_reward_amounts(self, victory_page):
        assert make_maze().reward(wicket.parse(victory_page)) == ["880'000", "1'234'567"]

    def test_no_reward_on_an_ordinary_page(self, doors_page):
        assert make_maze().reward(wicket.parse(doors_page)) == []


class TestIsDeadEnd:
    def test_detects_the_dead_end_banner(self, dead_end_page):
        assert make_maze().is_dead_end(wicket.parse(dead_end_page)) is True

    def test_false_on_a_normal_maze_page(self, doors_page):
        assert make_maze().is_dead_end(wicket.parse(doors_page)) is False

    def test_ignores_unrelated_notifications(self):
        soup = wicket.parse('<span class="notify">Получена награда</span>')
        assert make_maze().is_dead_end(soup) is False


class TestDoorUrls:
    def test_returns_absolute_door_urls(self, doors_page):
        urls = make_maze().door_urls(wicket.parse(doors_page), "https://nebo.mobi/doors")
        assert urls == [
            "https://nebo.mobi/doors?3-1.-doorLink1&action=1787078108652",
            "https://nebo.mobi/doors?3-1.-doorLink2&action=1787078108652",
            "https://nebo.mobi/doors?3-1.-doorLink3&action=1787078108652",
        ]

    def test_preserves_the_single_use_nonce(self, doors_page):
        # Dropping `action` would make the server reject the click.
        urls = make_maze().door_urls(wicket.parse(doors_page), "https://nebo.mobi/doors")
        assert all("action=1787078108652" in url for url in urls)

    def test_excludes_navigation_and_logout_links(self, doors_page):
        urls = make_maze().door_urls(wicket.parse(doors_page), "https://nebo.mobi/doors")
        assert not any("logoutLink" in url or url.endswith("/home") for url in urls)

    def test_returns_empty_when_there_are_no_doors(self, home_page):
        assert make_maze().door_urls(wicket.parse(home_page), "https://nebo.mobi/home") == []
