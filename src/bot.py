"""Top-level orchestration of the bot's features."""

from __future__ import annotations

import logging
import random
from pathlib import Path

import requests

from . import config as config_module
from . import wicket
from .config import Config
from .modules.auth import Auth
from .modules.maze import MazeBot
from .modules.quests import QuestBot
from .utils.human_like import within_active_hours

logger = logging.getLogger(__name__)


class NeboBot:
    """Runs the enabled features against an authenticated session.

    Example:
        bot = NeboBot('config/config.yml')
        if bot.start():
            bot.run()
        bot.stop()
    """

    def __init__(self, config: str | Path | Config = "config/config.yml"):
        """Prepare the modules for one account.

        Args:
            config: An already-loaded Config, or the path of a YAML file to
                read one from. Passing a Config is what lets a single file
                drive one bot per account.

        Raises:
            ConfigError: If the configuration is missing or invalid. Raised
                rather than exiting, so callers decide how to handle it.
        """
        self.config: Config = config if isinstance(config, Config) else config_module.load(config)
        self.auth = Auth(self.config)
        self.maze = MazeBot(self.auth, self.config)
        self.quests = QuestBot(self.auth, self.config)
        logger.debug("Bot initialised for %s", self.config.base_url)

    def start(self) -> bool:
        """Authenticate.

        Returns:
            True if the session is ready for use.
        """
        logger.info("Starting bot")
        return self.auth.login()

    def run(self) -> bool:
        """Run the enabled features once.

        Returns:
            True if every feature that ran succeeded.
        """
        if not within_active_hours(self.config.active_hours):
            start, end = self.config.active_hours  # type: ignore[misc]
            logger.info(
                "Outside the configured active hours (%s-%s); nothing to do",
                start.strftime("%H:%M"),
                end.strftime("%H:%M"),
            )
            return True

        if not self.auth.is_authenticated():
            logger.error("Cannot run features without an authenticated session")
            return False

        # Same order every run is its own signature, so the errands that do
        # not depend on each other are shuffled.
        errands = [self._read_quests, self.auth.wanderer.maybe_wander]
        random.shuffle(errands)
        for errand in errands:
            errand()

        completed = self.maze.solve()
        wanted = self.config.maze_rounds
        if wanted:
            logger.info("Completed %d of %d maze(s)", completed, wanted)
            return completed >= wanted
        logger.info("Completed %d maze(s)", completed)
        return completed > 0

    def collect(self) -> int:
        """Take every reward waiting on the task and marathon pages.

        Keys arrive here without ever being named on a card, so the count is
        read before and after and the difference reported.

        Returns:
            How many rewards were taken.
        """
        before = self._keys()
        taken = 0
        for page in ("/quests", "/tasks"):
            try:
                taken += self.quests.claim_all(page)
            except requests.RequestException as exc:
                logger.warning("Could not collect from %s: %s", page, exc)

        after = self._keys()
        if taken and before is not None and after is not None:
            logger.info("Collected %d reward(s), keys %d -> %d (%+d)",
                        taken, before, after, after - before)
        return taken

    def _keys(self) -> int | None:
        """Read the current key count, forgiving a network hiccup."""
        try:
            response = self.auth.session.get(
                self.config.url("/doors"), timeout=self.config.timeout
            )
            return self.maze.keys_left(wicket.parse(response.text))
        except requests.RequestException:
            return None

    def _read_quests(self) -> None:
        """Report the task page, forgiving a network hiccup."""
        try:
            self.quests.report()
        except requests.RequestException as exc:
            logger.warning("Could not read the task page: %s", exc)

    def stop(self, logout: bool = True) -> None:
        """Release the session, logging out first unless asked not to.

        Safe to call even if :meth:`start` failed or was never called.

        Args:
            logout: Whether to end the session on the server. False when the
                session has been handed to a browser: logging out would drop
                the player out of the game mid-click.
        """
        logger.info("Stopping bot")

        if logout:
            if not self.auth.logout():
                logger.warning("Logout did not complete cleanly")
        else:
            logger.info("Leaving the session open; it was handed to a browser")

        self.auth.session.close()
