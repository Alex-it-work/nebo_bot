"""Authentication and session management for nebo.mobi."""

from __future__ import annotations

import logging

import requests

from .. import wicket
from ..liveserver import LiveServer
from ..recorder import PageRecorder
from ..config import Config
from ..utils.human_like import HumanBehavior

logger = logging.getLogger(__name__)

# Sent on every request so the session looks like an ordinary mobile browser
# visiting a WAP-oriented site.
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,uk;q=0.8,en;q=0.7",
}


class AuthError(Exception):
    """Raised when authentication cannot proceed."""


class Auth:
    """Manages the logged-in session.

    Owns the ``requests.Session`` that every other module borrows, so cookies
    are shared across the whole bot.
    """

    def __init__(self, config: Config, session: requests.Session | None = None):
        """Initialise the session.

        Args:
            config: Validated bot configuration.
            session: Existing session to reuse. A fresh one is created when
                omitted; mainly an injection point for tests.
        """
        self.config = config
        self.human = HumanBehavior(config.delays)

        self.session = session or requests.Session()
        self.session.headers.update(_DEFAULT_HEADERS)

        self.recorder: PageRecorder | None = None
        self.live: LiveServer | None = None
        if config.live_view:
            self.live = LiveServer(config.record_dir, config.live_port)
        if config.record_pages or config.live_view:
            # A response hook catches every page without each module knowing
            # that recording exists at all.
            self.recorder = PageRecorder(
                config.record_dir,
                config.base_url,
                keep=config.record_pages,
                live=config.live_view,
                on_page=self.live.notify if self.live else None,
            )
            self.session.hooks["response"].append(self.recorder.hook)

    @property
    def base_url(self) -> str:
        """Site root, exposed for modules that build their own URLs."""
        return self.config.base_url

    def login(self) -> bool:
        """Authenticate with the credentials from the configuration.

        Fetches the login page, submits the form exactly as rendered, then
        confirms the session really is authenticated rather than trusting the
        response body.

        Returns:
            True if the session is authenticated afterwards.
        """
        logger.info("Logging in as %s", self.config.username)

        try:
            self.human.pause()
            page = self._get(self.config.url("/login"))

            soup = wicket.parse(page.text)
            form = wicket.parse_form(wicket.find_form(soup, "loginForm"), page.url)
            logger.debug("Login form action: %s", form.action_url)

            # Read the page as a human would before typing.
            self.human.pause_page_load()

            response = self.session.post(
                form.action_url,
                data=form.payload(login=self.config.username, password=self.config.password),
                timeout=self.config.timeout,
                allow_redirects=True,
            )
            response.raise_for_status()

            if self.is_authenticated():
                logger.info("Authenticated successfully")
                return True

            # Not authenticated: surface whatever the server complained about.
            # Bad credentials land in Wicket's feedback panel, other problems
            # in the game's own banner, so check both.
            soup = wicket.parse(response.text)
            reason = wicket.find_error(soup) or wicket.find_notification(soup)
            if reason:
                logger.error("Login failed for %s: %s", self.config.username, reason)
            else:
                logger.error(
                    "Login failed for %s; the session is not authenticated", self.config.username
                )
            return False

        except wicket.WicketError as exc:
            logger.error("Login page markup has changed: %s", exc)
            return False
        except requests.RequestException as exc:
            logger.error("Login request failed: %s", exc)
            return False

    def is_authenticated(self) -> bool:
        """Check whether the session is currently logged in.

        Requests ``/home`` without following redirects. The site serves it only
        to authenticated sessions and bounces everyone else to ``/welcome``,
        which makes this a more reliable signal than inspecting cookies.

        Returns:
            True if the session is logged in.
        """
        try:
            response = self.session.get(
                self.config.url("/home"),
                timeout=self.config.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            logger.error("Could not verify the session: %s", exc)
            return False

        if response.status_code == 200:
            return True

        if response.is_redirect:
            logger.debug("Not authenticated, /home redirects to %s", response.headers.get("Location"))
        else:
            logger.debug("Unexpected status %s from /home", response.status_code)
        return False

    def logout(self) -> bool:
        """Log out by following the site's own logout link.

        Returns:
            True if the session is no longer authenticated. Also True when the
            session was already logged out, since there is nothing to do.
        """
        if not self.is_authenticated():
            logger.debug("Already logged out")
            return True

        logger.info("Logging out")

        try:
            self.human.pause()
            page = self._get(self.config.url("/home"))

            logout_url = wicket.find_link_href(wicket.parse(page.text), "Выход", page.url)
            if logout_url is None:
                logger.error("Logout link not found on /home")
                return False

            logger.debug("Logout URL: %s", logout_url)
            self.session.get(
                logout_url,
                timeout=self.config.timeout,
                allow_redirects=True,
            ).raise_for_status()

            if self.is_authenticated():
                logger.error("Logout request completed but the session is still active")
                return False

            logger.info("Logged out")
            return True

        except requests.RequestException as exc:
            logger.error("Logout request failed: %s", exc)
            return False

    def _get(self, url: str) -> requests.Response:
        """Fetch a page, raising on HTTP errors."""
        response = self.session.get(url, timeout=self.config.timeout)
        response.raise_for_status()
        return response
