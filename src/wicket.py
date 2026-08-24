"""
Helpers for talking to an Apache Wicket application.

Everything that depends on Wicket's URL and markup conventions lives here, so
that a framework upgrade on the server only requires changes to this module.

Background: nebo.mobi runs a modern Wicket (6+). Its URLs look like

    ./login?0-1.-loginForm-loginForm

where ``0`` is the page version and the rest identifies the component and its
listener. Older Wicket (1.x) used a completely different scheme:

    ?wicket:interface=:0:loginForm::IFormSubmitListener

Two consequences drive the design below:

* The page version increments on every request, so a form action or link href
  is only valid for the page it was read from. Never cache or hardcode one.
* Before the session cookie is established, Wicket embeds the session id in the
  path as ``;jsessionid=...``. A ``requests.Session`` picks the cookie up on the
  first response, after which URLs come back clean.
"""

from __future__ import annotations

import re

from dataclasses import dataclass, field
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

# Input types that carry no value we should submit.
_SKIPPED_INPUT_TYPES = frozenset({"submit", "button", "reset", "image", "file"})


class WicketError(Exception):
    """Raised when the expected Wicket markup cannot be found on a page."""


@dataclass
class WicketForm:
    """A parsed HTML form, ready to be submitted.

    Attributes:
        action_url: Absolute URL to POST to, resolved against the page URL.
        fields: Field names mapped to their current values, including hidden
            fields. Pre-filled with whatever the server rendered.
        submit_name: ``name`` of the submit button, or None if the form has no
            named submit. Wicket needs this to know which button was pressed.
    """

    action_url: str
    fields: dict[str, str] = field(default_factory=dict)
    submit_name: str | None = None

    def payload(self, **overrides: str) -> dict[str, str]:
        """Build the POST body, overriding or adding the given fields.

        The submit button is included automatically, because Wicket routes the
        submission based on which button reported itself as pressed.
        """
        data = dict(self.fields)
        data.update(overrides)
        if self.submit_name:
            data.setdefault(self.submit_name, "")
        return data


def parse(html: str) -> BeautifulSoup:
    """Parse a page into a BeautifulSoup tree."""
    return BeautifulSoup(html, "html.parser")


def resolve(page_url: str, href: str) -> str:
    """Turn a possibly relative href into an absolute URL.

    Wicket emits relative hrefs such as ``./home`` or ``./login?0-1.-form``,
    which only make sense relative to the URL the page was served from.
    """
    return urljoin(page_url, href)


def find_form(soup: BeautifulSoup, action_contains: str) -> Tag:
    """Locate a POST form whose action contains the given substring.

    Matching on the action rather than the element id is deliberate: Wicket
    regenerates ids (``id1``, ``id3``, ...) on every render, while the component
    name inside the action is stable.

    Raises:
        WicketError: If no matching form exists on the page.
    """
    form = soup.find(
        "form",
        attrs={
            "method": lambda value: value is not None and value.lower() == "post",
            "action": lambda value: value is not None and action_contains in value,
        },
    )
    if not isinstance(form, Tag):
        raise WicketError(f"No POST form with {action_contains!r} in its action")
    return form


def parse_form(form: Tag, page_url: str) -> WicketForm:
    """Extract the action URL and all submittable fields from a form.

    Collects every named input, textarea and select, so hidden fields the server
    added are carried through untouched. Unchecked checkboxes and radios are
    omitted, matching real browser behaviour.
    """
    action = form.get("action")
    if not isinstance(action, str) or not action:
        raise WicketError("Form has no action attribute")

    parsed = WicketForm(action_url=resolve(page_url, action))

    for element in form.find_all(["input", "textarea", "select"]):
        name = element.get("name")
        if not isinstance(name, str) or not name:
            continue

        if element.name == "input":
            input_type = (element.get("type") or "text").lower()
            if input_type in _SKIPPED_INPUT_TYPES:
                if input_type == "submit" and parsed.submit_name is None:
                    parsed.submit_name = name
                continue
            if input_type in ("checkbox", "radio") and not element.has_attr("checked"):
                continue
            parsed.fields[name] = element.get("value") or ""

        elif element.name == "textarea":
            parsed.fields[name] = element.get_text()

        else:  # select
            selected = element.find("option", selected=True) or element.find("option")
            parsed.fields[name] = (selected.get("value") or selected.get_text()) if selected else ""

    return parsed


def find_link(soup: BeautifulSoup, text: str) -> Tag | None:
    """Find an anchor by its exact visible text, whitespace-insensitive.

    Uses ``string=`` rather than the long-deprecated ``text=`` argument, and
    compares stripped text so surrounding markup whitespace does not matter.
    """
    for anchor in soup.find_all("a"):
        if anchor.get_text(strip=True) == text:
            return anchor
    return None


def find_link_href(soup: BeautifulSoup, text: str, page_url: str) -> str | None:
    """Return the absolute URL of the anchor with the given text, if present."""
    anchor = find_link(soup, text)
    if anchor is None:
        return None
    href = anchor.get("href")
    if not isinstance(href, str) or not href:
        return None
    return resolve(page_url, href)


def find_links_containing(soup: BeautifulSoup, component: str, page_url: str) -> list[str]:
    """Return absolute URLs of every anchor whose href mentions a component name.

    Wicket puts the component's name into the href (for example ``doorLink``),
    and that name survives framework upgrades even though the surrounding URL
    format does not. Matching on it is therefore the most durable option.
    """
    urls: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if component in href:
            urls.append(resolve(page_url, href))
    return urls


def find_notification(soup: BeautifulSoup) -> str | None:
    """Return the text of the page's notification/feedback banner, if any.

    The game renders both errors and status messages in ``<span class="notify">``.
    """
    notify = soup.find("span", class_="notify")
    if notify is None:
        return None
    text = notify.get_text(strip=True)
    return text or None


def find_error(soup: BeautifulSoup) -> str | None:
    """Return the text of Wicket's feedback panel error, if any.

    Form errors do not use the game's ``<span class="notify">`` banner. They
    come back in the framework's own feedback panel::

        <li class="errorlevel feedbackPanelERROR">
          <span class="errorlevel">Неверное имя или пароль</span>
        </li>

    Reporting it is what turns "login failed" into something actionable when a
    whole list of accounts is being run.
    """
    panel = soup.find(class_="feedbackPanelERROR")
    if panel is None:
        return None
    text = panel.get_text(" ", strip=True)
    return text or None


def find_online_count(soup: BeautifulSoup) -> int | None:
    """Return how many players the footer reports as online.

    Recorded alongside maze outcomes so the belief that the maze is easier at
    quiet hours can be tested rather than argued about.
    """
    for link in soup.find_all("a"):
        text = link.get_text(" ", strip=True)
        if text.startswith("Онлайн:"):
            digits = re.sub(r"\D", "", text)
            return int(digits) if digits else None
    return None
