"""Keeping a browsable copy of the pages the bot saw.

The game is plain server-rendered HTML, so a saved response opens in a browser
looking like the real thing — provided relative URLs still resolve. A ``<base>``
tag pointing at the site takes care of that: images load from the game and the
links stay clickable, leading to the live game rather than to nothing.

Two ways to look at it. ``keep`` retains the last few pages as a browsable
history, and ``live`` additionally writes every page over a single file that
carries a refresh header, so a browser left open on it shows the bot playing
in real time rather than a pile of files to click through. Either can be used
without the other.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_HEAD = re.compile(r"<head[^>]*>", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


class PageRecorder:
    """Writes recent pages to disk so they can be opened in a browser."""

    def __init__(
        self,
        directory: str | Path,
        base_url: str,
        keep: int = 50,
        live: bool = False,
        refresh_seconds: int = 1,
    ):
        """Prepare the output directory.

        Args:
            directory: Where to write the pages and their index.
            base_url: Site root, injected as ``<base>`` so assets resolve.
            keep: How many pages to retain as history, or 0 for none.
            live: Also overwrite ``live.html`` with the newest page.
            refresh_seconds: How often the live page reloads itself.
        """
        self.directory = Path(directory)
        self.base_url = base_url.rstrip("/") + "/"
        self.keep = max(0, keep)
        self.live = live
        self.refresh_seconds = max(1, refresh_seconds)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._pages: list[tuple[str, str, str]] = []
        # Monotonic: naming from the list length would repeat numbers as soon
        # as pruning began, and later pages would overwrite earlier ones.
        self._written = 0

    def hook(self, response: requests.Response, *args, **kwargs) -> requests.Response:
        """Record a response. Suitable for ``session.hooks["response"]``."""
        try:
            if "text/html" in response.headers.get("Content-Type", ""):
                self.record(response)
        except OSError as exc:
            # Recording is a convenience; never let it break a run.
            logger.debug("Could not record %s: %s", response.url, exc)
        return response

    def record(self, response: requests.Response) -> Path:
        """Write one page and refresh the index."""
        stamp = datetime.now()
        self._written += 1
        name = f"{stamp:%H%M%S}_{self._written:05d}.html"
        path = self.directory / name

        html = self._prepare(response.text)

        if self.live:
            # No URL in the refresh directive: the document reloads itself,
            # while a URL would be resolved against <base> and hit the game.
            live = html.replace(
                "<base ",
                f'<meta http-equiv="refresh" content="{self.refresh_seconds}"><base ',
                1,
            )
            (self.directory / "live.html").write_text(live, encoding="utf-8")

        if not self.keep:
            return self.directory / "live.html"

        path.write_text(html, encoding="utf-8")
        title = _TITLE.search(response.text)
        self._pages.append(
            (name, f"{stamp:%H:%M:%S}", title.group(1).strip() if title else response.url)
        )
        self._prune()
        self._write_index()
        return path

    def _prepare(self, html: str) -> str:
        """Insert the base tag so images and links keep working."""
        prepared = _HEAD.sub(
            lambda m: f'{m.group(0)}<base href="{self.base_url}">', html, count=1
        )
        if "<base " not in prepared:
            prepared = f'<base href="{self.base_url}">' + prepared
        return prepared

    def _prune(self) -> None:
        """Drop the oldest pages beyond the retention limit."""
        while len(self._pages) > self.keep:
            name, _, _ = self._pages.pop(0)
            (self.directory / name).unlink(missing_ok=True)

    def _write_index(self) -> None:
        """Rewrite the index listing, newest first."""
        rows = "\n".join(
            f'<li><a href="{name}">{when}</a> — {title}</li>'
            for name, when, title in reversed(self._pages)
        )
        self.directory.joinpath("index.html").write_text(
            "<!doctype html><meta charset='utf-8'><title>Что видел бот</title>"
            "<style>body{font:14px system-ui;margin:2rem}li{margin:.2rem 0}</style>"
            f"<h1>Что видел бот</h1><p>Последние {len(self._pages)} страниц, свежие сверху.</p>"
            f"<ol reversed>{rows}</ol>",
            encoding="utf-8",
        )
