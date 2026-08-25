"""Tests for multi-account configuration and selection."""

from __future__ import annotations

import pytest
import yaml

from src import config as config_module
from src.config import Config, ConfigError
from main import apply_overrides, select_accounts

MULTI = {
    "defaults": {"delay_min": 2, "delay_max": 4, "maze_rounds": 1},
    "accounts": [
        {"username": "First", "password": "pw1", "maze_rounds": 3},
        {"username": "Second", "password": "pw2"},
        {"username": "Third", "password": "pw3", "delay_min": 9, "delay_max": 9},
    ],
}


def write(tmp_path, data):
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


class TestLoadAll:
    def test_reads_every_account(self, tmp_path):
        configs = config_module.load_all(write(tmp_path, MULTI))
        assert [c.username for c in configs] == ["First", "Second", "Third"]

    def test_each_account_keeps_its_own_password(self, tmp_path):
        configs = config_module.load_all(write(tmp_path, MULTI))
        assert [c.password for c in configs] == ["pw1", "pw2", "pw3"]

    def test_defaults_apply_where_not_overridden(self, tmp_path):
        second = config_module.load_all(write(tmp_path, MULTI))[1]
        assert second.maze_rounds == 1
        assert second.delays.min_seconds == 2

    def test_an_account_overrides_the_defaults(self, tmp_path):
        first, _, third = config_module.load_all(write(tmp_path, MULTI))
        assert first.maze_rounds == 3
        assert third.delays.min_seconds == 9

    def test_a_flat_file_still_describes_one_account(self, tmp_path):
        # The single-account format has to keep working unchanged.
        configs = config_module.load_all(write(tmp_path, {"username": "Solo", "password": "pw"}))
        assert len(configs) == 1
        assert configs[0].username == "Solo"

    def test_names_the_account_that_fails_validation(self, tmp_path):
        broken = {"accounts": [{"username": "Good", "password": "pw"}, {"username": "Bad"}]}
        with pytest.raises(ConfigError, match="Bad"):
            config_module.load_all(write(tmp_path, broken))

    def test_rejects_a_duplicated_account(self, tmp_path):
        twice = {"accounts": [{"username": "Same", "password": "a"},
                              {"username": "Same", "password": "b"}]}
        with pytest.raises(ConfigError, match="more than once"):
            config_module.load_all(write(tmp_path, twice))

    def test_rejects_an_empty_account_list(self, tmp_path):
        with pytest.raises(ConfigError, match="non-empty"):
            config_module.load_all(write(tmp_path, {"accounts": []}))

    def test_rejects_a_malformed_entry(self, tmp_path):
        with pytest.raises(ConfigError, match="mapping"):
            config_module.load_all(write(tmp_path, {"accounts": ["just a string"]}))


class TestSelectAccounts:
    @pytest.fixture
    def configs(self):
        return [Config(username=name, password="pw") for name in ("First", "Second", "Third")]

    def test_no_selection_means_all(self, configs):
        assert select_accounts(configs, None) == configs

    def test_selects_one(self, configs):
        assert [c.username for c in select_accounts(configs, ["Second"])] == ["Second"]

    def test_selects_several_and_keeps_file_order(self, configs):
        chosen = select_accounts(configs, ["Third", "First"])
        assert [c.username for c in chosen] == ["First", "Third"]

    def test_reports_an_unknown_name(self, configs):
        with pytest.raises(ConfigError, match="Nobody"):
            select_accounts(configs, ["Nobody"])


class TestOutputEncoding:
    def test_listing_cyrillic_names_does_not_crash(self, tmp_path, capsys, monkeypatch):
        # --list-accounts prints before logging is configured, so the UTF-8
        # switch has to happen first or Cyrillic names raise on Windows.
        import main as main_module

        config = write(tmp_path, {"accounts": [{"username": "Профиль А", "password": "pw"}]})
        assert main_module.main(["-c", str(config), "--list-accounts"]) == 0
        assert "Профиль А" in capsys.readouterr().out


class TestRoundsOverride:
    def test_leaves_configs_alone_without_the_flag(self):
        configs = [Config(username="A", password="p", maze_rounds=3)]
        assert apply_overrides(configs, None)[0].maze_rounds == 3

    def test_overrides_every_selected_account(self):
        configs = [Config(username="A", password="p", maze_rounds=3),
                   Config(username="B", password="p", maze_rounds=1)]
        assert [c.maze_rounds for c in apply_overrides(configs, 4)] == [4, 4]

    def test_zero_means_unlimited(self):
        assert apply_overrides([Config(username="A", password="p")], 0)[0].maze_rounds == 0

    def test_rejects_a_negative_count(self):
        with pytest.raises(ConfigError, match="negative"):
            apply_overrides([Config(username="A", password="p")], -1)

    def test_fast_replaces_the_delays(self):
        from main import FAST_DELAYS

        configs = [Config(username="A", password="p")]
        assert apply_overrides(configs, None, fast=True)[0].delays == FAST_DELAYS

    def test_normal_pace_is_left_alone(self):
        original = Config(username="A", password="p")
        assert apply_overrides([original], None, fast=False)[0].delays == original.delays

    def test_rounds_and_pace_apply_together(self):
        result = apply_overrides([Config(username="A", password="p")], 4, fast=True)[0]
        assert result.maze_rounds == 4 and result.delays.max_seconds == 1.5


