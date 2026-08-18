"""Automated maze runs.

The page renders the progress counter as ``Комната: <b class="amount">1</b>``
and offers one anchor per door, each carrying a single-use ``action`` nonce:

    ./doors?3-1.-doorLink1&action=1787078108652

Two details matter. A decoy ``<span class="amount">`` sits next to the counter,
so the lookup is restricted to ``<b>``. And every door costs exactly one key,
so the remaining count is checked before opening anything.

All selectors here have been confirmed against live runs, dead-end banner
included.

Doors are chosen from accumulated experience rather than at random: uniform
random cannot realistically clear ten rooms (about one run in 19,700), while
probing showed the layout is largely stable. See :mod:`src.memory`.
"""

from __future__ import annotations

import logging
import re

import requests
from bs4 import BeautifulSoup

from .. import wicket
from ..config import Config
from ..memory import DoorMemory
from ..modules.auth import Auth
from ..utils.human_like import SessionBudget

logger = logging.getLogger(__name__)

# Wicket component name embedded in door links. The surrounding URL format
# changes between framework versions, but the component name does not.
_DOOR_COMPONENT = "doorLink"
_DOOR_NUMBER = re.compile(rf"{_DOOR_COMPONENT}(\d+)")

# Pauses after a dead end are stretched, as a person would hesitate on a reset.
_SETBACK_MULTIPLIER = 1.5

# Steps allowed per attempt, as a multiple of the target room count.
_STEP_BUDGET_FACTOR = 3

# "Осталось ключей: 1707" — the number may carry thousand separators.
_KEYS_PATTERN = re.compile(r"Осталось\s+ключей:\s*(\d[\d'’ ]*)")


class OutOfKeys(Exception):
    """Raised when no keys remain, so retrying cannot help."""


class MazeBot:
    """Walks the maze until the target depth is reached."""

    def __init__(self, auth: Auth, config: Config, memory: DoorMemory | None = None):
        """Initialise with an authenticated session.

        Args:
            auth: Auth instance owning the logged-in session.
            config: Validated bot configuration.
            memory: Door knowledge to consult and extend. Loaded from the
                configured file when omitted.
        """
        self.session = auth.session
        self.human = auth.human
        self.config = config
        self.memory = memory if memory is not None else DoorMemory(config.maze_memory_file)

    def keys_left(self, soup: BeautifulSoup) -> int | None:
        """Read how many keys remain.

        Each door consumes a key, so running out ends the session rather than
        the current attempt.

        Returns:
            The remaining count, or None when the page does not report it.
        """
        match = _KEYS_PATTERN.search(soup.get_text(" ", strip=True))
        if match is None:
            return None
        return int(re.sub(r"\D", "", match.group(1)))

    def current_level(self, soup: BeautifulSoup) -> int:
        """Read the current room number from the page.

        Restricted to ``<b>`` because a decoy ``<span class="amount">`` holding
        promotional text sits directly above the counter.

        Returns:
            The room number, or 0 when the counter is absent or unreadable.
        """
        amount = soup.find("b", class_="amount")
        if amount is None:
            return 0
        try:
            return int(amount.get_text(strip=True))
        except ValueError:
            logger.debug("Could not read the level counter: %r", amount.get_text(strip=True))
            return 0

    def is_dead_end(self, soup: BeautifulSoup) -> bool:
        """Check whether the run ended in a dead end."""
        message = wicket.find_notification(soup)
        return message is not None and "тупик" in message.lower()

    def door_urls(self, soup: BeautifulSoup, page_url: str) -> list[str]:
        """Return absolute URLs for every door link on the page."""
        urls = wicket.find_links_containing(soup, _DOOR_COMPONENT, page_url)
        logger.debug("Found %d door links: %s", len(urls), urls)
        return urls

    def doors_by_number(self, soup: BeautifulSoup, page_url: str) -> dict[int, str]:
        """Map each door's number to its URL.

        Doors are named ``doorLink1``, ``doorLink2``, ... and those numbers stay
        put between requests, which is what makes remembering them worthwhile.
        """
        numbered: dict[int, str] = {}
        for url in self.door_urls(soup, page_url):
            match = _DOOR_NUMBER.search(url)
            if match:
                numbered[int(match.group(1))] = url
        return numbered

    def solve(self) -> bool:
        """Run the maze until the target level is reached.

        Restarts from the entrance on every dead end, up to the configured
        attempt limit.

        Returns:
            True if the target level was reached.
        """
        target = self.config.maze_target_level
        max_attempts = self.config.maze_max_attempts
        attempt = 0

        logger.info("Solving the maze, target level %d", target)

        budget = SessionBudget(self.config.session_max_minutes)

        try:
            while max_attempts == 0 or attempt < max_attempts:
                if budget.expired():
                    logger.info(
                        "Session limit of %d min reached after %d attempts; stopping",
                        budget.max_minutes,
                        attempt,
                    )
                    return False

                attempt += 1
                logger.info("Maze attempt #%d", attempt)

                try:
                    if self._walk(target):
                        logger.info("Maze solved on attempt #%d", attempt)
                        return True
                except OutOfKeys as exc:
                    # Retrying cannot produce keys, so stop rather than spin.
                    logger.warning("Stopping: %s", exc)
                    return False
                except requests.RequestException as exc:
                    logger.error("Maze attempt #%d failed: %s", attempt, exc)

                self.human.pause(_SETBACK_MULTIPLIER)

            logger.warning("Gave up on the maze after %d attempts", attempt)
            return False
        finally:
            # Keep what was learned even if the run is interrupted.
            self.memory.save()

    def _walk(self, target: int) -> bool:
        """Walk one run from the entrance.

        Returns:
            True if the target level was reached during this run.
        """
        response = self._get(self.config.url("/doors"))

        # Every step should either advance a room or end in a dead end. If
        # neither happens the page is not behaving as expected, and looping on
        # would keep spending keys for nothing.
        budget = target * _STEP_BUDGET_FACTOR

        # The door opened on the previous step, whose outcome this step reveals.
        pending: tuple[int, int] | None = None

        for _ in range(budget):
            soup = wicket.parse(response.text)

            if self.is_dead_end(soup):
                if pending:
                    self.memory.record(*pending, success=False)
                    logger.info("Dead end behind room %d door %d, restarting", *pending)
                else:
                    logger.info("Dead end, restarting")
                return False

            level = self.current_level(soup)
            if pending:
                self.memory.record(*pending, success=True)
                pending = None

            keys = self.keys_left(soup)
            logger.info(
                "Room %d/%d%s", level, target, f", keys left: {keys}" if keys is not None else ""
            )

            if level >= target:
                return True

            if keys == 0:
                raise OutOfKeys("No keys left to open another door")

            doors = self.doors_by_number(soup, response.url)
            if not doors:
                logger.warning("No door links on %s; the maze markup may have changed", response.url)
                return False

            choice = self.memory.choose(level, sorted(doors))
            logger.debug(
                "Room %d: chose door %d (history %s)",
                level,
                choice,
                self.memory.summary(level, sorted(doors)),
            )

            # "Think" before committing to a door.
            self.human.pause()
            pending = (level, choice)
            response = self._get(doors[choice])

        logger.warning("Walk exceeded %d steps without finishing; abandoning the attempt", budget)
        return False

    def _get(self, url: str) -> requests.Response:
        """Fetch a page, raise on HTTP errors, then pause as a reader would."""
        response = self.session.get(url, timeout=self.config.timeout)
        response.raise_for_status()
        self.human.pause_page_load()
        return response
