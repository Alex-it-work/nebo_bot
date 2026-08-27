"""Tests for running accounts on demand from the dashboard."""

from __future__ import annotations

import threading
import time

import pytest

from src.config import Config
from src.control import ACTIONS, Controller


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

    def stop(self):
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

    def solve(self, rounds=None, should_stop=None):
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
