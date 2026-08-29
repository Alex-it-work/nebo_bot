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

    def rounds_for(self, account):
        return 1

    def settings_for(self, account):
        return {"maze_rounds": 1, "min_keys": 200, "active_hours": "",
                "spend_baksy": False, "fast": False}

    def remove_account(self, account):
        self.removed = account
        return ""

    def update_account(self, account, values):
        self.updated = (account, values)
        return ""

    def start(self, account, action, rounds=None):
        self.started.append((account, action))
        self.rounds = rounds
        return self.accept

    def add_account(self, username, password):
        self.added = (username, password)
        return ""

    def stop(self, account):
        self.stopped.append(account)
        return True

    def stop_all(self):
        self.stopped.append("*")
        return 2


def post(path: str, server: LiveServer):
    # "?" and "=" stay literal, or the query would arrive as part of the path.
    quoted = urllib.parse.quote(path, safe="/?=&")
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


class TestPanelScript:
    def test_no_inline_handlers(self, server):
        # Concatenating handler attributes nested quotes three deep; one stray
        # quote was a syntax error that killed the script and left the table
        # empty while the server had six accounts.
        server.attach(FakeController())
        assert 'onclick="' not in get("", server)

    def test_rows_and_buttons_are_built_as_elements(self, server):
        server.attach(FakeController())
        page = get("", server)
        assert "createElement" in page and "textContent" in page

    def test_actions_travel_as_data_attributes(self, server):
        server.attach(FakeController())
        page = get("", server)
        assert "dataset.do" in page and "dataset.name" in page

    def test_account_names_are_never_pasted_into_html(self, server):
        # textContent rather than innerHTML, so a name cannot become markup.
        server.attach(FakeController())
        assert "innerHTML" not in get("", server)


class TestAddingAccounts:
    def test_the_panel_offers_a_form(self, server):
        server.attach(FakeController())
        page = get("", server)
        assert "Добавить профиль" in page and 'name="username"' in page

    def test_the_form_hides_the_password(self, server):
        server.attach(FakeController())
        assert 'type="password"' in get("", server)

    def test_columns_explain_themselves(self, server):
        # "Занят" and "Событие" told a newcomer nothing.
        server.attach(FakeController())
        page = get("", server)
        assert "Что делает" in page and "Последний ход" in page and "title=" in page

    def test_rounds_are_shown_and_editable(self, server):
        server.attach(FakeController())
        page = get("", server)
        assert "Кругов" in page and "type = 'number'" in page.replace("'", "'")


class TestFormParsing:
    def test_reads_both_fields(self):
        from src.liveserver import _form_fields

        newline = chr(13) + chr(10)
        body = (
            "------X" + newline + 'Content-Disposition: form-data; name="username"'
            + newline + newline + "Новый" + newline
            + "------X" + newline + 'Content-Disposition: form-data; name="password"'
            + newline + newline + "secret" + newline + "------X--" + newline
        )
        assert _form_fields(body) == {"username": "Новый", "password": "secret"}

    def test_an_empty_body_yields_nothing(self):
        from src.liveserver import _form_fields

        assert _form_fields("") == {}


class TestRowButtonsAreClickable:
    def test_created_buttons_are_not_submit_buttons(self, server):
        # createElement('button') defaults to type="submit", and the click
        # handler ignores submit buttons — every row button went inert.
        server.attach(FakeController())
        assert "element.type = 'button'" in get("", server)

    def test_the_handler_skips_only_buttons_inside_forms(self, server):
        server.attach(FakeController())
        page = get("", server)
        assert "button.closest('form')" in page and "button.type === 'submit'" not in page

    def test_settings_and_remove_are_offered_per_row(self, server):
        server.attach(FakeController())
        page = get("", server)
        assert "'Настройки'" in page and "'Удалить'" in page

    def test_removal_asks_first(self, server):
        server.attach(FakeController())
        assert "confirm(" in get("", server)


