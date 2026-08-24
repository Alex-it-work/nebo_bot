"""Tests for reading the personal task page."""

from __future__ import annotations

from src import wicket
from src.config import Config, Delays
from src.modules.auth import Auth
from src.modules.quests import QuestBot
from tests.test_auth import FakeSession

NO_DELAYS = Delays(min_seconds=0, max_seconds=0, page_load_min=0, page_load_max=0)


def make_bot():
    config = Config(username="u", password="p", delays=NO_DELAYS)
    return QuestBot(Auth(config, session=FakeSession()), config)


def by_name(quests, name):
    return next(q for q in quests if q.name == name)


class TestParse:
    def test_reads_every_task(self, quests_page):
        quests = make_bot().parse(wicket.parse(quests_page))
        assert [q.name for q in quests] == [
            "Инкассатор", "Новые жители", "Инвестор", "Индиана Джонс", "Легкие деньги",
        ]

    def test_reads_progress(self, quests_page):
        task = by_name(make_bot().parse(wicket.parse(quests_page)), "Инкассатор")
        assert (task.done, task.total) == (149, 150)
        assert task.remaining == 1

    def test_reads_the_description(self, quests_page):
        task = by_name(make_bot().parse(wicket.parse(quests_page)), "Инкассатор")
        assert task.description == "Собери выручку со 150 товаров"

    def test_recognises_a_finished_task(self, quests_page):
        assert by_name(make_bot().parse(wicket.parse(quests_page)), "Новые жители").complete

    def test_an_unfinished_task_is_not_complete(self, quests_page):
        assert not by_name(make_bot().parse(wicket.parse(quests_page)), "Инкассатор").complete


class TestCooldown:
    def test_reads_hours_and_minutes(self, quests_page):
        task = by_name(make_bot().parse(wicket.parse(quests_page)), "Индиана Джонс")
        assert task.on_cooldown and task.minutes_left == 15 * 60 + 33

    def test_reads_minutes_only(self, quests_page):
        assert by_name(make_bot().parse(wicket.parse(quests_page)), "Легкие деньги").minutes_left == 45

    def test_a_waiting_task_is_never_complete(self, quests_page):
        assert not by_name(make_bot().parse(wicket.parse(quests_page)), "Индиана Джонс").complete

    def test_an_active_task_has_no_cooldown(self, quests_page):
        assert by_name(make_bot().parse(wicket.parse(quests_page)), "Инкассатор").minutes_left is None


class TestPaidTasks:
    def test_flags_a_task_wanting_real_money(self, quests_page):
        # "Пополни счет на 200 баксов" costs actual money; the bot must not act.
        assert by_name(make_bot().parse(wicket.parse(quests_page)), "Инвестор").paid

    def test_ordinary_tasks_are_not_flagged(self, quests_page):
        assert not by_name(make_bot().parse(wicket.parse(quests_page)), "Инкассатор").paid


class TestPageSummary:
    def test_reads_the_daily_count(self, quests_page):
        assert make_bot().done_today(wicket.parse(quests_page)) == (7, 7)

    def test_reads_keys_earned(self, quests_page):
        assert make_bot().keys_earned(wicket.parse(quests_page)) == 57

    def test_no_summary_on_another_page(self, home_page):
        assert make_bot().keys_earned(wicket.parse(home_page)) is None


class TestNextAvailable:
    def test_zero_while_something_is_active(self, quests_page):
        quests = make_bot().parse(wicket.parse(quests_page))
        assert make_bot().next_available_in(quests) == 0

    def test_the_soonest_cooldown_when_all_are_waiting(self, quests_page):
        bot = make_bot()
        waiting = [q for q in bot.parse(wicket.parse(quests_page)) if q.on_cooldown]
        assert bot.next_available_in(waiting) == 45

    def test_none_when_the_page_lists_nothing(self):
        assert make_bot().next_available_in([]) is None


class TestDescriptionParsing:
    def test_a_task_without_a_description_gets_an_empty_one(self):
        # Marathon-style entries carry no description div; matching a wrapper
        # instead returned the title with the progress line glued on.
        page = '''<div class="nfl"><div><b>Золотой строитель</b></div>
        <div class="minor small nshd">Прогресс: <span>3</span> из <span>5</span></div></div>'''
        task = make_bot().parse(wicket.parse(page))[0]
        assert task.name == "Золотой строитель"
        assert task.description == ""
        assert (task.done, task.total) == (3, 5)

    def test_cooldown_description_comes_from_the_grey_line(self, quests_page):
        task = by_name(make_bot().parse(wicket.parse(quests_page)), "Индиана Джонс")
        assert task.description == "Пройди лабиринт 1 раз"


class TestClaimable:
    def test_finds_the_reward_link_of_a_finished_task(self, quests_page):
        # Finishing is not collecting: the reward needs a separate click, and
        # the 20-hour cooldown only starts once it is taken.
        urls = make_bot().claimable(wicket.parse(quests_page), "https://nebo.mobi/quests")
        assert urls == ["https://nebo.mobi/quests?3-1.-completedQuests-1-quest-getAwarLink"]

    def test_ignores_tasks_still_in_progress(self, quests_page):
        urls = make_bot().claimable(wicket.parse(quests_page), "https://nebo.mobi/quests")
        assert len(urls) == 1

    def test_nothing_to_claim_elsewhere(self, home_page):
        assert make_bot().claimable(wicket.parse(home_page), "https://nebo.mobi/home") == []

    def test_matches_the_games_own_misspelling(self, quests_page):
        # The component is "getAwarLink", not "getAwardLink".
        urls = make_bot().claimable(wicket.parse(quests_page), "https://nebo.mobi/quests")
        assert "getAwarLink" in urls[0]

    def test_matches_both_spellings_the_game_uses(self):
        # /quests renders "getAwarLink", /tasks renders "getAwardLink".
        page = '''<a href="./quests?3-1.-completedQuests-0-quest-getAwarLink">Получить награду!</a>
        <a href="./tasks?11-1.-tasks-2-task-taskBlock-getAwardLink">Получить награду!</a>'''
        urls = make_bot().claimable(wicket.parse(page), "https://nebo.mobi/quests")
        assert len(urls) == 2
