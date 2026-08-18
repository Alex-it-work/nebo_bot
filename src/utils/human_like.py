"""Timing helpers that keep the bot's request pattern irregular."""

from __future__ import annotations

import random
import time

from ..config import Delays

# How often a pause is stretched into a longer "distraction".
_LONG_PAUSE_CHANCE = 0.1
_LONG_PAUSE_RANGE = (2.0, 4.0)
_MICRO_VARIATION = 0.3


class HumanBehavior:
    """Generates varied delays instead of a fixed request cadence.

    Bounds come from the configuration, so pacing can be tuned without touching
    the code.
    """

    def __init__(self, delays: Delays | None = None):
        """Initialise with a timing envelope.

        Args:
            delays: Timing bounds. Defaults are used when omitted.
        """
        self.delays = delays or Delays()

    def delay(self) -> float:
        """Return a randomised pause between actions, in seconds.

        Combines a uniform base with an occasional longer pause and a small
        jitter, so the intervals do not cluster around a single value.
        """
        base = random.uniform(self.delays.min_seconds, self.delays.max_seconds)

        if random.random() < _LONG_PAUSE_CHANCE:
            base += random.uniform(*_LONG_PAUSE_RANGE)

        jittered = base + random.uniform(-_MICRO_VARIATION, _MICRO_VARIATION)
        return max(0.0, jittered)

    def page_load_delay(self) -> float:
        """Return a randomised page-reading pause, in seconds."""
        return random.uniform(self.delays.page_load_min, self.delays.page_load_max)

    def pause(self, multiplier: float = 1.0) -> None:
        """Sleep for :meth:`delay` seconds.

        Args:
            multiplier: Scales the pause; use a value above 1 after a setback,
                where a person would naturally hesitate longer.
        """
        time.sleep(self.delay() * multiplier)

    def pause_page_load(self) -> None:
        """Sleep for :meth:`page_load_delay` seconds."""
        time.sleep(self.page_load_delay())
