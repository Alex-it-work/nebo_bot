"""Tests for configuration loading and validation."""

from __future__ import annotations

import logging
from datetime import time

import pytest
import yaml

from src import config as config_module
from src.config import Config, ConfigError, Delays

VALID = {
    "username": "Player",
    "password": "real-password",
    "base_url": "https://nebo.mobi",
    "timeout": 30,
    "delay_min": 1.5,
    "delay_max": 3.5,
    "log_level": "INFO",
}


def write_config(tmp_path, data):
    """Write a config mapping to a temporary file and return its path."""
    path = tmp_path / "config.yml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


class TestLoad:
    def test_loads_a_valid_file(self, tmp_path):
        config = config_module.load(write_config(tmp_path, VALID))
        assert config.username == "Player"
        assert config.password == "real-password"
        assert config.timeout == 30

    def test_applies_defaults_for_optional_keys(self, tmp_path):
        config = config_module.load(
            write_config(tmp_path, {"username": "Player", "password": "pw"})
        )
        assert config.base_url == "https://nebo.mobi"
        assert config.maze_target_level == 10
        assert config.delays.min_seconds == 1.5

    def test_reads_the_delay_settings_that_used_to_be_ignored(self, tmp_path):
        config = config_module.load(
            write_config(tmp_path, {**VALID, "delay_min": 4, "delay_max": 9})
        )
        assert config.delays.min_seconds == 4
        assert config.delays.max_seconds == 9

    def test_strips_a_trailing_slash_from_the_base_url(self, tmp_path):
        config = config_module.load(write_config(tmp_path, {**VALID, "base_url": "https://x.io/"}))
        assert config.base_url == "https://x.io"

    def test_reports_a_missing_file_clearly(self, tmp_path):
        with pytest.raises(ConfigError, match="not found"):
            config_module.load(tmp_path / "absent.yml")

    def test_rejects_an_empty_file(self, tmp_path):
        path = tmp_path / "config.yml"
        path.write_text("", encoding="utf-8")
        with pytest.raises(ConfigError, match="empty"):
            config_module.load(path)

    def test_rejects_malformed_yaml(self, tmp_path):
        path = tmp_path / "config.yml"
        path.write_text("username: [unclosed", encoding="utf-8")
        with pytest.raises(ConfigError, match="parse"):
            config_module.load(path)

    @pytest.mark.parametrize("missing", ["username", "password"])
    def test_requires_credentials(self, tmp_path, missing):
        data = {key: value for key, value in VALID.items() if key != missing}
        with pytest.raises(ConfigError, match=missing):
            config_module.load(write_config(tmp_path, data))

    def test_accepts_any_non_empty_password(self, tmp_path):
        # Passwords are opaque: "..." is a perfectly valid one, so nothing here
        # may second-guess the value the user entered.
        config = config_module.load(write_config(tmp_path, {**VALID, "password": "..."}))
        assert config.password == "..."

    def test_rejects_an_unknown_log_level(self, tmp_path):
        with pytest.raises(ConfigError, match="log_level"):
            config_module.load(write_config(tmp_path, {**VALID, "log_level": "LOUD"}))

    def test_accepts_a_lowercase_log_level(self, tmp_path):
        config = config_module.load(write_config(tmp_path, {**VALID, "log_level": "debug"}))
        assert config.numeric_log_level == logging.DEBUG

    def test_rejects_a_non_numeric_delay(self, tmp_path):
        with pytest.raises(ConfigError, match="delay_min"):
            config_module.load(write_config(tmp_path, {**VALID, "delay_min": "fast"}))


class TestActiveHours:
    def test_absent_means_no_restriction(self, tmp_path):
        assert config_module.load(write_config(tmp_path, VALID)).active_hours is None

    def test_parses_a_window(self, tmp_path):
        config = config_module.load(
            write_config(tmp_path, {**VALID, "active_hours": "09:00-23:30"})
        )
        assert config.active_hours == (time(9, 0), time(23, 30))

    def test_accepts_a_window_spanning_midnight(self, tmp_path):
        config = config_module.load(
            write_config(tmp_path, {**VALID, "active_hours": "22:00-02:00"})
        )
        assert config.active_hours == (time(22, 0), time(2, 0))

    def test_empty_string_means_no_restriction(self, tmp_path):
        assert config_module.load(
            write_config(tmp_path, {**VALID, "active_hours": ""})
        ).active_hours is None

    @pytest.mark.parametrize("value", ["09:00", "9-23", "09:00-", "25:00-26:00", "09:00-10:00-11:00"])
    def test_rejects_malformed_windows(self, tmp_path, value):
        with pytest.raises(ConfigError, match="active_hours"):
            config_module.load(write_config(tmp_path, {**VALID, "active_hours": value}))

    def test_rejects_an_empty_window(self, tmp_path):
        with pytest.raises(ConfigError, match="differ"):
            config_module.load(write_config(tmp_path, {**VALID, "active_hours": "09:00-09:00"}))


class TestSessionLimit:
    def test_defaults_to_unlimited(self, tmp_path):
        assert config_module.load(write_config(tmp_path, VALID)).session_max_minutes == 0

    def test_reads_the_limit(self, tmp_path):
        config = config_module.load(write_config(tmp_path, {**VALID, "session_max_minutes": 25}))
        assert config.session_max_minutes == 25


class TestDelays:
    def test_rejects_an_inverted_range(self):
        with pytest.raises(ConfigError, match="delay_min"):
            Delays(min_seconds=5, max_seconds=1)

    def test_rejects_an_inverted_page_load_range(self):
        with pytest.raises(ConfigError, match="page_load_min"):
            Delays(page_load_min=5, page_load_max=1)

    def test_rejects_negative_values(self):
        with pytest.raises(ConfigError, match="negative"):
            Delays(min_seconds=-1)

    def test_rejects_an_inverted_long_pause_range(self):
        with pytest.raises(ConfigError, match="long_pause_min"):
            Delays(long_pause_min=200, long_pause_max=10)

    @pytest.mark.parametrize("chance", [-0.1, 1.5])
    def test_rejects_an_impossible_probability(self, chance):
        with pytest.raises(ConfigError, match="long_pause_chance"):
            Delays(long_pause_chance=chance)

    def test_reads_the_long_pause_settings(self, tmp_path):
        config = config_module.load(
            write_config(
                tmp_path,
                {**VALID, "long_pause_chance": 0.2, "long_pause_min": 10, "long_pause_max": 40},
            )
        )
        assert config.delays.long_pause_chance == 0.2
        assert config.delays.long_pause_min == 10
        assert config.delays.long_pause_max == 40


class TestUrl:
    @pytest.mark.parametrize("path", ["/home", "home"])
    def test_builds_absolute_urls(self, path):
        config = Config(username="u", password="p", base_url="https://nebo.mobi")
        assert config.url(path) == "https://nebo.mobi/home"
