"""Running accounts on demand, so the dashboard can drive the bot.

Everything the bot does is otherwise reached through command line flags, which
is fine for whoever wrote them and useless for anyone else. This turns those
same actions into jobs that can be started and stopped by name, one per
account, with the concurrency limit and the pacing left exactly as they are.

A job is a thread. Stopping asks it to finish the step it is on rather than
killing it, because a half-finished maze move is worse than a slow exit.
"""

from __future__ import annotations

import logging
import random
import pathlib
import threading
import time
from dataclasses import dataclass, field

import yaml

from .bot import NeboBot
from .config import Config, ConfigError
from . import config as config_module

logger = logging.getLogger(__name__)

# Actions the dashboard can start. Each maps to a method of this controller.
ACTIONS = {
    "maze": "Пройти лабиринт",
    "collect": "Забрать награды",
    "check": "Проверить вход",
}


@dataclass
class Job:
    """One account's running work."""

    account: str
    action: str
    thread: threading.Thread | None = None
    stopping: threading.Event = field(default_factory=threading.Event)
    finished: bool = False
    result: str = ""

    @property
    def running(self) -> bool:
        """Whether the job is still going."""
        return self.thread is not None and self.thread.is_alive()


class Controller:
    """Starts and stops per-account jobs on behalf of the dashboard."""

    def __init__(
        self,
        configs: list[Config],
        at_once: int = 3,
        stagger: float = 20.0,
        config_path: str | None = None,
    ):
        """Prepare to run the given accounts.

        Args:
            configs: Every configured account.
            at_once: How many may play at the same time.
            stagger: Seconds to spread starts over, so a batch of accounts
                does not sign in in lockstep.
            config_path: File to write to when an account is added from the
                panel. Adding is refused without it.
        """
        self.configs = {config.username: config for config in configs}
        self.config_path = config_path
        self.jobs: dict[str, Job] = {}
        self.stagger = stagger
        self._permit = threading.Semaphore(max(1, at_once))
        self._lock = threading.Lock()

    def names(self) -> list[str]:
        """Every account this controller knows about, in file order."""
        return list(self.configs)

    def status(self, account: str) -> str:
        """A short word for what this account is doing."""
        job = self.jobs.get(account)
        if job is None:
            return "—"
        if job.running:
            return ACTIONS.get(job.action, job.action)
        return job.result or "готово"

    def rounds_for(self, account: str) -> int:
        """How many mazes this account plays when asked to play."""
        config = self.configs.get(account)
        return config.maze_rounds if config else 0

    def add_account(self, username: str, password: str) -> str:
        """Add an account to the configuration file and register it.

        Returns:
            An empty string on success, or why it was refused.
        """
        username, password = username.strip(), password.strip()
        if not username or not password:
            return "нужны имя и пароль"
        if username in self.configs:
            return "такой профиль уже есть"
        if self.config_path is None:
            return "не задан файл настроек"

        path = pathlib.Path(self.config_path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            return f"не читается файл настроек: {exc}"

        # A file written as a single account has to grow an accounts list
        # before another one can be appended to it.
        if "accounts" not in raw:
            single = {key: raw.pop(key) for key in ("username", "password") if key in raw}
            raw = {"defaults": raw, "accounts": [single] if single else []}
        raw["accounts"].append({"username": username, "password": password})

        try:
            path.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            added = [c for c in config_module.load_all(path) if c.username == username]
        except (OSError, ConfigError, yaml.YAMLError) as exc:
            return f"не сохранилось: {exc}"

        if not added:
            return "не удалось прочитать добавленный профиль"
        self.configs[username] = added[0]
        logger.info("Added account %s from the panel", username)
        return ""

    # What the panel may change, with the label it shows and how to read it.
    EDITABLE = {
        "maze_rounds": ("Кругов за запуск", int),
        "min_keys": ("Не тратить ключи ниже", int),
        "active_hours": ("Часы работы", str),
        "spend_baksy": ("Тратить баксы на ускорения", bool),
        "fast": ("Быстрый темп", bool),
    }

    def settings_for(self, account: str) -> dict[str, object]:
        """Current values of everything the panel may change."""
        config = self.configs.get(account)
        if config is None:
            return {}
        start, end = config.active_hours or (None, None)
        return {
            "maze_rounds": config.maze_rounds,
            "min_keys": config.min_keys,
            "active_hours": f"{start:%H:%M}-{end:%H:%M}" if start else "",
            "spend_baksy": config.spend_baksy,
            "fast": config.delays.max_seconds <= 2,
        }

    def update_account(self, account: str, values: dict[str, str]) -> str:
        """Save changed settings for one account.

        Returns:
            An empty string on success, or why it was refused.
        """
        if account not in self.configs:
            return "нет такого профиля"
        if self.config_path is None:
            return "не задан файл настроек"

        changes: dict[str, object] = {}
        for key, (label, kind) in self.EDITABLE.items():
            if key not in values:
                continue
            raw = values[key].strip()
            if kind is bool:
                changes[key] = raw in ("on", "true", "1", "да")
            elif kind is int:
                if not raw.isdigit():
                    return f"«{label}» — нужно целое число"
                changes[key] = int(raw)
            else:
                changes[key] = raw

        return self._write_account(account, changes)

    def _write_account(self, account: str, changes: dict[str, object]) -> str:
        """Apply changes to one account in the configuration file."""
        path = pathlib.Path(self.config_path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            return f"не читается файл настроек: {exc}"

        entries = raw.get("accounts") or []
        entry = next((e for e in entries if e.get("username") == account), None)
        if entry is None:
            return "профиль не найден в файле"

        # "fast" is a shorthand the panel offers rather than a setting of its
        # own: it stands for the whole pacing envelope.
        if "fast" in changes:
            if changes.pop("fast"):
                entry.update(delay_min=0.6, delay_max=1.5, long_pause_chance=0.01)
            else:
                entry.update(delay_min=1.5, delay_max=3.5, long_pause_chance=0.04)
        entry.update(changes)

        try:
            path.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            updated = [c for c in config_module.load_all(path) if c.username == account]
        except (OSError, ConfigError, yaml.YAMLError) as exc:
            return f"не сохранилось: {exc}"

        if not updated:
            return "профиль не перечитался"
        self.configs[account] = updated[0]
        logger.info("Updated settings for %s from the panel", account)
        return ""

    def remove_account(self, account: str) -> str:
        """Remove an account from the configuration file.

        Returns:
            An empty string on success, or why it was refused.
        """
        if account not in self.configs:
            return "нет такого профиля"
        job = self.jobs.get(account)
        if job is not None and job.running:
            return "профиль сейчас работает, сначала остановите"
        if self.config_path is None:
            return "не задан файл настроек"

        path = pathlib.Path(self.config_path)
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            entries = raw.get("accounts") or []
            raw["accounts"] = [e for e in entries if e.get("username") != account]
            path.write_text(
                yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
        except (OSError, yaml.YAMLError) as exc:
            return f"не сохранилось: {exc}"

        self.configs.pop(account, None)
        self.jobs.pop(account, None)
        logger.info("Removed account %s from the panel", account)
        return ""

    def start(self, account: str, action: str, rounds: int | None = None) -> bool:
        """Begin an action for one account.

        Returns:
            True if a job was started; False if the account is unknown or
            already busy.
        """
        config = self.configs.get(account)
        if config is None or action not in ACTIONS:
            return False

        with self._lock:
            existing = self.jobs.get(account)
            if existing is not None and existing.running:
                return False
            job = Job(account=account, action=action)
            self.jobs[account] = job

        job.thread = threading.Thread(
            target=self._play, args=(config, job, rounds), name=account, daemon=True
        )
        job.thread.start()
        return True

    def stop(self, account: str) -> bool:
        """Ask one account's job to wind up after its current step."""
        job = self.jobs.get(account)
        if job is None or not job.running:
            return False
        job.stopping.set()
        logger.info("%s: asked to stop", account)
        return True

    def stop_all(self) -> int:
        """Ask every running job to wind up."""
        return sum(self.stop(name) for name in list(self.jobs))

    def _play(self, config: Config, job: Job, rounds: int | None) -> None:
        """Run one job to completion, whatever happens."""
        try:
            with self._permit:
                if job.stopping.is_set():
                    job.result = "отменено"
                    return
                # Two players never sign in on the same second.
                if self.stagger:
                    time.sleep(random.uniform(0, self.stagger))

                bot = NeboBot(config)
                try:
                    if not bot.start():
                        job.result = "вход не прошёл"
                        return
                    job.result = self._act(bot, job, rounds)
                finally:
                    bot.stop()
        except Exception as exc:  # noqa: BLE001 - a job must never take the app down
            logger.exception("%s: job failed", job.account)
            job.result = f"ошибка: {exc}"
        finally:
            job.finished = True

    def _act(self, bot: NeboBot, job: Job, rounds: int | None) -> str:
        """Carry out the requested action and describe the outcome."""
        if job.action == "check":
            return "вход есть"

        if job.action == "collect":
            return f"забрано наград: {bot.collect()}"

        wanted = rounds if rounds is not None else bot.config.maze_rounds
        done = bot.maze.solve(rounds=wanted, should_stop=job.stopping.is_set)

        # Finishing mazes ripens marathon rewards, and an unclaimed one blocks
        # the next tier — so take whatever came due before reporting.
        taken = bot.collect()
        return f"лабиринтов: {done}" + (f", наград: {taken}" if taken else "")
