"""Shared test fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the project root importable so `src` resolves when pytest runs.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    """Read a saved HTML page from the fixtures directory."""
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def login_page() -> str:
    """The real login page as served before a session cookie exists."""
    return load_fixture("login.html")


@pytest.fixture
def login_page_with_cookie() -> str:
    """The login page as served once the session cookie is set."""
    return load_fixture("login_with_cookie.html")


@pytest.fixture
def home_page() -> str:
    """An authenticated home page carrying the logout link."""
    return load_fixture("home.html")


@pytest.fixture
def doors_page() -> str:
    """A maze page offering three doors, with room 3's layout revealed."""
    return load_fixture("doors.html")


@pytest.fixture
def dead_end_page() -> str:
    """A maze page reporting a dead end."""
    return load_fixture("dead_end.html")


@pytest.fixture
def victory_page() -> str:
    """The screen shown after the prize door is opened."""
    return load_fixture("victory.html")

@pytest.fixture
def login_error_page() -> str:
    """The login page after credentials were rejected."""
    return load_fixture("login_error.html")
