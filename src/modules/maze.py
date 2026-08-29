"""Automated maze runs.

The page renders the progress counter as ``Комната: <b class="amount">1</b>``
and offers one anchor per door, each carrying a single-use ``action`` nonce:

    ./doors?3-1.-doorLink1&action=1787078108652

Two details matter. A decoy ``<span class="amount">`` sits next to the counter,
so the lookup is restricted to ``<b>``. And every door costs exactly one key,
so the remaining count is checked before opening anything.

All selectors here have been confirmed against live runs, dead-end banner
included.

Doors are chosen at random, and measurement says nothing better is available.
Over 58 attempts across two accounts every door in rooms 2 to 9 turned up as
both a wall and a passage, so the layout is reshuffled on each attempt and no
door can be learned. The first room always opens (57 of 57) and so does the
last, while the rooms between pass about 65% of the time regardless of depth.
Nine consecutive successes are needed, so roughly one run in 32 reaches the
prize, at some 120 keys apiece. Estimates from two or three prizes swing
wildly and should not be trusted.

The page does reveal each room's layout once it is behind you, but by then it
has already been reshuffled, so that knowledge cannot inform a choice either.
"""

from __future__ import annotations

import logging
import random
import re

import requests
from bs4 import BeautifulSoup

from .. import wicket
from ..config import Config
from ..modules.auth import Auth
from ..utils.human_like import SessionBudget

logger = logging.getLogger(__name__)

# Wicket component name embedded in door links. The surrounding URL format
# changes between framework versions, but the component name does not.
_DOOR_COMPONENT = "doorLink"
_DOOR_NUMBER = re.compile(rf"{_DOOR_COMPONENT}(\d+)")

# Pauses after a dead end are stretched, as a person would hesitate on a reset.
_SETBACK_MULTIPLIER = 1.5

# A prize screen is worth looking at. Rushing past it in milliseconds is both
# unlike a player and hides the one moment worth watching in the live view.
_PRIZE_MULTIPLIER = 5

# Steps allowed per attempt, as a multiple of the target room count.
_STEP_BUDGET_FACTOR = 3

# The victory screen drops the room counter entirely, so a win is recognised by
# its text: "Поздравляем! Вы прошли лабиринт!".
_VICTORY_PATTERN = re.compile(r"прошли\s+лабиринт", re.I)

# A dead end offers "Пройти за 200" — a guaranteed finish, paid in keys. The
# price is read from the page rather than assumed.
_BUY_FINISH_COMPONENT = "buyFinishLink"
_BUY_PRICE = re.compile(r"Пройти\s+за\s*(\d[\d'’ ]*)")

# "Осталось ключей: 1707" — the number may carry thousand separators.
_KEYS_PATTERN = re.compile(r"Осталось\s+ключей:\s*(\d[\d'’ ]*)")


class OutOfKeys(Exception):
    """Raised when the keys are down to the floor, so playing on would eat it."""


