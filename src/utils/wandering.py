"""Idle browsing between useful actions.

A bot that only ever opens the pages it needs leaves a very particular trace:
the same handful of URLs, in the same order, with nothing in between. A player
wanders — glances at the chat, checks the rating, opens their own profile for
no reason, and sometimes walks into a page and straight back out again.

None of this costs keys or baksy. Every page here is free to open, which is
what makes it safe to do at random: the worst case is a wasted request.
"""

from __future__ import annotations

import logging
import random

import requests

from ..config import Config
from .human_like import HumanBehavior

logger = logging.getLogger(__name__)

# Free to open, and all of them are places a player has reason to look.
IDLE_PAGES = (
    "/home",
    "/chat",
    "/rating",
    "/online",
    "/forum/list",
    "/forum/1",
    "/humans",
    "/vendor",
    "/mail",
    "/friends",
)

# Pages a player might open and immediately back out of.
_GLANCE_PAGES = ("/about", "/support", "/vendor/humans", "/rating")


class Wanderer:
    """Occasionally looks at something other than the task at hand."""

    def __init__(self, session: requests.Session, config: Config, human: HumanBehavior):
        """Initialise with the session to browse on.

        Args:
            session: Authenticated session, shared with the rest of the bot.
            config: Validated configuration; ``wander_chance`` drives how often
                this happens at all.
            human: Pacing, so wandering is spaced like everything else.
        """
        self.session = session
        self.config = config
        self.human = human

    def maybe_wander(self) -> int:
        """Look at an unrelated page or two, now and then.

        Returns:
            How many pages were opened, usually zero.
        """
        if not self.config.wander_chance:
            return 0
        if random.random() >= self.config.wander_chance:
            return 0

        # One page most of the time, occasionally a short browse.
        pages = random.choice([1, 1, 1, 2, 3])
        opened = 0
        for path in random.sample(IDLE_PAGES, min(pages, len(IDLE_PAGES))):
            if self._open(path):
                opened += 1

        if opened:
            logger.debug("Wandered through %d page(s)", opened)
        return opened

    def maybe_wrong_turn(self) -> bool:
        """Open a page and go straight back, as a misclick would.

        Nothing here spends a resource: the mistake costs a page load and
        nothing else.

        Returns:
            True if a wrong turn was taken.
        """
        if not self.config.wander_chance:
            return False
        # Much rarer than ordinary wandering: a misclick is an event, not a
        # habit, and at a third of the wander rate it still read as one.
        if random.random() >= self.config.wander_chance / 10:
            return False

        if not self._open(random.choice(_GLANCE_PAGES)):
            return False

        self._open("/home")
        logger.debug("Took a wrong turn and came back")
        return True

    def _open(self, path: str) -> bool:
        """Fetch one page, forgiving failures."""
        try:
            response = self.session.get(self.config.url(path), timeout=self.config.timeout)
            response.raise_for_status()
            return True
        except requests.RequestException as exc:
            # Idle browsing must never be able to break a run.
            logger.debug("Could not open %s while wandering: %s", path, exc)
            return False
