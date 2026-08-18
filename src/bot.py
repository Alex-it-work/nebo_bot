"""Top-level orchestration of the bot's features."""

from __future__ import annotations

import logging
from pathlib import Path

from . import config as config_module
from .config import Config
from .modules.auth import Auth
from .modules.maze import MazeBot
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

    def __init__(self, config_path: str | Path = "config/config.yml"):
        """Load the configuration and prepare the modules.

        Args:
            config_path: Path to the YAML configuration file.

        Raises:
            ConfigError: If the configuration is missing or invalid. Raised
                rather than exiting, so callers decide how to handle it.
        """
        self.config: Config = config_module.load(config_path)
        self.auth = Auth(self.config)
        self.maze = MazeBot(self.auth, self.config)
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

        return self.maze.solve()

    def stop(self) -> None:
        """Log out and release the session.

        Safe to call even if :meth:`start` failed or was never called.
        """
        logger.info("Stopping bot")

        if not self.auth.logout():
            logger.warning("Logout did not complete cleanly")

        self.auth.session.close()