class MazeBot:
    """Walks the maze until the target depth is reached."""

    def __init__(self, auth: Auth, config: Config):
        """Initialise with an authenticated session.

        Args:
            auth: Auth instance owning the logged-in session.
            config: Validated bot configuration.
        """
        self.session = auth.session
        self.human = auth.human
        self.wanderer = auth.wanderer
        self.config = config

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

    def buy_finish_offer(self, soup: BeautifulSoup, page_url: str) -> tuple[str, int] | None:
        """Return the paid finish as ``(url, price_in_keys)``, if offered.

        The game puts this on the dead-end screen: rather than starting over,
        the run can be completed outright for a fixed number of keys.

        Returns:
            The link and its price, or None when not on offer.
        """
        urls = wicket.find_links_containing(soup, _BUY_FINISH_COMPONENT, page_url)
        if not urls:
            return None
        price = _BUY_PRICE.search(soup.get_text(" ", strip=True))
        if price is None:
            return None
        return urls[0], int(re.sub(r"\D", "", price.group(1)))

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

        Doors are named ``doorLink1``, ``doorLink2``, ... and the numbers are
        what the dead-end log reports, which is how the reshuffling was found.
        """
        numbered: dict[int, str] = {}
        for url in self.door_urls(soup, page_url):
            match = _DOOR_NUMBER.search(url)
            if match:
                numbered[int(match.group(1))] = url
        return numbered

    def solve(self, rounds=None, should_stop=None, on_complete=None, on_attempt=None) -> int:
        """Complete whole mazes, prize included.

        A dead end restarts from the entrance; so does a win, since "Начать
        сначала" simply links back to the maze entrance.

        Args:
            rounds: How many mazes to complete, or 0 for as many as the keys,
                attempt limit and session budget allow. Defaults to the
                configured value. May be a callable, which is asked again
                before every attempt, so the target can be raised or lowered
                while the run is going rather than only for the next one.
            should_stop: Called between attempts; when it returns True the run
                winds up. Checked between attempts rather than mid-maze, since
                abandoning a half-walked maze wastes the keys already spent.
            on_complete: Called after each finished maze. Clearing a marathon
                tier ripens a reward, and until it is taken the next tier does
                not appear — so further mazes would count towards nothing.
            on_attempt: Called with (attempt, completed, target) as each
                attempt begins, so progress can be shown while it runs
                instead of only in the log.

        Returns:
            The number of mazes completed.
        """
        target = self.config.maze_target_level
        if rounds is None:
            rounds = self.config.maze_rounds
        # A callable target is re-read every attempt; a plain number is fixed.
        asked = rounds if callable(rounds) else (lambda: rounds)
        max_attempts = self.config.maze_max_attempts
        budget = SessionBudget(self.config.session_max_minutes)

        logger.info(
            "Solving mazes: %s to complete, %d rooms each",
            str(asked()) if asked() else "unlimited",
            target,
        )

        completed = 0
        attempt = 0

        while True:
            goal = asked()
            wanted = str(goal) if goal else "unlimited"
            if goal and completed >= goal:
                break

            if should_stop is not None and should_stop():
                logger.info("Asked to stop; finishing after %d maze(s)", completed)
                break

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
            if on_attempt is not None:
                on_attempt(attempt, completed, goal)

            try:
                if self._walk(target):
                    completed += 1
                    logger.info("Maze %d/%s complete on attempt #%d", completed, wanted, attempt)
                    if on_complete is not None:
                        on_complete()
            except OutOfKeys as exc:
                # Retrying cannot produce keys, so stop rather than spin.
                logger.warning("Stopping: %s", exc)
                break
            except requests.RequestException as exc:
                logger.error("Attempt #%d failed: %s", attempt, exc)

            self.human.pause(_SETBACK_MULTIPLIER)
            if self.wanderer is not None:
                # Between attempts only: a glance elsewhere mid-maze would be
                # odd, and this is where a player would drift off anyway.
                self.wanderer.maybe_wander()
                self.wanderer.maybe_wrong_turn()

        return completed

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

            if self.is_solved(soup):
                reward = self.reward(soup)
                logger.info(
                    "Maze complete%s", f", reward: {' + '.join(reward)}" if reward else ""
                )
                self.human.pause(_PRIZE_MULTIPLIER)
                return True

            if self.is_dead_end(soup):
                if pending:
                    logger.info("Dead end behind room %d door %d", *pending)
                else:
                    logger.info("Dead end")

                if self.config.buy_finish:
                    offer = self.buy_finish_offer(soup, response.url)
                    keys = self.keys_left(soup)
                    if offer and (keys is None or keys >= offer[1]):
                        url, price = offer
                        logger.info("Buying the finish for %d keys", price)
                        bought = self._get(url)
                        if self.is_solved(wicket.parse(bought.text)):
                            return True
                        logger.warning("Paid finish did not complete the maze")
                return False

            level = self.current_level(soup)
            pending = None

            if level == 0:
                logger.warning("No room counter on %s; the maze markup may have changed", response.url)
                return False

            keys = self.keys_left(soup)
            # The online count rides along so "the maze is easier when it is
            # quiet" can be checked against data later.
            online = wicket.find_online_count(soup)
            logger.info(
                "Room %d/%d%s%s",
                level,
                target,
                f", keys left: {keys}" if keys is not None else "",
                f", online: {online}" if online is not None else "",
            )

            floor = self.config.min_keys
            if keys is not None and keys <= floor:
                raise OutOfKeys(
                    f"Down to {keys} keys, at or below the floor of {floor}"
                    if floor
                    else "No keys left to open another door"
                )

            doors = self.doors_by_number(soup, response.url)
            if not doors:
                logger.warning("No door links on %s; the maze markup may have changed", response.url)
                return False

            choice = random.choice(sorted(doors))

            pending = (level, choice)
            response = self._get(doors[choice])

        logger.warning("Walk exceeded %d steps without finishing; abandoning the attempt", budget)
        return False

    def _get(self, url: str) -> requests.Response:
        """Fetch a page and raise on HTTP errors.

        The pauses either side are the session's; see
        :class:`~src.utils.human_like.HumanSession`.
        """
        response = self.session.get(url, timeout=self.config.timeout)
        response.raise_for_status()
        return response
