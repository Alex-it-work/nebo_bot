"""Persistent knowledge about which maze doors lead onward.

Live probing showed the maze is far from random: room 1's first door opened
nine times out of nine, which pure chance would produce about once in 19,700
runs. But it is not fully deterministic either — doors that worked repeatedly
did occasionally dead-end.

So doors are not labelled "good" or "bad". Each ``(room, door)`` pair keeps a
success/failure tally and is scored by a Laplace-smoothed success rate::

    score = (successes + 1) / (successes + failures + 2)

An untried door scores 0.5, a door that has worked three times scores 0.8, and
one that failed its only attempt scores 0.33. Reliable doors are therefore
preferred, unexplored ones are tried before known-poor ones, and a single
surprise degrades a door's standing instead of discarding what was learned.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

logger = logging.getLogger(__name__)


class DoorMemory:
    """Success/failure tallies per room and door, optionally persisted to disk."""

    def __init__(self, path: str | Path | None = None):
        """Load existing knowledge, if any.

        Args:
            path: JSON file to persist to. Memory is kept in RAM only when None.
        """
        self.path = Path(path) if path else None
        # room -> door -> [successes, failures]
        self._rooms: dict[int, dict[int, list[int]]] = {}
        if self.path:
            self._load()

    def _load(self) -> None:
        """Read stored tallies, ignoring a missing or unreadable file."""
        if not self.path or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._rooms = {
                int(room): {int(door): list(counts) for door, counts in doors.items()}
                for room, doors in raw.get("rooms", {}).items()
            }
            logger.debug("Loaded door memory for %d rooms", len(self._rooms))
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            # Corrupt memory is not worth failing a run over; start fresh.
            logger.warning("Ignoring unreadable door memory %s: %s", self.path, exc)
            self._rooms = {}

    def save(self) -> None:
        """Write tallies to disk, creating the directory when needed."""
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "rooms": {
                str(room): {str(door): counts for door, counts in doors.items()}
                for room, doors in self._rooms.items()
            }
        }
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def record(self, room: int, door: int, success: bool) -> None:
        """Note the outcome of opening a door."""
        counts = self._rooms.setdefault(room, {}).setdefault(door, [0, 0])
        counts[0 if success else 1] += 1

    def tally(self, room: int, door: int) -> tuple[int, int]:
        """Return ``(successes, failures)`` recorded for a door."""
        counts = self._rooms.get(room, {}).get(door)
        return (counts[0], counts[1]) if counts else (0, 0)

    def score(self, room: int, door: int) -> float:
        """Return the Laplace-smoothed success rate of a door."""
        successes, failures = self.tally(room, door)
        return (successes + 1) / (successes + failures + 2)

    def choose(self, room: int, doors: list[int]) -> int:
        """Pick the most promising door.

        Ties are broken at random, so a room with no history does not always
        funnel the bot down the same path.

        Args:
            room: Current room number.
            doors: Door numbers available on the page.

        Returns:
            The chosen door number.
        """
        best = max(self.score(room, door) for door in doors)
        return random.choice([door for door in doors if self.score(room, door) == best])

    def summary(self, room: int, doors: list[int]) -> str:
        """Render the tallies for a room, for logging."""
        return " ".join(
            f"{door}:{self.tally(room, door)[0]}/{sum(self.tally(room, door))}" for door in doors
        )
