"""Tests for the shared live view."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from src.liveserver import LiveServer


@pytest.fixture
def server():
    made = LiveServer(port=8791)
    yield made
    made.stop()


def get(path: str, server: LiveServer) -> str:
    # A browser percent-encodes the account name; so must the test.
    quoted = urllib.parse.quote(path, safe="/?=")
    with urllib.request.urlopen(server.url + quoted, timeout=5) as response:
        return response.read().decode("utf-8")


class TestOverview:
    def test_lists_nothing_before_anyone_joins(self, server):
        assert server.channels == {}

    def test_serves_the_overview(self, server):
        assert "Профилей" in get("", server)

    def test_the_overview_listens_rather_than_polls(self, server):
        page = get("", server)
        assert "EventSource" in page and "setInterval" not in page

    def test_summarises_every_account(self, server):
        server.publish("Первый", "<p>a", "Лабиринт")
        server.publish("Второй", "<p>b", "Задания")
        summary = json.loads(server._summary())
        assert {row["name"] for row in summary} == {"Первый", "Второй"}

    def test_the_summary_carries_what_each_is_doing(self, server):
        server.publish("Первый", "<p>a", "Лабиринт")
        row = json.loads(server._summary())[0]
        assert row["title"] == "Лабиринт" and row["pages"] == 1


class TestManyAccounts:
    def test_one_port_serves_them_all(self, server):
        # Thirty ports and thirty tabs would not be a dashboard.
        for number in range(30):
            server.publish(f"Профиль{number}", "<p>x", "Лифт")
        assert len(server.channels) == 30
        assert "Профилей" in get("", server)

    def test_each_account_keeps_its_own_page(self, server):
        server.publish("Первый", "<p>комната 3", "Лабиринт")
        server.publish("Второй", "<p>комната 7", "Лабиринт")
        assert "комната 3" in get("watch/Первый/page", server)
        assert "комната 7" in get("watch/Второй/page", server)

    def test_counts_pages_per_account(self, server):
        for _ in range(3):
            server.publish("Первый", "<p>x", "t")
        server.publish("Второй", "<p>x", "t")
        assert server.channels["Первый"].pages == 3
        assert server.channels["Второй"].pages == 1

    def test_a_watch_page_names_its_account(self, server):
        server.publish("Первый", "<p>x", "t")
        assert "Первый" in get("watch/Первый", server)

    def test_an_unknown_account_waits_rather_than_breaking(self, server):
        assert "Ожидание" in get("watch/Нетакого/page", server)


class TestWatchPage:
    def test_uses_two_frames_so_nothing_half_drawn_shows(self, server):
        server.publish("Первый", "<p>x", "t")
        assert get("watch/Первый", server).count("<iframe") == 2

    def test_reveals_a_frame_only_once_it_has_loaded(self, server):
        server.publish("Первый", "<p>x", "t")
        assert "onload" in get("watch/Первый", server)

    def test_wears_the_games_own_blue(self, server):
        # Taken from the game's stylesheet: body #036, header #174577.
        server.publish("Первый", "<p>x", "t")
        styles = get("watch/Первый", server).split("<style>")[1].split("</style>")[0]
        assert "#036" in styles and "#174577" in styles

    def test_no_frame_paints_itself_white(self, server):
        # A white frame background was what burned the eyes on every swap.
        # Only the frame rule matters here: white button text is fine.
        server.publish("Первый", "<p>x", "t")
        styles = get("watch/Первый", server).split("<style>")[1].split("</style>")[0]
        frame_rule = styles.split("iframe{")[1].split("}")[0]
        assert "#fff" not in frame_rule.lower() and "#036" in frame_rule


class TestSafety:
    def test_binds_only_to_localhost(self, server):
        # The bot's pages are nobody else's business.
        assert server.url.startswith("http://127.0.0.1:")

    def test_publishing_with_nobody_watching_is_harmless(self, server):
        server.publish("Первый", "<p>x", "t")
        assert server.channels["Первый"].pages == 1

    def test_an_account_name_cannot_inject_markup(self, server):
        server.publish("<script>alert(1)</script>", "<p>x", "t")
        assert "<script>alert(1)</script>" not in json.loads(server._summary())[0]["name"]

    def test_unknown_paths_answer_404_rather_than_crashing(self, server):
        with pytest.raises(urllib.error.HTTPError) as raised:
            get("no/such/place", server)
        assert raised.value.code == 404
        assert "Нет такой" in raised.value.read().decode("utf-8")


class TestLifecycle:
    def test_the_port_is_free_again_after_stopping(self):
        # Stopping without closing left the socket bound, so a second server
        # on the same port could not start.
        first = LiveServer(port=8792)
        first.stop()
        second = LiveServer(port=8792)
        try:
            assert "Профилей" in get("", second)
        finally:
            second.stop()

    def test_accounts_share_one_server(self):
        first = LiveServer.shared(port=8793)
        try:
            assert LiveServer.shared(port=8793) is first
        finally:
            first.stop()

    def test_stopping_the_shared_server_lets_a_new_one_start(self):
        first = LiveServer.shared(port=8794)
        first.stop()
        second = LiveServer.shared(port=8794)
        try:
            assert second is not first
        finally:
            second.stop()


class TestNoNesting:
    def test_the_frame_points_at_an_absolute_page_url(self, server):
        # A relative "page" resolved to /watch/page, which was then served as
        # an account named "page": the shell rendered inside itself.
        server.publish("Первый", "<p>x", "t")
        page = get("watch/Первый", server)
        assert 'src="/watch/%D0%9F%D0%B5%D1%80%D0%B2%D1%8B%D0%B9/page"' in page

    def test_the_event_stream_is_absolute_too(self, server):
        server.publish("Первый", "<p>x", "t")
        assert "EventSource('/watch/" in get("watch/Первый", server)

    def test_the_watch_page_is_not_served_inside_itself(self, server):
        server.publish("Первый", "<p>x", "t")
        assert "<iframe" not in get("watch/Первый/page", server)

    def test_back_link_goes_to_the_root(self, server):
        server.publish("Первый", "<p>x", "t")
        assert 'href="/"' in get("watch/Первый", server)


class FakeController:
    """Stands in for the real controller in dashboard tests."""

    def __init__(self):
        self.started: list[tuple[str, str]] = []
        self.stopped: list[str] = []
        self.busy: dict[str, str] = {}
        self.accept = True

    def names(self):
        return ["Первый", "Второй"]

    def status(self, account):
        return self.busy.get(account, "—")

    def start(self, account, action):
        self.started.append((account, action))
        return self.accept

    def stop(self, account):
        self.stopped.append(account)
        return True

    def stop_all(self):
        self.stopped.append("*")
        return 2


def post(path: str, server: LiveServer):
    quoted = urllib.parse.quote(path, safe="/")
    request = urllib.request.Request(server.url + quoted, method="POST", data=b"")
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, response.read().decode("utf-8")


class TestControls:
    def test_the_overview_offers_buttons(self, server):
        server.attach(FakeController())
        page = get("", server)
        assert "Лабиринт" in page and "Награды" in page and "Стоп" in page

    def test_starting_a_job_reaches_the_controller(self, server):
        controller = FakeController()
        server.attach(controller)
        status, _ = post("run/Первый/maze", server)
        assert status == 200 and controller.started == [("Первый", "maze")]

    def test_a_busy_account_is_refused_rather_than_queued(self, server):
        controller = FakeController()
        controller.accept = False
        server.attach(controller)
        with pytest.raises(urllib.error.HTTPError) as raised:
            post("run/Первый/maze", server)
        assert raised.value.code == 409

    def test_stopping_one_account(self, server):
        controller = FakeController()
        server.attach(controller)
        post("stop/Первый", server)
        assert controller.stopped == ["Первый"]

    def test_stopping_everything(self, server):
        controller = FakeController()
        server.attach(controller)
        post("stop-all", server)
        assert controller.stopped == ["*"]

    def test_attaching_registers_every_account(self, server):
        server.attach(FakeController())
        assert set(server.channels) == {"Первый", "Второй"}

    def test_the_table_shows_what_each_is_busy_with(self, server):
        controller = FakeController()
        controller.busy["Первый"] = "Пройти лабиринт"
        server.attach(controller)
        assert "Пройти лабиринт" in server._summary()

    def test_controls_are_inert_without_a_controller(self, server):
        with pytest.raises(urllib.error.HTTPError) as raised:
            post("run/Первый/maze", server)
        assert raised.value.code == 503

    def test_an_unknown_command_is_refused(self, server):
        server.attach(FakeController())
        with pytest.raises(urllib.error.HTTPError) as raised:
            post("nonsense", server)
        assert raised.value.code == 404
