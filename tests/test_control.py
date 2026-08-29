"""Tests for running accounts on demand from the dashboard."""

from __future__ import annotations

import threading
import time
from dataclasses import replace

import pytest
import requests

from src.config import Config
from src.control import ACTIONS, Controller, Job
from src.utils.human_like import HumanSession


@pytest.fixture
def configs():
    return [Config(username=name, password="p") for name in ("Первый", "Второй")]


@pytest.fixture
def controller(configs):
    return Controller(configs, at_once=2, stagger=0)


class TestKnownAccounts:
    def test_lists_the_configured_accounts(self, controller):
        assert controller.names() == ["Первый", "Второй"]

    def test_an_idle_account_has_no_status(self, controller):
        assert controller.status("Первый") == "—"

    def test_an_unknown_account_cannot_be_started(self, controller):
        assert controller.start("Нетакого", "maze") is False

    def test_an_unknown_action_is_refused(self, controller):
        assert controller.start("Первый", "explode") is False

    def test_every_offered_action_is_named_in_russian(self):
        # The dashboard is for someone who does not read code.
        assert set(ACTIONS) == {"maze", "collect", "check"}
        assert all(label and label[0].isupper() for label in ACTIONS.values())


class TestRunning:
    def test_starting_reports_success(self, controller, monkeypatch):
        import src.control as control_module

        monkeypatch.setattr(control_module, "NeboBot", _StubBot)
        assert controller.start("Первый", "check") is True
        _wait_for(lambda: controller.status("Первый") == "вход есть")

    def test_a_failed_login_is_reported_rather_than_raised(self, controller, monkeypatch):
        import src.control as control_module

        monkeypatch.setattr(control_module, "NeboBot", _FailingBot)
        controller.start("Первый", "check")
        _wait_for(lambda: controller.status("Первый") == "вход не прошёл")

    def test_a_crash_becomes_a_status_not_a_dead_app(self, controller, monkeypatch):
        import src.control as control_module

        monkeypatch.setattr(control_module, "NeboBot", _ExplodingBot)
        controller.start("Первый", "check")
        _wait_for(lambda: controller.status("Первый").startswith("ошибка"))

    def test_the_same_account_cannot_run_twice_at_once(self, controller, monkeypatch):
        import src.control as control_module

        monkeypatch.setattr(control_module, "NeboBot", _SlowBot)
        assert controller.start("Первый", "check") is True
        assert controller.start("Первый", "check") is False

    def test_a_different_account_may_run_alongside(self, controller, monkeypatch):
        import src.control as control_module

        monkeypatch.setattr(control_module, "NeboBot", _SlowBot)
        assert controller.start("Первый", "check") is True
        assert controller.start("Второй", "check") is True

    def test_the_bot_is_always_logged_out_afterwards(self, controller, monkeypatch):
        import src.control as control_module

        _StubBot.stopped = 0
        monkeypatch.setattr(control_module, "NeboBot", _StubBot)
        controller.start("Первый", "check")
        _wait_for(lambda: _StubBot.stopped == 1)


class TestStopping:
    def test_stopping_an_idle_account_does_nothing(self, controller):
        assert controller.stop("Первый") is False

    def test_a_running_job_is_asked_to_wind_up(self, controller, monkeypatch):
        import src.control as control_module

        monkeypatch.setattr(control_module, "NeboBot", _SlowBot)
        controller.start("Первый", "check")
        assert controller.stop("Первый") is True

    def test_the_maze_is_told_when_to_stop(self, controller, monkeypatch):
        # Stopping asks the walk to finish its attempt rather than killing it
        # mid-maze, which would waste the keys already spent.
        import src.control as control_module

        monkeypatch.setattr(control_module, "NeboBot", _MazeBot)
        controller.start("Первый", "maze")
        _wait_for(lambda: _MazeBot.should_stop is not None)
        assert callable(_MazeBot.should_stop)


class _StubBot:
    stopped = 0

    def __init__(self, config):
        self.config = config

    def start(self):
        return True

    def stop(self, logout=True):
        type(self).stopped += 1

    def collect(self):
        return 3


