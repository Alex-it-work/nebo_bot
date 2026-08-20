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

# The victory screen drops the room counter entirely, so a win is recognised by
# its text: "Поздравляем! Вы прошли лабиринт!".
_VICTORY_PATTERN = re.compile(r"прошли\s+лабиринт", re.I)

# Images used on the revealed layout of a room already left behind.
_WALL_IMAGE = "door_wall"
_PASSAGE_IMAGE = "door_go"

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

    def is_solved(self, soup: BeautifulSoup) -> bool:
        """Check whether this page is the victory screen.

        Reaching the final room is not the same as winning: one more door has
        to be opened there, and only then does the prize appear.
        """
        return _VICTORY_PATTERN.search(soup.get_text(" ", strip=True)) is not None

    def reward(self, soup: BeautifulSoup) -> list[str]:
        """Return the reward amounts shown on the victory screen."""
        label = soup.find("span", class_="white")
        if label is None:
            return []
        return [
            amount.get_text(strip=True)
            for amount in label.find_all_next("span", class_="amount")
            if amount.get_text(strip=True)
        ][:2]

    def revealed_layouts(self, soup: BeautifulSoup) -> dict[int, dict[int, bool]]:
        """Read the layouts the page reveals for rooms already left behind.

        After moving on, the game shows what was behind each door of the
        previous room: ``door_wall`` for a dead end, ``door_go`` for a passage.
        That is free, exact information about doors never opened, and it shows
        rooms can hold more than one passage.

        Returns:
            Room number mapped to door number mapped to "is a passage".
        """
        layouts: dict[int, dict[int, bool]] = {}

        for block in soup.find_all("div", class_="m5"):
            # The current room uses <b class="amount">; revealed ones a <span>.
            if "Комната:" not in block.get_text():
                continue
            number = block.find("span")
            if number is None or block.find("b") is not None:
                continue
            try:
                room = int(number.get_text(strip=True))
            except ValueError:
                continue

            images = block.find_next_sibling("div")
            if images is None:
                continue

            doors: dict[int, bool] = {}
            for position, image in enumerate(images.find_all("img"), start=1):
                source = image.get("src", "")
                if _WALL_IMAGE in source:
                    doors[position] = False
                elif _PASSAGE_IMAGE in source:
                    doors[position] = True
            if doors:
                layouts[room] = doors

        return layouts

    def learn_from(self, soup: BeautifulSoup) -> None:
        """Record every door layout the page happens to reveal."""
        for room, doors in self.revealed_layouts(soup).items():
            for door, is_passage in doors.items():
                self.memory.record(room, door, success=is_passage)
            logger.debug(
                "Room %d revealed: %s",
                room,
                ", ".join(f"{d}={'проход' if ok else 'стена'}" for d, ok in sorted(doors.items())),
            )

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

    def solve(self, rounds: int | None = None) -> int:
        """Complete whole mazes, prize included.

        A dead end restarts from the entrance; so does a win, since "Начать
        сначала" simply links back to the maze entrance.

        Args:
            rounds: How many mazes to complete, or 0 for as many as the keys,
                attempt limit and session budget allow. Defaults to the
                configured value.

        Returns:
            The number of mazes completed.
        """
        target = self.config.maze_target_level
        rounds = self.config.maze_rounds if rounds is None else rounds
        max_attempts = self.config.maze_max_attempts
        budget = SessionBudget(self.config.session_max_minutes)
        wanted = str(rounds) if rounds else "unlimited"

        logger.info("Solving mazes: %s to complete, %d rooms each", wanted, target)

        completed = 0
        attempt = 0

        try:
            while rounds == 0 or completed < rounds:
                if budget.expired():
                    logger.info(
                        "Session limit of %d min reached; stopping after %d maze(s)",
                        budget.max_minutes,
                        completed,
                    )
                    break

                if max_attempts and attempt >= max_attempts:
                    logger.warning("Gave up after %d attempts with %d maze(s) done", attempt, completed)
                    break

                attempt += 1
                logger.info("Attempt #%d (%d/%s done)", attempt, completed, wanted)

                try:
                    if self._walk(target):
                        completed += 1
                        logger.info("Maze %d/%s complete on attempt #%d", completed, wanted, attempt)
                except OutOfKeys as exc:
                    # Retrying cannot produce keys, so stop rather than spin.
                    logger.warning("Stopping: %s", exc)
                    break
                except requests.RequestException as exc:
                    logger.error("Attempt #%d failed: %s", attempt, exc)

                self.human.pause(_SETBACK_MULTIPLIER)

            return completed
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

            # The page reveals the layout of rooms already passed, whichever
            # screen it is, so read that before anything else.
            self.learn_from(soup)

            if self.is_solved(soup):
                reward = self.reward(soup)
                logger.info(
                    "Maze complete%s", f", reward: {' + '.join(reward)}" if reward else ""
                )
                return True

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

            if level == 0:
                logger.warning("No room counter on %s; the maze markup may have changed", response.url)
                return False

            keys = self.keys_left(soup)
            logger.info(
                "Room %d/%d%s", level, target, f", keys left: {keys}" if keys is not None else ""
            )

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