class TestParallelRun:
    def test_sequential_keeps_file_order(self, monkeypatch):
        import main as main_module

        played = []
        monkeypatch.setattr(main_module, "run_account",
                            lambda config, login_only, collect_only=False: played.append(config.username) or True)
        configs = [Config(username=n, password="p") for n in ("A", "B", "C")]
        result = main_module.run_all(configs, login_only=False, parallel=None)
        assert played == ["A", "B", "C"] and result == {"A": True, "B": True, "C": True}

    def test_parallel_reports_every_account(self, monkeypatch):
        import main as main_module

        monkeypatch.setattr(main_module, "run_account",
                            lambda config, login_only, collect_only=False: config.username != "B")
        configs = [Config(username=n, password="p") for n in ("A", "B", "C")]
        result = main_module.run_all(configs, login_only=False, parallel=3, stagger=0)
        assert result == {"A": True, "B": False, "C": True}

    def test_never_exceeds_the_limit(self, monkeypatch):
        import threading
        import time as time_module

        import main as main_module

        running = 0
        peak = 0
        lock = threading.Lock()

        def play(config, login_only, collect_only=False):
            nonlocal running, peak
            with lock:
                running += 1
                peak = max(peak, running)
            time_module.sleep(0.05)
            with lock:
                running -= 1
            return True

        monkeypatch.setattr(main_module, "run_account", play)
        configs = [Config(username=f"A{n}", password="p") for n in range(12)]
        main_module.run_all(configs, login_only=False, parallel=3, stagger=0)
        # Thirty sessions opening at once is a very different load than three.
        assert peak <= 3

    def test_a_limit_of_one_runs_them_in_order(self, monkeypatch):
        import main as main_module

        played = []
        monkeypatch.setattr(main_module, "run_account",
                            lambda config, login_only, collect_only=False: played.append(config.username) or True)
        configs = [Config(username=n, password="p") for n in ("A", "B", "C")]
        main_module.run_all(configs, login_only=False, parallel=1, stagger=0)
        assert played == ["A", "B", "C"]


class TestPerAccountLogs:
    def test_a_record_reaches_only_its_own_account(self):
        import logging

        from main import AccountFilter

        record = logging.LogRecord("x", logging.INFO, "f", 1, "msg", None, None)
        record.threadName = "Первый"
        assert AccountFilter("Первый").filter(record) is True
        assert AccountFilter("Второй").filter(record) is False

    def test_awkward_names_become_usable_filenames(self):
        from main import safe_filename

        assert safe_filename("Super Allex") == "Super Allex"
        assert safe_filename("сосочки") == "сосочки"
        assert safe_filename("a/b:c*?") == "a_b_c_"

    def test_punctuation_becomes_a_usable_name(self):
        from main import safe_filename

        assert safe_filename("///") == "_"

    def test_an_empty_name_still_yields_a_file(self):
        from main import safe_filename

        assert safe_filename("   ") == "account"

    def test_writes_one_file_per_account(self, tmp_path):
        import logging

        import main as main_module

        config = Config(username="A", password="p", log_file=str(tmp_path / "bot.log"))
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        main_module.setup_logging(config, ["Первый", "Второй"])
        try:
            names = {p.name for p in tmp_path.iterdir()}
            assert names == {"bot.log", "Первый.log", "Второй.log"}
        finally:
            for handler in logging.root.handlers[:]:
                handler.close()
                logging.root.removeHandler(handler)


