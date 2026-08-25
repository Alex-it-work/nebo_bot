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


class TestLivePorts:
    def test_one_watched_account_keeps_its_port(self):
        from main import separate_live_ports

        configs = [Config(username="A", password="p", live_view=True, live_port=8765)]
        assert separate_live_ports(configs)[0].live_port == 8765

    def test_two_watched_accounts_get_different_ports(self):
        # Sharing a config means sharing a port, and the second bot to start
        # would fail to bind.
        from main import separate_live_ports

        configs = [Config(username=n, password="p", live_view=True, live_port=8765)
                   for n in ("A", "B", "C")]
        ports = [c.live_port for c in separate_live_ports(configs)]
        assert ports == [8765, 8766, 8767]

    def test_unwatched_accounts_are_left_alone(self):
        from main import separate_live_ports

        configs = [Config(username="A", password="p", live_view=True, live_port=8765),
                   Config(username="B", password="p", live_view=False, live_port=8765),
                   Config(username="C", password="p", live_view=True, live_port=8765)]
        result = separate_live_ports(configs)
        assert [c.live_port for c in result] == [8765, 8765, 8766]

    def test_nothing_changes_when_nobody_is_watched(self):
        from main import separate_live_ports

        configs = [Config(username="A", password="p"), Config(username="B", password="p")]
        assert separate_live_ports(configs) == configs


class TestParallelRun:
    def test_sequential_keeps_file_order(self, monkeypatch):
        import main as main_module

        played = []
        monkeypatch.setattr(main_module, "run_account",
                            lambda config, login_only: played.append(config.username) or True)
        configs = [Config(username=n, password="p") for n in ("A", "B", "C")]
        result = main_module.run_all(configs, login_only=False, parallel=False)
        assert played == ["A", "B", "C"] and result == {"A": True, "B": True, "C": True}

    def test_parallel_reports_every_account(self, monkeypatch):
        import main as main_module

        monkeypatch.setattr(main_module, "run_account",
                            lambda config, login_only: config.username != "B")
        configs = [Config(username=n, password="p") for n in ("A", "B", "C")]
        result = main_module.run_all(configs, login_only=False, parallel=True)
        assert result == {"A": True, "B": False, "C": True}

    def test_parallel_actually_overlaps(self, monkeypatch):
        import threading
        import time as time_module

        import main as main_module

        started = threading.Barrier(3, timeout=5)

        def play(config, login_only):
            # Times out unless all three are running at once.
            started.wait()
            time_module.sleep(0.01)
            return True

        monkeypatch.setattr(main_module, "run_account", play)
        configs = [Config(username=n, password="p") for n in ("A", "B", "C")]
        assert main_module.run_all(configs, login_only=False, parallel=True) == {
            "A": True, "B": True, "C": True
        }
