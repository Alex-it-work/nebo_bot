"""Pacing helpers that keep the bot's activity pattern irregular.

Measuring a real run showed why uniform delays are not enough: 38 clicks
averaged 3.34 s apart with a standard deviation of 0.94 s and never once
paused longer than 6 s. That regularity, not the speed, is what no human
produces.

So pauses are drawn from a log-normal curve, which is skewed the way human
reaction times are: a dense cluster of short gaps with a thin tail of long
ones. On top of that a small fraction of actions get a real break, and both
the session length and the hours of play can be capped — a bot that plays
without pause and without end is the easiest kind to notice.
"""

from __future__ import annotations

import logging
import math
import random
import time as time_module
from datetime import datetime, time

from ..config import Delays

logger = logging.getLogger(__name__)

# Span between the 10th and 90th percentile of a normal curve, in sigmas.
_P10_TO_P90 = 2 * 1.2816

# No action is ever faster than this, whatever the distribution returns.
_FLOOR_SECONDS = 0.4


class HumanBehavior:
    """Generates varied delays instead of a fixed request cadence."""

    def __init__(self, delays: Delays | None = None):
        """Initialise with a timing envelope.

        Args:
            delays: Timing bounds. Defaults are used when omitted.
        """
        self.delays = delays or Delays()

    def delay(self) -> float:
        """Return a randomised pause between actions, in seconds.

        The configured minimum and maximum act as the 10th and 90th
        percentiles of a log-normal draw, so most pauses land between them and
        a few run noticeably longer.
        """
        low, high = self.delays.min_seconds, self.delays.max_seconds
        if high <= 0:
            return 0.0

        if low <= 0 or high <= low:
            # Degenerate range; fall back to a plain uniform draw.
            base = random.uniform(max(0.0, low), high)
        else:
            # Centre on the geometric mean so the median sits between the
            # bounds, and spread so they land near the 10th/90th percentiles.
            mu = math.log(math.sqrt(low * high))
            sigma = math.log(high / low) / _P10_TO_P90
            base = random.lognormvariate(mu, sigma)

        if random.random() < self.delays.long_pause_chance:
            # Stepped away for a moment.
            base += random.uniform(self.delays.long_pause_min, self.delays.long_pause_max)

        return max(_FLOOR_SECONDS, base) if high > _FLOOR_SECONDS else base

    def page_load_delay(self) -> float:
        """Return a randomised page-reading pause, in seconds."""
        return random.uniform(self.delays.page_load_min, self.delays.page_load_max)

    def pause(self, multiplier: float = 1.0) -> None:
        """Sleep for :meth:`delay` seconds.

        Args:
            multiplier: Scales the pause; use a value above 1 after a setback,
                where a person would naturally hesitate longer.
        """
        time_module.sleep(self.delay() * multiplier)

    def pause_page_load(self) -> None:
        """Sleep for :meth:`page_load_delay` seconds."""
        time_module.sleep(self.page_load_delay())


class SessionBudget:
    """Caps how long a single run may keep playing."""

    def __init__(self, max_minutes: int):
        """Start the clock.

        Args:
            max_minutes: Minutes allowed, or 0 for no limit.
        """
        self.max_minutes = max_minutes
        self.started = time_module.monotonic()

    @property
    def unlimited(self) -> bool:
        """Whether this budget imposes no limit at all."""
        return self.max_minutes <= 0

    def elapsed_minutes(self) -> float:
        """Minutes played so far."""
        return (time_module.monotonic() - self.started) / 60

    def expired(self) -> bool:
        """Whether the allotted time has run out."""
        return not self.unlimited and self.elapsed_minutes() >= self.max_minutes


def within_active_hours(window: tuple[time, time] | None, now: datetime | None = None) -> bool:
    """Check whether the current time falls inside the allowed window.

    Windows that wrap past midnight (``22:00-02:00``) are handled.

    Args:
        window: Start and end of the window, or None to allow any time.
        now: Time to test. Defaults to the current local time.

    Returns:
        True if playing is allowed right now.
    """
    if window is None:
        return True

    start, end = window
    current = (now or datetime.now()).time()

    if start <= end:
        return start <= current < end
    # The window wraps past midnight.
    return current >= start or current < end
