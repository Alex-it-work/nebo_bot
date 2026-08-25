"""Tests for the push-based live view."""

from __future__ import annotations

import urllib.request

import pytest

from src.liveserver import LiveServer


@pytest.fixture
def server(tmp_path):
    made = LiveServer(tmp_path, port=8791)
    yield made
    made.stop()


def get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=5) as response:
        return response.read().decode("utf-8")


class TestServing:
    def test_serves_the_shell(self, server):
        assert "<iframe" in get(server.url)

    def test_the_shell_listens_rather_than_polls(self, server):
        page = get(server.url)
        assert "EventSource" in page and "setInterval" not in page

    def test_waits_politely_before_the_first_page(self, server):
        assert "Ожидание" in get(server.url + "page.html")

    def test_serves_the_recorded_page(self, server, tmp_path):
        (tmp_path / "live.html").write_text("<p>Комната 7", encoding="utf-8")
        assert "Комната 7" in get(server.url + "page.html")


class TestNotifications:
    def test_counts_the_pages_it_was_told_about(self, server):
        for _ in range(4):
            server.notify()
        assert server.pages == 4

    def test_notifying_with_nobody_listening_is_harmless(self, server):
        server.notify()
        assert server.pages == 1

    def test_binds_only_to_localhost(self, server):
        # The bot's pages are nobody else's business.
        assert server.url.startswith("http://127.0.0.1:")


class TestLifecycle:
    def test_the_port_is_free_again_after_stopping(self, tmp_path):
        # Stopping without closing left the socket bound, so a second server
        # on the same port could not start.
        first = LiveServer(tmp_path, port=8792)
        first.stop()
        second = LiveServer(tmp_path, port=8792)
        try:
            assert "<iframe" in get(second.url)
        finally:
            second.stop()
