"""Riding the lift.

Visitors queue in the lobby, each wanting a particular floor, and the lift is
raised towards them one press at a time. A press climbs as many floors as the
lift's own level, which is what makes the upgrade matter: at L-5 a visitor
bound for floor 39 costs eight presses, at L-35 the same trip costs two. With
a hundred visitors waiting that is the difference between seven hundred
requests and sixty.

Tips accrue only from riding by hand, and only up to a daily ceiling that
grows with the player's level ("Чем выше уровень игрока, тем больше бесплатных
баксов в день"). The paid shortcut delivers the whole queue at once but pays
no tips at all, so the sensible order is to ride while tips still accrue and
only then consider paying.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from .. import wicket
from ..config import Config
from ..modules.auth import Auth

logger = logging.getLogger(__name__)

_UP_COMPONENT = "liftState-upLink"
_DELIVER_ALL_COMPONENT = "processLiftAll-link"
# City announcements sit on top of the lift page with their own hide link.
_HIDE_ANNOUNCEMENT_COMPONENT = "guildMessageBlock-hideLink"

_FLOOR = re.compile(r"\[Этаж:\s*(\d+)\s*\]")
_VISITORS = re.compile(r"Посетителей:\s*(\d+)")
_TIPS = re.compile(r"чаевых\s*(\d+)\s*из\s*(\d+)")
_CALLER = re.compile(r"([А-ЯЁ][а-яё]+)\s*:\s*(\d+)\s*этаж")


@dataclass(frozen=True)
class LiftState:
    """What the lift page reports.

    Attributes:
        floor: Where the lift currently stands.
        visitors: How many are still waiting.
        tips: Tips banked today.
        tips_cap: Today's ceiling, which rises with the player's level.
        wanted_floor: Floor the current visitor asked for, if anyone waits.
    """

    floor: int = 0
    visitors: int = 0
    tips: int = 0
    tips_cap: int = 0
    wanted_floor: int | None = None

    @property
    def tips_full(self) -> bool:
        """Whether the day's tips are already maxed out."""
        return self.tips_cap > 0 and self.tips >= self.tips_cap


class LiftBot:
    """Delivers waiting visitors."""

    def __init__(self, auth: Auth, config: Config):
        """Initialise with an authenticated session."""
        self.session = auth.session
        self.human = auth.human
        self.config = config

    def fetch(self) -> tuple[BeautifulSoup, str]:
        """Load the lift page, returning it with the URL it came from."""
        response = self.session.get(self.config.url("/lift"), timeout=self.config.timeout)
        response.raise_for_status()
        self.human.pause_page_load()
        return wicket.parse(response.text), response.url

    def state(self, soup: BeautifulSoup) -> LiftState:
        """Read the lift's current situation."""
        text = soup.get_text(" ", strip=True)
        floor = _FLOOR.search(text)
        visitors = _VISITORS.search(text)
        tips = _TIPS.search(text)
        caller = _CALLER.search(text)
        return LiftState(
            floor=int(floor.group(1)) if floor else 0,
            visitors=int(visitors.group(1)) if visitors else 0,
            tips=int(tips.group(1)) if tips else 0,
            tips_cap=int(tips.group(2)) if tips else 0,
            wanted_floor=int(caller.group(2)) if caller else None,
        )

    def up_url(self, soup: BeautifulSoup, page_url: str) -> str | None:
        """Return the link that raises the lift, if it is offered."""
        urls = wicket.find_links_containing(soup, _UP_COMPONENT, page_url)
        return urls[0] if urls else None

    def deliver_all_url(self, soup: BeautifulSoup, page_url: str) -> str | None:
        """Return the paid "deliver everyone" link, if it is offered."""
        urls = wicket.find_links_containing(soup, _DELIVER_ALL_COMPONENT, page_url)
        return urls[0] if urls else None

    def hide_announcement(self, soup: BeautifulSoup, page_url: str) -> bool:
        """Dismiss the city announcement banner if one is showing.

        Returns:
            True if a banner was dismissed.
        """
        urls = wicket.find_links_containing(soup, _HIDE_ANNOUNCEMENT_COMPONENT, page_url)
        if not urls:
            return False

        self.human.pause()
        response = self.session.get(urls[0], timeout=self.config.timeout)
        response.raise_for_status()
        logger.info("Hid the city announcement")
        return True

    def ride(self, max_presses: int, stop_when_tips_full: bool = True) -> int:
        """Raise the lift repeatedly, delivering whoever is waiting.

        Args:
            max_presses: Hard ceiling on presses, since a low-level lift needs
                many of them per visitor.
            stop_when_tips_full: Stop once the day's tips are capped, as
                further riding earns nothing.

        Returns:
            How many times the lift was raised.
        """
        presses = 0

        while presses < max_presses:
            soup, page_url = self.fetch()

            if self.config.hide_city_announcements and self.hide_announcement(soup, page_url):
                soup, page_url = self.fetch()

            state = self.state(soup)

            if state.visitors == 0:
                logger.info("Lift: no one waiting")
                break

            if stop_when_tips_full and state.tips_full:
                logger.info("Lift: tips capped at %d, riding earns nothing more", state.tips_cap)
                break

            url = self.up_url(soup, page_url)
            if url is None:
                logger.info("Lift: nothing to press")
                break

            self.human.pause()
            response = self.session.get(url, timeout=self.config.timeout)
            response.raise_for_status()
            presses += 1

        if presses:
            soup, _ = self.fetch()
            after = self.state(soup)
            logger.info(
                "Lift: %d press(es), %d visitor(s) left, tips %d/%d",
                presses, after.visitors, after.tips, after.tips_cap,
            )
        return presses

    def deliver_all(self) -> bool:
        """Clear the whole queue with the paid shortcut.

        Costs a bak and forfeits the tips the ride would have paid, so callers
        should ride first while tips still accrue.

        Returns:
            True if the shortcut was taken.
        """
        soup, page_url = self.fetch()
        url = self.deliver_all_url(soup, page_url)
        if url is None:
            logger.info("Lift: no paid delivery offered")
            return False

        self.human.pause()
        response = self.session.get(url, timeout=self.config.timeout)
        response.raise_for_status()
        logger.info("Lift: delivered everyone for a bak")
        return True