class TestTypedValuesSurviveRedraws:
    def test_the_typed_round_count_is_remembered(self, server):
        # The table is rebuilt on every event; the number used to snap back to
        # the saved value, which looked exactly like the click being ignored.
        server.attach(FakeController())
        page = get("", server)
        assert "chosen_rounds" in page

    def test_typing_is_captured_as_it_happens(self, server):
        server.attach(FakeController())
        assert "addEventListener('input'" in get("", server)

    def test_a_field_being_typed_into_is_left_alone(self, server):
        # Rows are updated in place now, so focus is never lost to begin with.
        server.attach(FakeController())
        assert "document.activeElement !== rounds" in get("", server)

    def test_rows_are_updated_rather_than_rebuilt(self, server):
        # Rebuilding threw away whatever was being typed, several times a
        # second, which made the round count impossible to set mid-run.
        server.attach(FakeController())
        page = get("", server)
        assert "replaceChildren()" not in page and "setText" in page


class TestPanelScriptIsValid:
    def test_no_regular_expressions_in_the_panel_script(self, server):
        # A regex written through several layers of escaping arrived broken and
        # took the whole script with it: "Invalid regular expression".
        server.attach(FakeController())
        page = get("", server)
        script = page.split("<script>")[1].split("</script>")[0]
        assert "replace(/" not in script

    def test_rows_are_found_by_name_not_by_selector(self, server):
        server.attach(FakeController())
        assert "row_by_name" in get("", server)

    def test_the_script_has_balanced_braces_and_quotes(self, server):
        # A cheap smoke test for the class of breakage that leaves the table
        # empty with no visible error.
        server.attach(FakeController())
        script = get("", server).split("<script>")[1].split("</script>")[0]
        assert script.count("{") == script.count("}")
        assert script.count("(") == script.count(")")
        assert script.count("'") % 2 == 0


class TestRememberingTheRoundCount:
    """The number typed into the table has to outlive the page showing it."""

    def test_saving_reaches_the_controller(self, server):
        controller = FakeController()
        server.attach(controller)
        status, body = post("rounds/Первый?rounds=9", server)
        assert status == 200 and body == "сохранено"
        assert controller.updated == ("Первый", {"maze_rounds": "9"})

    def test_it_is_saved_as_a_setting_not_as_a_one_off(self, server):
        # Passing it with the run only would leave the file saying 1, which is
        # what came back every time the page was reopened.
        controller = FakeController()
        server.attach(controller)
        post("rounds/Первый?rounds=9", server)
        assert controller.started == []

    def test_a_missing_number_is_refused(self, server):
        server.attach(FakeController())
        with pytest.raises(urllib.error.HTTPError) as raised:
            post("rounds/Первый", server)
        assert raised.value.code == 400

    def test_zero_is_refused(self, server):
        server.attach(FakeController())
        with pytest.raises(urllib.error.HTTPError) as raised:
            post("rounds/Первый?rounds=0", server)
        assert raised.value.code == 400

    def test_nonsense_is_refused(self, server):
        server.attach(FakeController())
        with pytest.raises(urllib.error.HTTPError) as raised:
            post("rounds/Первый?rounds=девять", server)
        assert raised.value.code == 400

    def test_the_field_is_saved_when_it_is_left(self, server):
        # An "input" listener alone only remembered it in this one page.
        script = get("", server)
        assert "addEventListener('change'" in script
        assert "'rounds/'" in script


class TestTheFieldIsNotChangedByAccident:
    """Saving the field made the browser's own quirks dangerous."""

    def test_scrolling_cannot_change_a_number_field(self, server):
        # A wheel over a number input changes it, so scrolling past the table
        # rewrote a saved setting: nine rounds silently became twelve.
        page = get("", server)
        assert "addEventListener('wheel'" in page
        assert "passive: false" in page

    def test_a_change_that_changes_nothing_is_not_written(self, server):
        page = get("", server)
        assert "String(saved_rounds[name])" in page

    def test_the_saved_value_is_tracked(self, server):
        page = get("", server)
        assert "saved_rounds[account.name] = account.rounds" in page
