"""Tests for configuration loading and validation."""

from __future__ import annotations

import logging

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


class TestUrl:
    @pytest.mark.parametrize("path", ["/home", "home"])
    def test_builds_absolute_urls(self, path):
        config = Config(username="u", password="p", base_url="https://nebo.mobi")
        assert config.url(path) == "https://nebo.mobi/home"
