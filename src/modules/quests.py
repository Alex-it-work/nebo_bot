"""Reading the personal task page.

Personal tasks are where keys come from, and the maze only spends them, so
this page is upstream of everything else the bot does. See docs/game.md.

The page distinguishes two states by markup rather than by any label:

    <div class="nfl">                          a task being worked on
      <div><b>Инкассатор</b></div>
      <div class="white">Собери выручку со 150 товаров</div>
      <div class="minor small nshd">Прогресс: <span>149</span> из <span>150</span></div>

    <div class="nfl">                          a task on cooldown
      <div><strong class="minor nshd">Индиана Джонс</strong></div>
      <div class="minor nshd">Пройди лабиринт 1 раз</div>
      <div class="m5 cntr minor small nshd">До старта: <span>15 ч 33 мин</span></div>

The cooldown is 20 hours from when a reward was taken, not midnight, so the
page's own countdown is the only reliable schedule: whatever it says is when
there is a point in coming back.
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

# Wicket component behind "Получить награду!". The two pages spell it
# differently — /quests uses "getAwarLink", /tasks "getAwardLink" — so the
# match is on the common prefix. Matching the component name also survives the
# page-version number that changes on every render.
_AWARD_COMPONENT = "getAwar"

_PROGRESS = re.compile(r"Прогресс:\s*([\d'’ ]+)\s*из\s*([\d'’ ]+)")
_COOLDOWN = re.compile(r"До\s+старта:\s*(?:(\d+)\s*ч)?\s*(?:(\d+)\s*мин)?")
_DONE_TODAY = re.compile(r"Сегодня\s+выполнено\s+заданий:\s*(\d+)\s*из\s*(\d+)")
# The game states the key bonus outright rather than leaving it to be inferred:
# "Cегодня за задания Вы получаете в два раза больше ключей!" and
# "Завтра за все задания будет в два раза больше ключей!".
_DOUBLE_TODAY = re.compile(r"[СC]егодня[^.!]*два\s+раза\s+больше\s+ключей")
_DOUBLE_TOMORROW = re.compile(r"Завтра[^.!]*два\s+раза\s+больше\s+ключей")

_KEYS_EARNED = re.compile(r"Собрано\s+ключей:\s*([\d'’ ]+)")

# Tasks asking for real money. The bot must never act on these.
_PAID = re.compile(r"Пополни\s+счет", re.I)

# How much longer to dwell on a reward screen than on an ordinary page.
_REWARD_MULTIPLIER = 5

# And how long to look at the page before reaching for the reward at all.
_BEFORE_CLAIM_MULTIPLIER = 3


def _number(text: str) -> int:
    """Read a count that may carry thousand separators."""
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


# Reward icons name the currency; the game gives no text label. The same coin
# is drawn as st_sold on a task card and mn_iron in the header.
_CURRENCY = {
    "key": "ключи",
    "mn_gold": "баксы",
    "st_sold": "монеты",
    "mn_iron": "монеты",
    "star": "опыт",
}


@dataclass(frozen=True)
class Quest:
    """One personal task.

    Attributes:
        name: Task title, e.g. "Инкассатор".
        description: What it asks for.
        done: Progress so far, 0 while on cooldown.
        total: Target, 0 while on cooldown.
        minutes_left: Minutes until it becomes available, or None if active.
        paid: Whether it requires topping up with real money.
        reward: What it pays, as ``(currency, amount)`` pairs.
    """

    name: str
    description: str
    done: int = 0
    total: int = 0
    minutes_left: int | None = None
    paid: bool = False
    reward: tuple[tuple[str, int], ...] = ()

    @property
    def reward_text(self) -> str:
        """The reward rendered for logs, e.g. "1 баксы + 75000 монеты".

        Note this is only what the card advertises. Claiming also pays keys,
        between one and five, which appear nowhere on the card — see
        docs/game.md.
        """
        return " + ".join(f"{amount} {currency}" for currency, amount in self.reward)

    @property
    def on_cooldown(self) -> bool:
        """Whether the task is waiting rather than in progress."""
        return self.minutes_left is not None

    @property
    def complete(self) -> bool:
        """Whether the target has been reached."""
        return not self.on_cooldown and self.total > 0 and self.done >= self.total

    @property
    def remaining(self) -> int:
        """How much is still needed to finish it."""
        return max(0, self.total - self.done) if not self.on_cooldown else 0


class QuestBot:
    """Reads the personal task page."""

    def __init__(self, auth: Auth, config: Config):
        """Initialise with an authenticated session."""
        self.session = auth.session
        self.human = auth.human
        self.config = config

    def fetch(self) -> BeautifulSoup:
        """Load the task page."""
        response = self.session.get(self.config.url("/quests"), timeout=self.config.timeout)
        response.raise_for_status()
        self.human.pause_page_load()
        return wicket.parse(response.text)

    def parse(self, soup: BeautifulSoup) -> list[Quest]:
        """Read every task listed on the page."""
        quests: list[Quest] = []

        for block in soup.find_all("div", class_="nfl"):
            title = block.find("b") or block.find("strong")
            if title is None:
                continue
            name = title.get_text(strip=True)
            if not name:
                continue

            text = block.get_text(" ", strip=True)
            description = self._description(block, title)

            cooldown = _COOLDOWN.search(text)
            if cooldown and (cooldown.group(1) or cooldown.group(2)):
                minutes = int(cooldown.group(1) or 0) * 60 + int(cooldown.group(2) or 0)
                quests.append(Quest(name, description, minutes_left=minutes))
                continue

            progress = _PROGRESS.search(text)
            if progress is None:
                continue
            quests.append(
                Quest(
                    name,
                    description,
                    done=_number(progress.group(1)),
                    total=_number(progress.group(2)),
                    paid=bool(_PAID.search(description)),
                    reward=self._reward(block),
                )
            )

        return quests

    @staticmethod
    def _reward(block) -> tuple[tuple[str, int], ...]:
        """Read what a task pays.

        The amounts carry no label: the currency is only identifiable from the
        icon beside each number.
        """
        holder = block.find("span", class_="amount")
        if holder is None:
            return ()

        rewards: list[tuple[str, int]] = []
        for part in holder.find_all("span", recursive=False):
            image = part.find("img")
            amount = _number(part.get_text(" ", strip=True))
            if not amount:
                continue
            icon = (image.get("src", "").rsplit("/", 1)[-1].split(".")[0]) if image else ""
            rewards.append((_CURRENCY.get(icon, icon or "?"), amount))
        return tuple(rewards)

    @staticmethod
    def _description(block, title) -> str:
        """Read the line under the title that says what the task wants.

        Active tasks mark it ``<div class="white">``; tasks on cooldown grey
        the whole block out and use ``<div class="minor nshd">``. Anything else
        is a wrapper, and matching those produced the title back with the
        progress line glued on.
        """
        described = block.find("div", class_="white")
        if described is not None:
            return described.get_text(" ", strip=True)

        holder = title.find_parent("div")
        following = holder.find_next_sibling("div") if holder else None
        if following is not None and "minor" in (following.get("class") or []):
            text = following.get_text(" ", strip=True)
            # The progress and reward lines wear the same class.
            if not text.startswith(("Прогресс", "Награда", "До старта")):
                return text
        return ""

    def claimable(self, soup: BeautifulSoup, page_url: str) -> list[str]:
        """Return the URLs of every reward waiting to be taken.

        Finishing a task is not the end of it: the reward has to be claimed
        separately, and the 20-hour cooldown only starts once it is.
        """
        urls = wicket.find_links_containing(soup, _AWARD_COMPONENT, page_url)
        logger.debug("Found %d claimable reward(s)", len(urls))
        return urls

    def claim_all(self, path: str = "/quests") -> int:
        """Take every reward currently available on a page.

        Each claim re-renders the page and invalidates the remaining links, so
        the page is re-read after every one rather than walking a stale list.

        Args:
            path: Page to collect from, e.g. "/quests" or "/tasks".

        Returns:
            How many rewards were taken.
        """
        taken = 0

        while True:
            response = self.session.get(self.config.url(path), timeout=self.config.timeout)
            response.raise_for_status()
            self.human.pause_page_load()

            urls = self.claimable(wicket.parse(response.text), response.url)
            if not urls:
                break

            # Read the page before taking anything, as a player would.
            self.human.pause(_BEFORE_CLAIM_MULTIPLIER)
            claimed = self.session.get(urls[0], timeout=self.config.timeout)
            claimed.raise_for_status()
            taken += 1

            reward = self._reward_message(wicket.parse(claimed.text))
            logger.info("Reward taken%s", f": {reward}" if reward else "")
            # Linger on the reward as a player would, which also leaves it on
            # screen long enough to be seen in the live view.
            self.human.pause(_REWARD_MULTIPLIER)

        if taken:
            logger.info("Collected %d reward(s) from %s", taken, path)
        else:
            logger.info("Nothing to collect on %s", path)
        return taken

    @staticmethod
    def _reward_message(soup: BeautifulSoup) -> str | None:
        """Return whatever the game said after a reward was taken."""
        return wicket.find_notification(soup)

    def done_today(self, soup: BeautifulSoup) -> tuple[int, int]:
        """Return ``(completed, allowed)`` tasks for today."""
        match = _DONE_TODAY.search(soup.get_text(" ", strip=True))
        return (int(match.group(1)), int(match.group(2))) if match else (0, 0)

    def keys_earned(self, soup: BeautifulSoup) -> int | None:
        """Return how many keys the tasks have produced, as the page reports."""
        match = _KEYS_EARNED.search(soup.get_text(" ", strip=True))
        return _number(match.group(1)) if match else None

    def doubling(self, soup: BeautifulSoup) -> tuple[bool, bool]:
        """Return whether keys are doubled ``(today, tomorrow)``.

        Doubling is earned by claiming the full daily quota the day before, so
        the "tomorrow" banner is really a receipt for having hit today's seven.
        Reading the banners beats recomputing the rule from the calendar.
        """
        text = soup.get_text(" ", strip=True)
        return bool(_DOUBLE_TODAY.search(text)), bool(_DOUBLE_TOMORROW.search(text))

    def quota_left(self, soup: BeautifulSoup) -> int:
        """Return how many more claims today would reach the daily quota.

        The counter tracks claims, not finished work: claiming six rewards
        moved it to six of seven. That is what makes it possible to do the work
        on one day and register it on another.
        """
        completed, allowed = self.done_today(soup)
        return max(0, allowed - completed) if allowed else 0

    def next_available_in(self, quests: list[Quest]) -> int | None:
        """Return minutes until the soonest task unlocks.

        Returns:
            Minutes to wait, 0 when something is available now, or None when
            the page listed nothing at all.
        """
        if not quests:
            return None
        if any(not quest.on_cooldown for quest in quests):
            return 0
        return min(quest.minutes_left for quest in quests if quest.minutes_left is not None)

    def report(self) -> list[Quest]:
        """Log the current state of the task page.

        Returns:
            The parsed tasks.
        """
        soup = self.fetch()
        quests = self.parse(soup)
        completed, allowed = self.done_today(soup)
        keys = self.keys_earned(soup)

        logger.info(
            "Quests: %d today%s%s",
            completed,
            f" of {allowed}" if allowed else "",
            f", {keys} keys earned" if keys is not None else "",
        )
        for quest in quests:
            if quest.on_cooldown:
                logger.info("  %-20s ждёт %d ч %02d мин",
                            quest.name, quest.minutes_left // 60, quest.minutes_left % 60)
            else:
                logger.info("  %-20s %d/%d  %s%s", quest.name, quest.done, quest.total,
                            quest.description, "  [платное]" if quest.paid else "")

        wait = self.next_available_in(quests)
        if wait:
            logger.info("Nothing new for %d ч %02d мин", wait // 60, wait % 60)
        return quests
