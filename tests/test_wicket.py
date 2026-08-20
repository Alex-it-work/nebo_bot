"""Tests for the Wicket parsing helpers, run against saved real pages."""

from __future__ import annotations

import pytest

from src import wicket

LOGIN_URL = "https://nebo.mobi/login"


class TestFindForm:
    def test_finds_login_form_on_the_real_page(self, login_page):
        soup = wicket.parse(login_page)
        form = wicket.find_form(soup, "loginForm")
        assert "loginForm" in form["action"]

    def test_ignores_the_element_id(self, login_page, login_page_with_cookie):
        # Wicket renders a different id on every request (id1, id3, ...), so
        # neither lookup may depend on it.
        first = wicket.find_form(wicket.parse(login_page), "loginForm")
        second = wicket.find_form(wicket.parse(login_page_with_cookie), "loginForm")
        assert first["id"] != second["id"]

    def test_raises_when_the_form_is_absent(self, home_page):
        with pytest.raises(wicket.WicketError):
            wicket.find_form(wicket.parse(home_page), "loginForm")


class TestParseForm:
    def test_resolves_the_relative_action(self, login_page):
        form = wicket.parse_form(wicket.find_form(wicket.parse(login_page), "loginForm"), LOGIN_URL)
        assert form.action_url == (
            "https://nebo.mobi/login;jsessionid=0000000000000000000000000000DEAD"
            "?0-1.-loginForm-loginForm"
        )

    def test_keeps_the_query_string_that_routes_the_submit(self, login_page_with_cookie):
        soup = wicket.parse(login_page_with_cookie)
        form = wicket.parse_form(wicket.find_form(soup, "loginForm"), LOGIN_URL)
        # Losing this query string was the original bug: the POST went to a bare
        # /login, which merely re-rendered the page.
        assert form.action_url.endswith("?1-1.-loginForm-loginForm")

    def test_collects_the_credential_fields(self, login_page):
        form = wicket.parse_form(wicket.find_form(wicket.parse(login_page), "loginForm"), LOGIN_URL)
        assert form.fields == {"login": "", "password": ""}

    def test_records_the_submit_button_name(self, login_page):
        form = wicket.parse_form(wicket.find_form(wicket.parse(login_page), "loginForm"), LOGIN_URL)
        assert form.submit_name == "p::submit"

    def test_carries_hidden_fields_through(self, login_page_with_cookie):
        soup = wicket.parse(login_page_with_cookie)
        form = wicket.parse_form(wicket.find_form(soup, "loginForm"), LOGIN_URL)
        assert form.fields["id3_hf_0"] == "token42"


class TestPayload:
    def test_applies_overrides_and_includes_the_submit(self, login_page):
        form = wicket.parse_form(wicket.find_form(wicket.parse(login_page), "loginForm"), LOGIN_URL)
        payload = form.payload(login="user", password="secret")
        assert payload == {"login": "user", "password": "secret", "p::submit": ""}

    def test_never_contains_a_none_key(self, login_page):
        # The original code inserted `None` as a key when no hidden input existed.
        form = wicket.parse_form(wicket.find_form(wicket.parse(login_page), "loginForm"), LOGIN_URL)
        assert all(isinstance(key, str) for key in form.payload())

    def test_does_not_mutate_the_parsed_form(self, login_page):
        form = wicket.parse_form(wicket.find_form(wicket.parse(login_page), "loginForm"), LOGIN_URL)
        form.payload(login="user")
        assert form.fields["login"] == ""


class TestResolve:
    @pytest.mark.parametrize(
        "href, expected",
        [
            ("./home", "https://nebo.mobi/home"),
            ("/home", "https://nebo.mobi/home"),
            ("./doors?7-1.-doorLink", "https://nebo.mobi/doors?7-1.-doorLink"),
            ("https://nebo.mobi/home", "https://nebo.mobi/home"),
        ],
    )
    def test_resolves_relative_hrefs(self, href, expected):
        assert wicket.resolve("https://nebo.mobi/login", href) == expected


class TestFindLink:
    def test_finds_the_logout_link_despite_surrounding_whitespace(self, home_page):
        # The markup renders it as "<a ...> Выход </a>".
        url = wicket.find_link_href(wicket.parse(home_page), "Выход", "https://nebo.mobi/home")
        assert url == "https://nebo.mobi/home?4-1.-logoutLink"

    def test_returns_none_when_absent(self, login_page):
        assert wicket.find_link_href(wicket.parse(login_page), "Выход", LOGIN_URL) is None


class TestFindLinksContaining:
    def test_collects_only_door_links(self, doors_page):
        urls = wicket.find_links_containing(
            wicket.parse(doors_page), "doorLink", "https://nebo.mobi/doors"
        )
        assert urls == [
            "https://nebo.mobi/doors?3-1.-doorLink1&action=1787078108652",
            "https://nebo.mobi/doors?3-1.-doorLink2&action=1787078108652",
            "https://nebo.mobi/doors?3-1.-doorLink3&action=1787078108652",
        ]

    def test_matches_numbered_component_names(self, doors_page):
        # Components are doorLink1..doorLink3, so matching is on the prefix.
        urls = wicket.find_links_containing(
            wicket.parse(doors_page), "doorLink", "https://nebo.mobi/doors"
        )
        assert len(urls) == 3

    def test_returns_empty_when_no_doors(self, home_page):
        urls = wicket.find_links_containing(
            wicket.parse(home_page), "doorLink", "https://nebo.mobi/home"
        )
        assert urls == []


class TestFindNotification:
    def test_reads_the_dead_end_banner(self, dead_end_page):
        assert wicket.find_notification(wicket.parse(dead_end_page)) == "Вы попали в тупик!"

    def test_returns_none_without_a_banner(self, doors_page):
        assert wicket.find_notification(wicket.parse(doors_page)) is None


class TestFindError:
    def test_reads_the_feedback_panel_error(self, login_error_page):
        # Rejected credentials do not use the game's notify banner, so this is
        # the only way to report why an account failed to log in.
        assert wicket.find_error(wicket.parse(login_error_page)) == "Неверное имя или пароль"

    def test_none_on_a_clean_page(self, login_page):
        assert wicket.find_error(wicket.parse(login_page)) is None

    def test_none_when_the_panel_is_empty(self):
        assert wicket.find_error(wicket.parse('<li class="feedbackPanelERROR"></li>')) is None
