"""Serving the real game through the panel, on an account's own session.

Handing a browser a link with the session id in it does not work, and the
reason is worth writing down: the site sets a session cookie, and a browser
that already has one uses it in preference to the id in the address. So the
link opened whichever profile that browser had last logged into, or the login
page. A session with no cookies at all — which is what it was first tested
with — has nothing to prefer, which is exactly why the test passed.

The fix is to keep the browser away from the game's domain entirely. Pages are
fetched here, on the account's session, and served from the panel's own
address. The browser only ever talks to 127.0.0.1, so its nebo.mobi cookies
never come into it, and several profiles can be open in one browser at once.

Links inside the page are rewritten to point back through here, which also
makes the pages clickable: the profile can actually be played, not just
watched.

This hands out a logged-in session to whoever can reach the panel. It listens
on 127.0.0.1 and must stay there.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

# Attributes carrying a URL. Wicket writes plenty of them per page.
_URL_ATTR = re.compile(r"""\b(href|src|action)\s*=\s*(["'])(.*?)\2""", re.I)

_HEAD = re.compile(r"<head[^>]*>", re.I)

# Schemes that are not addresses to fetch.
_NOT_A_PAGE = ("#", "javascript:", "mailto:", "tel:", "data:", "about:")

# Headers that describe the hop, not the content, and must not be passed on.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "content-encoding",
    "content-length",
}


def prefix_for(account: str) -> str:
    """The panel path under which one account's game is served."""
    return f"/play/{quote(account, safe='')}/"


class GameProxy:
    """Fetches game pages on an account's session and rewrites them.

    Args:
        base_url: Site root, e.g. ``https://nebo.mobi``.
        session_for: Called with an account name; returns a ready session, or
            a string explaining why there is none.
        timeout: Seconds to wait on the game.
    """

    def __init__(self, base_url: str, session_for, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.session_for = session_for
        self.timeout = timeout

    def fetch(self, account: str, path: str, method: str = "GET",
              body: bytes | None = None, content_type: str | None = None):
        """Fetch one page as this account.

        Args:
            account: Whose session to use.
            path: Everything after the account's prefix, query included.
            method: HTTP method to forward.
            body: Request body for a POST.
            content_type: Its content type, forwarded as given.

        Returns:
            ``(status, headers, body)``; the body is bytes, and HTML has been
            rewritten to point back through the proxy.
        """
        session = self.session_for(account)
        if isinstance(session, str):
            return 502, {"Content-Type": "text/html; charset=utf-8"}, session.encode("utf-8")

        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {"Content-Type": content_type} if content_type else {}

        try:
            response = session.request(
                method, url, data=body, headers=headers,
                timeout=self.timeout, allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.warning("Proxy could not reach %s: %s", url, exc)
            return 502, {"Content-Type": "text/html; charset=utf-8"}, \
                f"<p>Игра не ответила: {exc}".encode("utf-8")

        out = {
            name: value for name, value in response.headers.items()
            if name.lower() not in _HOP_BY_HOP
        }

        if "Location" in out:
            out["Location"] = self.rewrite_url(out["Location"], account)

        if "text/html" in response.headers.get("Content-Type", ""):
            return response.status_code, out, self.rewrite(response.text, account).encode("utf-8")
        return response.status_code, out, response.content

    def rewrite(self, html: str, account: str) -> str:
        """Point every link in a page back through the proxy."""
        prefix = prefix_for(account)

        # Relative URLs are left alone and resolved by this instead, which is
        # far safer than trying to re-derive each one's directory.
        based = _HEAD.sub(lambda m: f'{m.group(0)}<base href="{prefix}">', html, count=1)
        if "<base " not in based:
            based = f'<base href="{prefix}">' + based

        return _URL_ATTR.sub(
            lambda m: f'{m.group(1)}={m.group(2)}'
                      f'{self.rewrite_url(m.group(3), account)}{m.group(2)}',
            based,
        )

    def rewrite_url(self, url: str, account: str) -> str:
        """Rewrite one URL, leaving anything that is not a page address alone.

        Relative URLs come back untouched: the injected ``<base>`` already
        resolves them under the prefix, and rewriting them here as well would
        prefix them twice.
        """
        value = url.strip()
        if not value or value.startswith(_NOT_A_PAGE):
            return url

        prefix = prefix_for(account)
        if value.startswith(prefix):
            return url

        for root in (self.base_url, self.base_url.replace("https://", "http://")):
            if value.startswith(root + "/"):
                return prefix + value[len(root) + 1:]
            if value == root:
                return prefix

        if value.startswith("//") or "://" in value.split("/")[0]:
            # Somewhere else entirely; leave it pointing where it points.
            return url
        if value.startswith("/"):
            return prefix + value[1:]
        return url