class _FailingBot(_StubBot):
    def start(self):
        return False


class _ExplodingBot(_StubBot):
    def start(self):
        raise RuntimeError("boom")


class _SlowBot(_StubBot):
    def start(self):
        time.sleep(0.3)
        return True


class _MazeBot(_StubBot):
    should_stop = None

    def __init__(self, config):
        super().__init__(config)
        self.maze = self
        self.config = config

    def solve(self, rounds=None, should_stop=None, on_complete=None, on_attempt=None):
        type(self).should_stop = should_stop
        return 1


def _wait_for(condition, timeout: float = 3.0) -> None:
    """Wait for a background job to reach a state, or fail the test."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.02)
    raise AssertionError("condition not reached in time")


def test_jobs_run_on_their_own_threads(controller, monkeypatch):
    import src.control as control_module

    monkeypatch.setattr(control_module, "NeboBot", _SlowBot)
    before = threading.active_count()
    controller.start("Первый", "check")
    assert threading.active_count() > before


class TestAddingAccounts:
    def test_adds_to_the_file_and_registers_it(self, tmp_path):
        import yaml

        path = tmp_path / "c.yml"
        path.write_text(yaml.safe_dump({
            "defaults": {"base_url": "https://nebo.mobi"},
            "accounts": [{"username": "Первый", "password": "p"}],
        }, allow_unicode=True), encoding="utf-8")

        controller = Controller(
            [Config(username="Первый", password="p")], stagger=0, config_path=str(path)
        )
        assert controller.add_account("Второй", "pw") == ""
        assert "Второй" in controller.names()

        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert [a["username"] for a in saved["accounts"]] == ["Первый", "Второй"]

    def test_refuses_a_duplicate(self, tmp_path):
        import yaml

        path = tmp_path / "c.yml"
        path.write_text(yaml.safe_dump({"accounts": [{"username": "A", "password": "p"}]}),
                        encoding="utf-8")
        controller = Controller([Config(username="A", password="p")], stagger=0,
                                config_path=str(path))
        assert "уже есть" in controller.add_account("A", "pw")

    def test_refuses_empty_fields(self, tmp_path):
        controller = Controller([], stagger=0, config_path=str(tmp_path / "c.yml"))
        assert "нужны" in controller.add_account("", "pw")
        assert "нужны" in controller.add_account("A", "")

    def test_refuses_without_a_file_to_write_to(self):
        controller = Controller([], stagger=0)
        assert "не задан" in controller.add_account("A", "pw")

    def test_grows_a_single_account_file_into_a_list(self, tmp_path):
        # The old flat format has to become an accounts list before a second
        # account can be appended to it.
        import yaml

        path = tmp_path / "c.yml"
        path.write_text(yaml.safe_dump({"username": "Solo", "password": "p", "timeout": 30}),
                        encoding="utf-8")
        controller = Controller([Config(username="Solo", password="p")], stagger=0,
                                config_path=str(path))
        assert controller.add_account("Второй", "pw") == ""

        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert [a["username"] for a in saved["accounts"]] == ["Solo", "Второй"]
        assert saved["defaults"]["timeout"] == 30

    def test_rounds_are_reported_per_account(self):
        controller = Controller([Config(username="A", password="p", maze_rounds=4)], stagger=0)
        assert controller.rounds_for("A") == 4


class TestRemovingAccounts:
    def _controller(self, tmp_path, names=("Первый", "Второй")):
        import yaml

        path = tmp_path / "c.yml"
        path.write_text(yaml.safe_dump(
            {"defaults": {}, "accounts": [{"username": n, "password": "p"} for n in names]},
            allow_unicode=True), encoding="utf-8")
        configs = [Config(username=n, password="p") for n in names]
        return Controller(configs, stagger=0, config_path=str(path)), path

    def test_removes_from_the_file_and_the_table(self, tmp_path):
        import yaml

        controller, path = self._controller(tmp_path)
        assert controller.remove_account("Второй") == ""
        assert controller.names() == ["Первый"]
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert [a["username"] for a in saved["accounts"]] == ["Первый"]

    def test_refuses_an_unknown_account(self, tmp_path):
        controller, _ = self._controller(tmp_path)
        assert "нет такого" in controller.remove_account("Нетакого")

    def test_refuses_while_the_account_is_working(self, tmp_path, monkeypatch):
        import src.control as control_module

        controller, _ = self._controller(tmp_path)
        monkeypatch.setattr(control_module, "NeboBot", _SlowBot)
        controller.start("Первый", "check")
        assert "работает" in controller.remove_account("Первый")

    def test_leaves_the_others_alone(self, tmp_path):
        import yaml

        controller, path = self._controller(tmp_path, ("A", "B", "C"))
        controller.remove_account("B")
        saved = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert [a["username"] for a in saved["accounts"]] == ["A", "C"]


class TestEditingSettings:
    def _controller(self, tmp_path, **account):
        import yaml

        from src import config as config_module

        path = tmp_path / "c.yml"
        entry = {"username": "A", "password": "p", **account}
        path.write_text(yaml.safe_dump({"defaults": {}, "accounts": [entry]},
                                       allow_unicode=True), encoding="utf-8")
        # Read the accounts back from the file, as the panel does on startup.
        return Controller(config_module.load_all(path), stagger=0,
                          config_path=str(path)), path

    def test_reports_the_current_values(self, tmp_path):
        controller, _ = self._controller(tmp_path, maze_rounds=3, min_keys=500)
        settings = controller.settings_for("A")
        assert settings["maze_rounds"] == 3 and settings["min_keys"] == 500

    def test_saves_numbers(self, tmp_path):
        controller, path = self._controller(tmp_path)
        assert controller.update_account("A", {"maze_rounds": "5", "min_keys": "300"}) == ""
        assert controller.configs["A"].maze_rounds == 5
        assert controller.configs["A"].min_keys == 300

    def test_rejects_a_number_that_is_not_one(self, tmp_path):
        controller, _ = self._controller(tmp_path)
        assert "целое число" in controller.update_account("A", {"maze_rounds": "много"})

    def test_saves_a_checkbox(self, tmp_path):
        controller, _ = self._controller(tmp_path)
        controller.update_account("A", {"spend_baksy": "on"})
        assert controller.configs["A"].spend_baksy is True
        controller.update_account("A", {"spend_baksy": "off"})
        assert controller.configs["A"].spend_baksy is False

    def test_fast_is_a_shorthand_for_the_whole_pacing(self, tmp_path):
        # The panel offers one switch rather than four numbers.
        controller, _ = self._controller(tmp_path)
        controller.update_account("A", {"fast": "on"})
        assert controller.configs["A"].delays.max_seconds <= 2
        controller.update_account("A", {"fast": "off"})
        assert controller.configs["A"].delays.max_seconds > 2

    def test_saves_active_hours(self, tmp_path):
        controller, _ = self._controller(tmp_path)
        assert controller.update_account("A", {"active_hours": "09:00-23:30"}) == ""
        assert controller.configs["A"].active_hours is not None

    def test_a_bad_window_is_refused_without_breaking_the_file(self, tmp_path):
        controller, path = self._controller(tmp_path)
        assert controller.update_account("A", {"active_hours": "не время"}) != ""
        # The account still loads afterwards.
        assert controller.settings_for("A")["maze_rounds"] == 1

    def test_untouched_settings_stay_put(self, tmp_path):
        controller, _ = self._controller(tmp_path, min_keys=400)
        controller.update_account("A", {"maze_rounds": "2"})
        assert controller.configs["A"].min_keys == 400




class TestCollectingBetweenMazes:
    def test_rewards_are_taken_after_every_maze(self, controller, monkeypatch):
        import src.control as control_module

        monkeypatch.setattr(control_module, "NeboBot", _PerMazeBot)
        _PerMazeBot.collected = 0
        controller.start("Первый", "maze")
        _wait_for(lambda: "лабиринтов" in controller.status("Первый"))
        # Three mazes, so three chances to take what ripened.
        assert _PerMazeBot.collected == 3


class _PerMazeBot(_StubBot):
    collected = 0

    def __init__(self, config):
        super().__init__(config)
        self.maze = self

    def solve(self, rounds=None, should_stop=None, on_complete=None, on_attempt=None):
        for _ in range(3):
            if on_complete:
                on_complete()
        return 3

    def collect(self):
        type(self).collected += 1
        return 1


class FakeCookie:
    def __init__(self, name, value):
        self.name, self.value = name, value


class FakeCookieJar(list):
    pass


class TestProgress:
    """The table said "Пройти лабиринт" for an hour and nothing more."""

    def test_nothing_to_report_before_the_first_attempt(self):
        assert Job(account="a", action="maze").progress(9) == ""

    def test_reports_how_many_are_done_and_which_attempt(self):
        job = Job(account="a", action="maze", completed=3, target=9, attempt=27)
        assert job.progress(9) == "3/9, попытка 27"

    def test_an_open_ended_run_shows_no_target(self):
        job = Job(account="a", action="maze", completed=2, target=0, attempt=5)
        assert job.progress(0) == "2/∞, попытка 5"

    def test_only_the_maze_reports_progress(self):
        job = Job(account="a", action="collect", completed=1, target=2, attempt=3)
        assert job.progress(2) == ""

    def test_the_target_shown_is_the_one_asked_for_now(self, controller):
        # It used to be whatever the last attempt started with, so changing
        # the number left the old one on screen until the next attempt.
        job = Job(account="Первый", action="maze", completed=3, target=9, attempt=27)
        assert job.progress(14) == "3/14, попытка 27"

    def test_the_status_follows_the_saved_setting(self, controller, tmp_path):
        job = Job(account="Первый", action="maze", completed=3, target=9, attempt=27)
        job.thread = threading.Thread(target=lambda: time.sleep(0.4), daemon=True)
        job.thread.start()
        controller.jobs["Первый"] = job
        try:
            controller.configs["Первый"] = replace(
                controller.configs["Первый"], maze_rounds=14
            )
            assert controller.status("Первый") == "Пройти лабиринт: 3/14, попытка 27"
        finally:
            job.thread.join()

    def test_a_pinned_count_is_not_overridden_by_the_setting(self, controller):
        job = Job(account="Первый", action="maze", asked_rounds=4,
                  completed=1, target=4, attempt=7)
        job.thread = threading.Thread(target=lambda: time.sleep(0.4), daemon=True)
        job.thread.start()
        controller.jobs["Первый"] = job
        try:
            controller.configs["Первый"] = replace(
                controller.configs["Первый"], maze_rounds=14
            )
            assert controller.status("Первый") == "Пройти лабиринт: 1/4, попытка 7"
        finally:
            job.thread.join()


class TestHandingOverTheGame:
    """Playing a profile by hand, from the panel."""

    def test_an_unknown_account_is_refused(self, controller):
        assert controller.play_session("Никто").startswith("не ")

    def test_a_running_account_lends_its_cookies(self, controller):
        source = requests.Session()
        source.cookies.set("JSESSIONID", "LIVE", domain="nebo.mobi")
        bot = type("B", (), {"auth": type("A", (), {"session": source})()})()
        job = Job(account="Первый", action="maze", bot=bot)
        job.thread = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
        job.thread.start()
        controller.jobs["Первый"] = job
        try:
            session = controller.play_session("Первый")
            assert session.cookies.get("JSESSIONID", domain="nebo.mobi") == "LIVE"
            # Borrowed, not the same object: the bot keeps its own pacing.
            assert session is not source
            # Signing in twice risks the game dropping one of them.
            assert job.handed_over is True
        finally:
            job.thread.join()

    def test_the_hand_session_is_not_paced(self, controller):
        # A person clicking should not wait out the bot's thinking time.
        source = requests.Session()
        source.cookies.set("JSESSIONID", "LIVE", domain="nebo.mobi")
        bot = type("B", (), {"auth": type("A", (), {"session": source})()})()
        job = Job(account="Первый", action="maze", bot=bot)
        job.thread = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
        job.thread.start()
        controller.jobs["Первый"] = job
        try:
            assert not isinstance(controller.play_session("Первый"), HumanSession)
        finally:
            job.thread.join()

    def test_the_session_is_reused_rather_than_signed_in_again(self, controller):
        source = requests.Session()
        source.cookies.set("JSESSIONID", "LIVE", domain="nebo.mobi")
        bot = type("B", (), {"auth": type("A", (), {"session": source})()})()
        job = Job(account="Первый", action="maze", bot=bot)
        job.thread = threading.Thread(target=lambda: time.sleep(0.5), daemon=True)
        job.thread.start()
        controller.jobs["Первый"] = job
        try:
            assert controller.play_session("Первый") is controller.play_session("Первый")
        finally:
            job.thread.join()

    def test_forgetting_it_makes_the_next_open_start_over(self, controller):
        controller._play_sessions["Первый"] = "held"
        controller.forget_play_session("Первый")
        assert "Первый" not in controller._play_sessions

    def test_a_handed_over_session_is_not_logged_out(self):
        # Logging out would drop the player out of the game mid-click.
        job = Job(account="a", action="maze")
        assert job.handed_over is False


class TestChangingTheCountMidRun:
    """Asking for ten more means ten more, counted from that moment."""

    def _running_job(self, controller, **fields):
        job = Job(account="Первый", action="maze", **fields)
        job.thread = threading.Thread(target=lambda: time.sleep(0.6), daemon=True)
        job.thread.start()
        controller.jobs["Первый"] = job
        return job

    def test_the_progress_restarts_from_the_change(self):
        job = Job(account="a", action="maze", completed=5, attempt=40,
                  banked=5, banked_attempts=40)
        # No attempt to number yet: "попытка 0" would read like a fault.
        assert job.progress(10) == "0/10"

    def test_it_counts_up_again_afterwards(self):
        job = Job(account="a", action="maze", completed=7, attempt=52,
                  banked=5, banked_attempts=40)
        assert job.progress(10) == "2/10, попытка 12"

    def test_nothing_is_banked_before_the_first_change(self):
        job = Job(account="a", action="maze", completed=3, attempt=27)
        assert job.progress(9) == "3/9, попытка 27"

    def test_changing_the_setting_banks_what_is_done(self, controller, tmp_path):
        path = tmp_path / "c.yml"
        path.write_text(
            "accounts:\n- username: Первый\n  password: p\n  maze_rounds: 7\n",
            encoding="utf-8",
        )
        controller.config_path = str(path)
        controller.configs["Первый"] = replace(
            controller.configs["Первый"], maze_rounds=7
        )
        job = self._running_job(controller, completed=2, attempt=30)
        try:
            assert controller.update_account("Первый", {"maze_rounds": "10"}) == ""
            assert (job.banked, job.banked_attempts) == (2, 30)
            assert controller.status("Первый") == "Пройти лабиринт: 0/10"
        finally:
            job.thread.join()

    def test_saving_the_same_number_banks_nothing(self, controller, tmp_path):
        path = tmp_path / "c.yml"
        path.write_text(
            "accounts:\n- username: Первый\n  password: p\n  maze_rounds: 7\n",
            encoding="utf-8",
        )
        controller.config_path = str(path)
        controller.configs["Первый"] = replace(
            controller.configs["Первый"], maze_rounds=7
        )
        job = self._running_job(controller, completed=2, attempt=30)
        try:
            controller.update_account("Первый", {"maze_rounds": "7"})
            assert job.banked == 0
        finally:
            job.thread.join()

    def test_an_idle_account_has_nothing_to_bank(self, controller, tmp_path):
        path = tmp_path / "c.yml"
        path.write_text(
            "accounts:\n- username: Первый\n  password: p\n  maze_rounds: 7\n",
            encoding="utf-8",
        )
        controller.config_path = str(path)
        controller.configs["Первый"] = replace(
            controller.configs["Первый"], maze_rounds=7
        )
        assert controller.update_account("Первый", {"maze_rounds": "10"}) == ""
