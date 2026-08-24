"""Managing the hotel's residents.

Residents carry a skill and, next to their dream job, a marker: ``(+)`` means
the game rates them an upgrade over whoever currently holds a matching floor,
``(-)`` the opposite. The marker does not follow the skill — a six can be
marked ``(+)`` while a nine is marked ``(-)`` — so eviction goes by the marker,
never by the number alone. Anyone marked ``(+)`` is kept.

There are two ways to evict. The page offers a bulk button that clears
everyone below skill nine for a bak, and each resident's own page offers a free
``evictLink``. The bulk button is faster but costs, and it also takes any
``(+)`` resident under nine with it, so it is only used when spending is
allowed and nothing marked ``(+)`` would be lost.
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

_BULK_EVICT_COMPONENT = "clearLinkPanel-clearLink-link"
_EVICT_COMPONENT = "evictLink"

# The skill the game's own bulk filter keeps: "с навыком ниже 9".
_BULK_FILTER_KEEPS = 9


@dataclass(frozen=True)
class Resident:
    """One resident of the hotel.

    Attributes:
        name: Display name.
        skill: Skill level.
        marker: "+", "-" or "" as shown beside the dream job.
        page_url: Absolute URL of the resident's own page.
    """

    name: str
    skill: int
    marker: str
    page_url: str

    @property
    def upgrades_a_floor(self) -> bool:
        """Whether the game marks this resident as an improvement."""
        return self.marker == "+"


class HumansBot:
    """Reads and evicts residents."""

    def __init__(self, auth: Auth, config: Config):
        """Initialise with an authenticated session."""
        self.session = auth.session
        self.human = auth.human
        self.config = config

    def fetch(self, page: int = 1) -> tuple[BeautifulSoup, str]:
        """Load one page of the resident list."""
        url = self.config.url("/humans") + (f"?page={page}" if page > 1 else "")
        response = self.session.get(url, timeout=self.config.timeout)
        response.raise_for_status()
        self.human.pause_page_load()
        return wicket.parse(response.text), response.url

    def residents(self, soup: BeautifulSoup, page_url: str) -> list[Resident]:
        """Read every resident listed on the page."""
        found: list[Resident] = []

        for item in soup.select("ul.rsd li"):
            skill_tag = item.find("b", class_="abstr")
            link = item.find("a", href=True)
            if skill_tag is None or link is None:
                continue

            digits = re.search(r"(\d+)", skill_tag.get_text(" ", strip=True))
            if digits is None:
                continue

            text = item.get_text(" ", strip=True)
            marker = "+" if "(+)" in text else ("-" if "(-)" in text else "")
            name = link.find_next("a", class_="white")
            found.append(
                Resident(
                    name=name.get_text(" ", strip=True) if name else "?",
                    skill=int(digits.group(1)),
                    marker=marker,
                    page_url=wicket.resolve(page_url, link["href"]),
                )
            )

        return found

    def evictable(self, residents: list[Resident]) -> list[Resident]:
        """Return those safe to evict: everyone the game has not marked ``(+)``."""
        return [resident for resident in residents if not resident.upgrades_a_floor]

    def evict(self, resident: Resident) -> bool:
        """Evict one resident from their own page, which costs nothing.

        Returns:
            True if the eviction link was found and followed.
        """
        response = self.session.get(resident.page_url, timeout=self.config.timeout)
        response.raise_for_status()
        self.human.pause_page_load()

        urls = wicket.find_links_containing(
            wicket.parse(response.text), _EVICT_COMPONENT, response.url
        )
        if not urls:
            logger.debug("No eviction link for %s", resident.name)
            return False

        self.human.pause()
        self.session.get(urls[0], timeout=self.config.timeout).raise_for_status()
        logger.info("Evicted %s (skill %d)", resident.name, resident.skill)
        return True

    def evict_many(self, limit: int) -> int:
        """Evict residents one by one until the limit is reached.

        Works from the last page backwards would be tidier, but the list
        renumbers as residents leave, so the first page is re-read each time.

        Args:
            limit: How many to evict.

        Returns:
            How many were actually evicted.
        """
        evicted = 0

        while evicted < limit:
            soup, page_url = self.fetch()
            candidates = self.evictable(self.residents(soup, page_url))
            if not candidates:
                logger.info("No one left to evict; everyone remaining is marked (+)")
                break
            if not self.evict(candidates[0]):
                break
            evicted += 1

        logger.info("Evicted %d resident(s)", evicted)
        return evicted

    def bulk_evict_url(self, soup: BeautifulSoup, page_url: str) -> str | None:
        """Return the paid "evict everyone below nine" link, if offered."""
        urls = wicket.find_links_containing(soup, _BULK_EVICT_COMPONENT, page_url)
        return urls[0] if urls else None

    def bulk_evict(self) -> bool:
        """Clear everyone below skill nine for a bak.

        Refuses when a ``(+)`` resident sits below the filter's threshold,
        since the button would evict them along with the rest.

        Returns:
            True if the bulk eviction was performed.
        """
        if not self.config.spend_baksy:
            logger.info("Bulk eviction costs a bak and spending is off")
            return False

        soup, page_url = self.fetch()
        residents = self.residents(soup, page_url)

        would_lose = [
            resident
            for resident in residents
            if resident.upgrades_a_floor and resident.skill < _BULK_FILTER_KEEPS
        ]
        if would_lose:
            logger.info(
                "Not bulk evicting: %s marked (+) below skill %d would be lost",
                ", ".join(resident.name for resident in would_lose),
                _BULK_FILTER_KEEPS,
            )
            return False

        url = self.bulk_evict_url(soup, page_url)
        if url is None:
            logger.info("No bulk eviction offered")
            return False

        self.human.pause()
        self.session.get(url, timeout=self.config.timeout).raise_for_status()
        logger.info("Bulk evicted everyone below skill %d", _BULK_FILTER_KEEPS)
        return True
