"""One live view for however many accounts are playing.

Reloading on a timer is guesswork — the bot knows exactly when a page arrives,
so the browser is told instead. And a view per account does not scale: thirty
ports and thirty tabs is not a dashboard. A single server therefore holds a
channel per account, serves an overview of all of them, and lets any one be
watched by clicking into it.

The server runs on a daemon thread bound to localhost, so it never delays a
run and never listens beyond this machine.
"""

from __future__ import annotations

import html as html_module
import json
import logging
import queue
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote

logger = logging.getLogger(__name__)

WAITING = "<p>Ожидание первой страницы…"

# The game's own palette, taken from its stylesheet: #036 behind everything,
# #174577 on the header, #6fcd72 for its green.
_STYLE = """
 html,body{margin:0;height:100%;background:#036;color:#ddd;font:13px system-ui;
           text-shadow:0 1px 5px black}
 a{color:#ffdf8c}
 header{display:flex;gap:1rem;align-items:center;padding:.4rem .8rem;background:#174577}
 .dot{width:.6rem;height:.6rem;border-radius:50%;background:#6fcd72;display:inline-block}
 .gone{background:#c44}
 table{border-collapse:collapse;width:100%;max-width:60rem}
 td,th{padding:.35rem .6rem;text-align:left;border-bottom:1px solid #174577}
 main{padding:1rem}
 .stage{position:relative;height:calc(100% - 2rem)}
 iframe{position:absolute;inset:0;border:0;width:100%;height:100%;
        background:#036;opacity:0;transition:opacity .12s}
 iframe.shown{opacity:1}
"""

_OVERVIEW = """<!doctype html><meta charset="utf-8"><title>Боты играют</title>
<style>STYLE</style>
<header><span class="dot" id="dot"></span><span>Профилей: <b id="n">0</b></span>
<span id="when">ожидание…</span></header>
<main><table><thead><tr><th>Профиль<th>Страница<th>Последнее событие<th>Страниц
</tr></thead><tbody id="rows"></tbody></table></main>
<script>
 var rows = document.getElementById('rows');
 var events = new EventSource('events');
 events.onmessage = function (e) {
   var state = JSON.parse(e.data);
   document.getElementById('n').textContent = state.length;
   document.getElementById('when').textContent = new Date().toLocaleTimeString();
   rows.innerHTML = state.map(function (a) {
     return '<tr><td><a href="watch/' + encodeURIComponent(a.name) + '">' +
            a.name + '</a><td>' + a.title + '<td>' + a.when + '<td>' + a.pages;
   }).join('');
 };
 events.onerror = function () { document.getElementById('dot').className = 'dot gone'; };
</script>
"""

_WATCH = """<!doctype html><meta charset="utf-8"><title>NAME</title>
<style>STYLE</style>
<header><span class="dot" id="dot"></span><a href="/">← все профили</a>
<b>NAME</b><span id="count">страниц: 0</span><span id="when"></span></header>
<div class="stage">
 <iframe id="a" class="shown" src="/watch/ENCNAME/page"></iframe>
 <iframe id="b"></iframe>
</div>
<script>
 var frames = [document.getElementById('a'), document.getElementById('b')];
 var count = document.getElementById('count');
 var when = document.getElementById('when');
 var dot = document.getElementById('dot');
 var visible = 0;

 // Double buffering: the incoming page loads out of sight and is revealed only
 // once it has rendered, so nothing half-drawn is ever on screen.
 function show(version) {
   var hidden = frames[1 - visible];
   hidden.onload = function () {
     hidden.className = 'shown';
     frames[visible].className = '';
     visible = 1 - visible;
   };
   hidden.src = '/watch/ENCNAME/page?' + version;
 }

 var events = new EventSource('/watch/ENCNAME/events');
 events.onmessage = function (e) {
   show(e.data);
   count.textContent = 'страниц: ' + e.data;
   when.textContent = new Date().toLocaleTimeString();
 };
 events.onerror = function () { dot.className = 'dot gone'; };
</script>
"""


class Channel:
    """One account's stream of pages."""

    def __init__(self, name: str):
        """Start empty, before the account has fetched anything."""
        self.name = name
        self.pages = 0
        self.title = "—"
        self.when = "—"
        self.html = WAITING

    def update(self, html: str, title: str) -> None:
        """Record the newest page for this account."""
        self.pages += 1
        self.html = html
        self.title = title
        self.when = f"{datetime.now():%H:%M:%S}"


