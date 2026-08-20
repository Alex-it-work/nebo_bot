"""Tests for multi-account configuration and selection."""

from __future__ import annotations

import pytest
import yaml

from src import config as config_module
from src.config import Config, ConfigError
from main import select_accounts

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
