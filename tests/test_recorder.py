"""Tests for the browsable page recorder."""

from __future__ import annotations

import requests

from src.recorder import PageRecorder


def response(html: str, url: str = "https://nebo.mobi/doors") -> requests.Response:
    made = requests.Response()
    made._content = html.encode("utf-8")
    made.encoding = "utf-8"
    made.url = url
    made.headers["Content-Type"] = "text/html; charset=utf-8"
    made.status_code = 200
    return made


PAGE = "<html><head><title>Лабиринт</title></head><body>Комната 3</body></html>"


class TestRecording:
    def test_writes_the_page(self, tmp_path):
        path = PageRecorder(tmp_path, "https://nebo.mobi").record(response(PAGE))
        assert path.is_file() and "Комната 3" in path.read_text(encoding="utf-8")

    def test_injects_a_base_so_images_and_links_resolve(self, tmp_path):
        # Without this the saved page opens without styling or pictures and
        # every link is dead.
        path = PageRecorder(tmp_path, "https://nebo.mobi").record(response(PAGE))
        assert '<base href="https://nebo.mobi/">' in path.read_text(encoding="utf-8")

    def test_copes_with_a_page_that_has_no_head(self, tmp_path):
        path = PageRecorder(tmp_path, "https://nebo.mobi").record(response("<body>hi</body>"))
        assert path.read_text(encoding="utf-8").startswith('<base href=')

    def test_writes_an_index_naming_the_pages(self, tmp_path):
        PageRecorder(tmp_path, "https://nebo.mobi").record(response(PAGE))
        index = (tmp_path / "index.html").read_text(encoding="utf-8")
        assert "Лабиринт" in index and ".html" in index


class TestRetention:
    def test_keeps_only_the_most_recent(self, tmp_path):
        recorder = PageRecorder(tmp_path, "https://nebo.mobi", keep=3)
        for _ in range(10):
            recorder.record(response(PAGE))
        pages = [p for p in tmp_path.iterdir() if p.name != "index.html"]
        assert len(pages) == 3

    def test_a_run_of_hundreds_does_not_grow_the_directory(self, tmp_path):
        # A single maze evening opens hundreds of pages; the point is to see
        # the last few, not to archive them.
        recorder = PageRecorder(tmp_path, "https://nebo.mobi", keep=5)
        for _ in range(300):
            recorder.record(response(PAGE))
        assert len([p for p in tmp_path.iterdir() if p.name != "index.html"]) == 5


class TestHook:
    def test_records_html_responses(self, tmp_path):
        recorder = PageRecorder(tmp_path, "https://nebo.mobi")
        recorder.hook(response(PAGE))
        assert len([p for p in tmp_path.iterdir() if p.name != "index.html"]) == 1

    def test_ignores_non_html(self, tmp_path):
        recorder = PageRecorder(tmp_path, "https://nebo.mobi")
        image = response(PAGE)
        image.headers["Content-Type"] = "image/png"
        recorder.hook(image)
        assert [p.name for p in tmp_path.iterdir()] == []

    def test_returns_the_response_unchanged(self, tmp_path):
        original = response(PAGE)
        assert PageRecorder(tmp_path, "https://nebo.mobi").hook(original) is original

    def test_pages_never_overwrite_each_other(self, tmp_path):
        # Naming from the list length repeated numbers once pruning started.
        recorder = PageRecorder(tmp_path, "https://nebo.mobi", keep=3)
        for _ in range(12):
            recorder.record(response(PAGE))
        names = {p.name for p in tmp_path.iterdir() if p.name != "index.html"}
        assert len(names) == 3
        assert recorder._written == 12


class TestWiring:
    def test_recording_off_leaves_the_session_clean(self):
        from src.config import Config
        from src.modules.auth import Auth

        auth = Auth(Config(username="u", password="p"))
        assert auth.recorder is None and auth.session.hooks["response"] == []

    def test_recording_on_installs_the_hook_after_the_session_exists(self, tmp_path):
        # The hook was once attached before the session was created.
        from src.config import Config
        from src.modules.auth import Auth

        auth = Auth(Config(username="u", password="p", record_pages=5,
                           record_dir=str(tmp_path)))
        assert auth.recorder is not None
        assert auth.recorder.hook in auth.session.hooks["response"]


class TestLiveView:
    def test_writes_a_single_live_file(self, tmp_path):
        recorder = PageRecorder(tmp_path, "https://nebo.mobi", keep=0, live=True)
        for _ in range(5):
            recorder.record(response(PAGE))
        assert [p.name for p in tmp_path.iterdir()] == ["live.html"]

    def test_announces_each_page_instead_of_being_polled(self, tmp_path):
        # The bot knows when a page arrives; nothing should poll for it.
        seen = []
        recorder = PageRecorder(tmp_path, "https://nebo.mobi", keep=0, live=True,
                                on_page=lambda: seen.append(1))
        for _ in range(3):
            recorder.record(response(PAGE))
        assert len(seen) == 3

    def test_the_live_page_carries_no_timer(self, tmp_path):
        PageRecorder(tmp_path, "https://nebo.mobi", keep=0, live=True).record(response(PAGE))
        assert "http-equiv" not in (tmp_path / "live.html").read_text(encoding="utf-8")

    def test_history_alone_announces_nothing(self, tmp_path):
        seen = []
        recorder = PageRecorder(tmp_path, "https://nebo.mobi", keep=2, live=False,
                                on_page=lambda: seen.append(1))
        recorder.record(response(PAGE))
        assert seen == []

    def test_the_live_page_is_always_the_newest(self, tmp_path):
        recorder = PageRecorder(tmp_path, "https://nebo.mobi", keep=0, live=True)
        recorder.record(response("<html><head></head><body>первая</body></html>"))
        recorder.record(response("<html><head></head><body>вторая</body></html>"))
        html = (tmp_path / "live.html").read_text(encoding="utf-8")
        assert "вторая" in html and "первая" not in html

    def test_history_and_live_can_run_together(self, tmp_path):
        recorder = PageRecorder(tmp_path, "https://nebo.mobi", keep=3, live=True)
        for _ in range(4):
            recorder.record(response(PAGE))
        names = {p.name for p in tmp_path.iterdir()}
        assert "live.html" in names and "index.html" in names and len(names) == 5

    def test_history_alone_writes_no_live_file(self, tmp_path):
        recorder = PageRecorder(tmp_path, "https://nebo.mobi", keep=2, live=False)
        recorder.record(response(PAGE))
        assert "live.html" not in {p.name for p in tmp_path.iterdir()}