class TestHumanArrival:
    def test_a_settle_pause_belongs_to_logging_in(self):
        # It lived in the NeboBot wrapper, so anything calling Auth.login()
        # directly went straight from arriving to playing in three seconds.
        from src.modules import auth as auth_module

        assert auth_module._SETTLE_MULTIPLIER > 1

    def test_logging_in_pauses_before_returning(self, monkeypatch, login_page):
        from src.config import Config as C, Delays
        from src.modules.auth import Auth
        from tests.test_auth import FakeResponse, FakeSession

        no_delays = Delays(min_seconds=0, max_seconds=0, page_load_min=0, page_load_max=0)
        session = FakeSession(
            get_responses={
                "/login": FakeResponse(login_page, url="https://nebo.mobi/login"),
                "/home": FakeResponse(url="https://nebo.mobi/home", status_code=200),
            }
        )
        auth = Auth(C(username="u", password="p", delays=no_delays), session=session)
        paused: list[float] = []
        monkeypatch.setattr(auth.human, "pause", lambda m=1.0: paused.append(m))

        assert auth.login() is True
        # A long pause before returning, not just the ordinary ones.
        assert max(paused) > 1

    def test_starts_are_staggered(self):
        import main as main_module

        assert main_module.STAGGER_SECONDS > 0

    def test_parallel_does_not_release_everyone_at_once(self, monkeypatch):
        import main as main_module

        seen: list[float] = []
        monkeypatch.setattr(main_module.time, "sleep", lambda s: seen.append(s))
        monkeypatch.setattr(main_module, "run_account", lambda config, login_only, collect_only=False: True)
        configs = [Config(username=f"A{n}", password="p") for n in range(4)]
        main_module.run_all(configs, login_only=False, parallel=4, stagger=0.3)
        assert len(seen) == 4 and len(set(seen)) > 1

    def test_starts_can_be_released_together_when_asked(self, monkeypatch):
        import main as main_module

        slept = []
        monkeypatch.setattr(main_module.time, "sleep", lambda s: slept.append(s))
        monkeypatch.setattr(main_module, "run_account", lambda config, login_only, collect_only=False: True)
        configs = [Config(username=f"A{n}", password="p") for n in range(3)]
        main_module.run_all(configs, login_only=False, parallel=3, stagger=0)
        assert slept == []


class TestShuffledErrands:
    def test_the_order_of_errands_varies(self, monkeypatch):
        # Doing the same things in the same order every run is its own
        # signature, whatever the timings look like.
        from src.bot import NeboBot
        from src.config import Config as C

        orders = set()
        for _ in range(30):
            bot = NeboBot(C(username="u", password="p"))
            done: list[str] = []
            monkeypatch.setattr(bot, "_read_quests", lambda: done.append("quests"))
            monkeypatch.setattr(bot.auth.wanderer, "maybe_wander", lambda: done.append("wander"))
            monkeypatch.setattr(bot.auth, "is_authenticated", lambda: True)
            monkeypatch.setattr(bot.maze, "solve", lambda: 0)
            bot.run()
            orders.add(tuple(done))
        assert len(orders) > 1


class TestCollecting:
    def test_collect_takes_rewards_without_playing(self, monkeypatch):
        from src.bot import NeboBot
        from src.config import Config as C

        bot = NeboBot(C(username="u", password="p"))
        played = []
        monkeypatch.setattr(bot.maze, "solve", lambda *a, **k: played.append("maze") or 0)
        monkeypatch.setattr(bot.quests, "claim_all", lambda page: 1)
        monkeypatch.setattr(bot, "_keys", lambda: 100)
        assert bot.collect() == 2
        assert played == []

    def test_collects_from_both_task_pages(self, monkeypatch):
        # Personal tasks and the marathon are separate pages with separate
        # rewards, and the marathon pays keys too.
        from src.bot import NeboBot
        from src.config import Config as C

        bot = NeboBot(C(username="u", password="p"))
        pages = []
        monkeypatch.setattr(bot.quests, "claim_all", lambda page: pages.append(page) or 0)
        monkeypatch.setattr(bot, "_keys", lambda: 0)
        bot.collect()
        assert pages == ["/quests", "/tasks"]

    def test_reports_the_keys_that_arrived(self, monkeypatch, caplog):
        import logging

        from src.bot import NeboBot
        from src.config import Config as C

        bot = NeboBot(C(username="u", password="p"))
        counts = iter([800, 855])
        monkeypatch.setattr(bot.quests, "claim_all", lambda page: 1)
        monkeypatch.setattr(bot, "_keys", lambda: next(counts))
        with caplog.at_level(logging.INFO):
            bot.collect()
        assert "+55" in caplog.text

    def test_a_network_failure_does_not_stop_the_other_page(self, monkeypatch):
        import requests as requests_module

        from src.bot import NeboBot
        from src.config import Config as C

        bot = NeboBot(C(username="u", password="p"))
        seen = []

        def claim(page):
            seen.append(page)
            if page == "/quests":
                raise requests_module.ConnectionError("down")
            return 1

        monkeypatch.setattr(bot.quests, "claim_all", claim)
        monkeypatch.setattr(bot, "_keys", lambda: 0)
        assert bot.collect() == 1
        assert seen == ["/quests", "/tasks"]
