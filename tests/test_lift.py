"""Tests for reading the lift."""

from __future__ import annotations

from src import wicket
from src.config import Config, Delays
from src.modules.auth import Auth
from src.modules.lift import LiftBot
from tests.test_auth import FakeSession

NO_DELAYS = Delays(min_seconds=0, max_seconds=0, page_load_min=0, page_load_max=0)


def make_bot():
    config = Config(username="u", password="p", delays=NO_DELAYS)
    return LiftBot(Auth(config, session=FakeSession()), config)


class TestState:
    def test_reads_the_queue_and_the_floor(self, lift_page):
        state = make_bot().state(wicket.parse(lift_page))
        assert (state.floor, state.visitors) == (0, 89)

    def test_reads_the_tips_and_their_ceiling(self, lift_page):
        state = make_bot().state(wicket.parse(lift_page))
        assert (state.tips, state.tips_cap) == (7, 32)

    def test_reads_where_the_current_visitor_is_going(self, lift_page):
        assert make_bot().state(wicket.parse(lift_page)).wanted_floor == 39

    def test_tips_are_not_full_while_under_the_ceiling(self, lift_page):
        assert not make_bot().state(wicket.parse(lift_page)).tips_full

    def test_recognises_a_capped_day(self, lift_capped_page):
        state = make_bot().state(wicket.parse(lift_capped_page))
        assert state.tips_full and (state.tips, state.tips_cap) == (67, 67)

    def test_an_unrelated_page_reads_as_empty(self, home_page):
        assert make_bot().state(wicket.parse(home_page)).visitors == 0


class TestLinks:
    def test_finds_the_raise_link(self, lift_page):
        url = make_bot().up_url(wicket.parse(lift_page), "https://nebo.mobi/lift")
        assert url == "https://nebo.mobi/lift?11-1.-liftState-upLink"

    def test_finds_the_paid_shortcut(self, lift_page):
        url = make_bot().deliver_all_url(wicket.parse(lift_page), "https://nebo.mobi/lift")
        assert url == "https://nebo.mobi/lift?11-1.-processLiftAll-link"

    def test_no_links_on_another_page(self, home_page):
        bot, soup = make_bot(), wicket.parse(home_page)
        assert bot.up_url(soup, "https://nebo.mobi/home") is None
        assert bot.deliver_all_url(soup, "https://nebo.mobi/home") is None

    def test_the_upgrade_button_is_not_mistaken_for_a_ride(self, lift_page):
        # "Улучшить лифт" links to /lobby and must never be pressed as a ride.
        url = make_bot().up_url(wicket.parse(lift_page), "https://nebo.mobi/lift")
        assert "lobby" not in url
