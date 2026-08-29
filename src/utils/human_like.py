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

The pacing is applied by :class:`HumanSession`, which every module fetches
through, so it covers every action in the game rather than the ones whose
author remembered to ask for it.
"""

from __future__ import annotations

import contextlib
import logging
import math
import random
import time as time_module
from datetime import datetime, time

import requests

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

    def delay(self, multiplier: float = 1.0) -> float:
        """Return a randomised pause between actions, in seconds.

        The configured minimum and maximum act as the 10th and 90th
        percentiles of a log-normal draw, so most pauses land between them and
        a few run noticeably longer.

        Args:
            multiplier: Scales the thinking time only. Stepping away is an
                event of its own and is never scaled: multiplying a two-minute
                break by the four-fold settling pause after a login left the
                bot motionless for eight minutes with nothing in the log.
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

        if high > _FLOOR_SECONDS:
            base = max(_FLOOR_SECONDS, base)
        thinking = base * multiplier

        if random.random() < self.delays.long_pause_chance:
            # Stepped away for a moment. Added at its own scale.
            thinking += random.uniform(self.delays.long_pause_min, self.delays.long_pause_max)

        return thinking

    def page_load_delay(self) -> float:
        """Return a randomised page-reading pause, in seconds."""
        return random.uniform(self.delays.page_load_min, self.delays.page_load_max)

    def pause(self, multiplier: float = 1.0) -> None:
        """Sleep for :meth:`delay` seconds.

        Args:
            multiplier: Scales the pause; use a value above 1 after a setback,
                where a person would naturally hesitate longer.
        """
        time_module.sleep(self.delay(multiplier))

    def pause_page_load(self) -> None:
        """Sleep for :meth:`page_load_delay` seconds."""
        time_module.sleep(self.page_load_delay())


class HumanSession(requests.Session):
    """A session that paces itself, so no caller has to remember to.

    Pacing used to be a convention: every place that fetched a page also had
    to call :meth:`HumanBehavior.pause` before it and
    :meth:`HumanBehavior.pause_page_load` after it. Conventions get forgotten.
    Reading the key count between mazes fetched a page with no pause at all,
    and every module added later would have had to know the rule.

    So the pause moved into the session. Every request made through this one —
    from any module, written or not yet written — is preceded by a pause for
    thinking and followed by a pause for reading. There is nothing left to
    forget, and the timing envelope is the same one the configuration sets.

    Redirects are followed inside a single request and are not paced
    separately, which is right: a browser follows them without the reader
    noticing.
    """

    def __init__(self, human: "HumanBehavior"):
        """Wrap a pacing policy around an ordinary session.

        Args:
            human: Supplies the pauses. Its delays come from the config.
        """
        super().__init__()
        self.human = human
        self.paced = True

    def request(self, method, url, *args, **kwargs):  # type: ignore[override]
        """Pause, make the request, then pause again as a reader would."""
        if not self.paced:
            return super().request(method, url, *args, **kwargs)

        self.human.pause()
        response = super().request(method, url, *args, **kwargs)
        self.human.pause_page_load()
        return response

    @contextlib.contextmanager
    def unpaced(self):
        """Run a block without pacing.

        For requests a player never makes — a health check, a test — not for
        hurrying the game along.
        """
        previous, self.paced = self.paced, False
        try:
            yield self
        finally:
            self.paced = previous


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
