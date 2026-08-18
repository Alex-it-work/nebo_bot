"""Loading and validation of the bot's YAML configuration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Any

import yaml

_VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigError(Exception):
    """Raised when the configuration file is missing, malformed or incomplete."""


@dataclass(frozen=True)
class Delays:
    """Timing envelope used to space out requests.

    ``min_seconds`` and ``max_seconds`` describe where most pauses land, not
    hard limits: pauses are drawn from a log-normal curve so a few run longer,
    the way a person's do. Occasionally a much longer break is inserted.

    Attributes:
        min_seconds: Roughly the 10th percentile of the pause between actions.
        max_seconds: Roughly the 90th percentile of the pause between actions.
        page_load_min: Lower bound of the simulated page reading pause.
        page_load_max: Upper bound of the simulated page reading pause.
        long_pause_chance: Probability that an action is preceded by a break.
        long_pause_min: Shortest such break.
        long_pause_max: Longest such break.
    """

    min_seconds: float = 1.5
    max_seconds: float = 3.5
    page_load_min: float = 0.3
    page_load_max: float = 1.2
    long_pause_chance: float = 0.04
    long_pause_min: float = 20.0
    long_pause_max: float = 120.0

    def __post_init__(self) -> None:
        if self.min_seconds < 0 or self.page_load_min < 0 or self.long_pause_min < 0:
            raise ConfigError("Delays cannot be negative")
        if self.min_seconds > self.max_seconds:
            raise ConfigError(
                f"delay_min ({self.min_seconds}) must not exceed delay_max ({self.max_seconds})"
            )
        if self.page_load_min > self.page_load_max:
            raise ConfigError(
                f"page_load_min ({self.page_load_min}) must not exceed "
                f"page_load_max ({self.page_load_max})"
            )
        if self.long_pause_min > self.long_pause_max:
            raise ConfigError(
                f"long_pause_min ({self.long_pause_min}) must not exceed "
                f"long_pause_max ({self.long_pause_max})"
            )
        if not 0.0 <= self.long_pause_chance <= 1.0:
            raise ConfigError(
                f"long_pause_chance must be between 0 and 1, got {self.long_pause_chance}"
            )


@dataclass(frozen=True)
class Config:
    """Fully validated bot configuration.

    Attributes:
        username: In-game name used to log in.
        password: Account password.
        base_url: Site root, without a trailing slash.
        timeout: Per-request timeout in seconds.
        delays: Timing envelope for pacing requests.
        log_level: Logging level name.
        log_file: Path of the log file, or None to log to stdout only.
        maze_target_level: Depth at which the maze counts as solved.
        maze_max_attempts: Maximum maze runs before giving up, 0 for unlimited.
        maze_memory_file: Where door knowledge is stored between runs, or None
            to keep it in memory only.
        session_max_minutes: How long one run may play before stopping, 0 for
            no limit. A session that never ends is the least human thing a bot
            can do.
        active_hours: Window during which the bot may play, as
            ``(start, end)``, or None to allow any time. May span midnight.
    """

    username: str
    password: str
    base_url: str = "https://nebo.mobi"
    timeout: int = 30
    delays: Delays = Delays()
    log_level: str = "INFO"
    log_file: str | None = "logs/nebo_bot.log"
    maze_target_level: int = 10
    maze_max_attempts: int = 0
    maze_memory_file: str | None = "data/maze_memory.json"
    session_max_minutes: int = 0
    active_hours: tuple[time, time] | None = None

    @property
    def numeric_log_level(self) -> int:
        """The log level as a ``logging`` module constant."""
        return getattr(logging, self.log_level)

    def url(self, path: str) -> str:
        """Build an absolute URL for a site-relative path."""
        return f"{self.base_url}/{path.lstrip('/')}"


def load(config_path: str | Path) -> Config:
    """Read and validate the configuration file.

    Args:
        config_path: Path to the YAML configuration file.

    Returns:
        The validated configuration.

    Raises:
        ConfigError: If the file is missing, unparseable, or fails validation.
    """
    path = Path(config_path)

    if not path.is_file():
        raise ConfigError(
            f"Configuration file not found: {path}. "
            "Copy config/config.example.yml to config/config.yml and fill it in."
        )

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Could not parse {path}: {exc}") from exc

    if raw is None:
        raise ConfigError(f"Configuration file {path} is empty")
    if not isinstance(raw, dict):
        raise ConfigError(f"Configuration file {path} must contain a mapping at the top level")

    return _from_mapping(raw, path)


def _from_mapping(raw: dict[str, Any], path: Path) -> Config:
    """Convert a raw YAML mapping into a validated Config."""
    username = raw.get("username")
    password = raw.get("password")

    for name, value in (("username", username), ("password", password)):
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"'{name}' is required in {path} and must be a non-empty string")

    log_level = str(raw.get("log_level", "INFO")).upper()
    if log_level not in _VALID_LOG_LEVELS:
        raise ConfigError(
            f"'log_level' must be one of {sorted(_VALID_LOG_LEVELS)}, got {log_level!r}"
        )

    log_file = raw.get("log_file", "logs/nebo_bot.log")
    if log_file is not None and not isinstance(log_file, str):
        raise ConfigError("'log_file' must be a string or empty")

    memory_file = raw.get("maze_memory_file", "data/maze_memory.json")
    if memory_file is not None and not isinstance(memory_file, str):
        raise ConfigError("'maze_memory_file' must be a string or empty")

    delays = Delays(
        min_seconds=_number(raw, "delay_min", 1.5),
        max_seconds=_number(raw, "delay_max", 3.5),
        page_load_min=_number(raw, "page_load_min", 0.3),
        page_load_max=_number(raw, "page_load_max", 1.2),
        long_pause_chance=_number(raw, "long_pause_chance", 0.04),
        long_pause_min=_number(raw, "long_pause_min", 20.0),
        long_pause_max=_number(raw, "long_pause_max", 120.0),
    )

    return Config(
        username=username.strip(),
        password=password,
        base_url=str(raw.get("base_url", "https://nebo.mobi")).rstrip("/"),
        timeout=int(_number(raw, "timeout", 30)),
        delays=delays,
        log_level=log_level,
        log_file=log_file or None,
        maze_target_level=int(_number(raw, "maze_target_level", 10)),
        maze_max_attempts=int(_number(raw, "maze_max_attempts", 0)),
        maze_memory_file=memory_file or None,
        session_max_minutes=int(_number(raw, "session_max_minutes", 0)),
        active_hours=_active_hours(raw.get("active_hours")),
    )


def _active_hours(value: Any) -> tuple[time, time] | None:
    """Parse an ``"HH:MM-HH:MM"`` activity window.

    Returns:
        The start and end of the window, or None when unset.

    Raises:
        ConfigError: If the value is not a well-formed window.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ConfigError(f"'active_hours' must be a string like \"09:00-23:30\", got {value!r}")

    parts = value.split("-")
    if len(parts) != 2:
        raise ConfigError(f"'active_hours' must look like \"09:00-23:30\", got {value!r}")

    try:
        start, end = (datetime.strptime(part.strip(), "%H:%M").time() for part in parts)
    except ValueError as exc:
        raise ConfigError(f"Could not read 'active_hours' {value!r}: {exc}") from exc

    if start == end:
        raise ConfigError("'active_hours' start and end must differ")
    return start, end


def _number(raw: dict[str, Any], key: str, default: float) -> float:
    """Read a numeric option, falling back to a default when absent."""
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"'{key}' must be a number, got {value!r}")
    return float(value)
