"""Tests for reading and evicting residents."""

from __future__ import annotations

from src import wicket
from src.config import Config, Delays
from src.modules.auth import Auth
from src.modules.humans import HumansBot
from tests.test_auth import FakeSession

NO_DELAYS = Delays(min_seconds=0, max_seconds=0, page_load_min=0, page_load_max=0)
URL = "https://nebo.mobi/humans"


def make_bot(spend=False):
    config = Config(username="u", password="p", delays=NO_DELAYS, spend_baksy=spend)
    return HumansBot(Auth(config, session=FakeSession()), config)


class TestResidents:
    def test_reads_everyone_listed(self, humans_page):
        people = make_bot().residents(wicket.parse(humans_page), URL)
        assert [p.name for p in people] == ["Саша Вайнер", "Ира Мишина", "Ира Яшина"]

    def test_reads_the_skill(self, humans_page):
        people = make_bot().residents(wicket.parse(humans_page), URL)
        assert [p.skill for p in people] == [6, 6, 9]

    def test_reads_the_markers(self, humans_page):
        people = make_bot().residents(wicket.parse(humans_page), URL)
        assert [p.marker for p in people] == ["", "+", "-"]

    def test_resolves_each_resident_page(self, humans_page):
        people = make_bot().residents(wicket.parse(humans_page), URL)
        assert people[0].page_url == "https://nebo.mobi/human/1?1=3"

    def test_no_residents_on_another_page(self, home_page):
        assert make_bot().residents(wicket.parse(home_page), URL) == []


class TestEvictable:
    def test_keeps_anyone_marked_plus(self, humans_page):
        bot = make_bot()
        people = bot.residents(wicket.parse(humans_page), URL)
        assert "Ира Мишина" not in [p.name for p in bot.evictable(people)]

    def test_evicts_a_nine_marked_minus(self, humans_page):
        # The marker decides, not the number: a nine marked (-) still goes.
        bot = make_bot()
        people = bot.residents(wicket.parse(humans_page), URL)
        assert "Ира Яшина" in [p.name for p in bot.evictable(people)]

    def test_evicts_the_unmarked(self, humans_page):
        bot = make_bot()
        people = bot.residents(wicket.parse(humans_page), URL)
        assert "Саша Вайнер" in [p.name for p in bot.evictable(people)]

    def test_a_marked_resident_is_flagged_as_an_upgrade(self, humans_page):
        people = make_bot().residents(wicket.parse(humans_page), URL)
        assert people[1].upgrades_a_floor and not people[2].upgrades_a_floor


class TestBulkEviction:
    def test_finds_the_paid_button(self, humans_page):
        url = make_bot().bulk_evict_url(wicket.parse(humans_page), URL)
        assert url == "https://nebo.mobi/humans?13-1.-clearLinkPanel-clearLink-link"

    def test_refuses_when_spending_is_off(self):
        assert make_bot(spend=False).bulk_evict() is False

    def test_never_mistakes_the_job_centre_for_eviction(self, humans_page):
        url = make_bot().bulk_evict_url(wicket.parse(humans_page), URL)
        assert "vendor" not in url