class LiveServer:
    """Serves an overview of every account and a live frame for each."""

    _shared: "LiveServer | None" = None
    _shared_lock = threading.Lock()

    @classmethod
    def shared(cls, port: int = 8765) -> "LiveServer":
        """Return the one server every account reports into.

        Accounts are watched together, so they share a server rather than each
        claiming a port of its own. Thirty ports would not be a dashboard.
        """
        with cls._shared_lock:
            if cls._shared is None:
                cls._shared = cls(port)
            return cls._shared

    def __init__(self, port: int = 8765):
        """Start the server on a daemon thread."""
        self.port = port
        self.channels: dict[str, Channel] = {}
        self._subscribers: dict[str | None, list[queue.Queue[str]]] = {}
        self._lock = threading.Lock()
        ThreadingHTTPServer.allow_reuse_address = True
        self._server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("Live view at %s", self.url)

    @property
    def url(self) -> str:
        """Where to point a browser."""
        return f"http://127.0.0.1:{self.port}/"

    def channel(self, name: str) -> Channel:
        """Register an account, or return the one already registered."""
        with self._lock:
            if name not in self.channels:
                self.channels[name] = Channel(name)
            return self.channels[name]

    def publish(self, name: str, html: str, title: str) -> None:
        """Record a page and wake everyone watching."""
        channel = self.channel(name)
        with self._lock:
            channel.update(html, title)
            watching = list(self._subscribers.get(name, []))
            overview = list(self._subscribers.get(None, []))
            summary = self._summary()
            pages = str(channel.pages)
        for listener in watching:
            listener.put(pages)
        for listener in overview:
            listener.put(summary)

    def stop(self) -> None:
        """Shut the server down and release the port.

        Closing matters as much as stopping: without it the socket stays bound
        and the next server on the same port cannot start.
        """
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        with LiveServer._shared_lock:
            if LiveServer._shared is self:
                LiveServer._shared = None

    def _summary(self) -> str:
        """Render the overview state as JSON."""
        return json.dumps(
            [
                {
                    "name": html_module.escape(c.name),
                    "title": html_module.escape(c.title),
                    "when": c.when,
                    "pages": c.pages,
                }
                for c in self.channels.values()
            ],
            ensure_ascii=False,
        )

    def _subscribe(self, name: str | None) -> queue.Queue[str]:
        listener: queue.Queue[str] = queue.Queue()
        with self._lock:
            self._subscribers.setdefault(name, []).append(listener)
        return listener

    def _unsubscribe(self, name: str | None, listener: queue.Queue[str]) -> None:
        with self._lock:
            if listener in self._subscribers.get(name, []):
                self._subscribers[name].remove(listener)

    def _handler(self):
        """Build a request handler bound to this server."""
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                """Silence the default stderr access log."""

            def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
                path = self.path.split("?")[0]
                parts = [unquote(p) for p in path.split("/") if p]

                if not parts:
                    self._send(_OVERVIEW.replace("STYLE", _STYLE))
                elif parts == ["events"]:
                    self._stream(None, server._summary())
                elif parts[0] == "watch" and len(parts) == 2:
                    # Relative URLs here resolved to /watch/page, which this
                    # very route then served as an account called "page" —
                    # the shell nesting inside itself. Absolute only.
                    page = (
                        _WATCH.replace("STYLE", _STYLE)
                        .replace("ENCNAME", quote(parts[1], safe=""))
                        .replace("NAME", html_module.escape(parts[1]))
                    )
                    self._send(page)
                elif parts[0] == "watch" and len(parts) == 3 and parts[2] == "page":
                    channel = server.channels.get(parts[1])
                    self._send(channel.html if channel else WAITING)
                elif parts[0] == "watch" and len(parts) == 3 and parts[2] == "events":
                    channel = server.channels.get(parts[1])
                    self._stream(parts[1], str(channel.pages) if channel else "0")
                else:
                    self._send("<p>Нет такой страницы", status=404)

            def _send(self, body: str, status: int = 200):
                encoded = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(encoded)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(encoded)

            def _stream(self, name: str | None, first: str):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                listener = server._subscribe(name)
                try:
                    self.wfile.write(f"data: {first}\n\n".encode())
                    self.wfile.flush()
                    while True:
                        try:
                            self.wfile.write(f"data: {listener.get(timeout=20)}\n\n".encode())
                        except queue.Empty:
                            # Keep the connection from being reaped while idle.
                            self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    server._unsubscribe(name, listener)

        return Handler
