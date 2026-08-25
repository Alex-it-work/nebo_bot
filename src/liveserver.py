"""A small local server that pushes each new page to the browser.

Reloading on a timer is guesswork: the bot knows exactly when a page arrives,
so the browser should be told rather than left polling. This serves a shell
page holding an iframe and an event stream; when a page is recorded the shell
is notified and swaps the frame. Nothing reloads on a timer, the outer page
never flickers, and an idle bot produces no traffic at all.

Everything runs on a daemon thread bound to localhost, so it never delays the
bot and never listens beyond this machine.
"""

from __future__ import annotations

import logging
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)

SHELL = """<!doctype html><meta charset="utf-8"><title>Бот играет</title>
<style>
 html,body{margin:0;height:100%;background:#111;color:#ddd;font:13px system-ui}
 header{display:flex;gap:1rem;align-items:center;padding:.4rem .8rem;background:#1b1b1b}
 #dot{width:.6rem;height:.6rem;border-radius:50%;background:#4c4;transition:background .3s}
 .stage{position:relative;height:calc(100% - 2rem)}
 /* Both frames sit on a dark ground: a frame paints its own background before
    the page inside it loads, and white there is what made it flash. */
 iframe{position:absolute;inset:0;border:0;width:100%;height:100%;
        background:#111;opacity:0;transition:opacity .12s}
 iframe.shown{opacity:1}
</style>
<header><span id="dot"></span><span id="count">страниц: 0</span>
<span id="when">ожидание…</span></header>
<div class="stage">
 <iframe id="a" class="shown" src="page.html"></iframe>
 <iframe id="b"></iframe>
</div>
<script>
 const frames = [document.getElementById('a'), document.getElementById('b')];
 const count = document.getElementById('count');
 const when = document.getElementById('when');
 const dot = document.getElementById('dot');
 let visible = 0;

 // Double buffering: the incoming page loads out of sight and is revealed only
 // once it has rendered, so nothing half-drawn is ever on screen.
 function show(version) {
   const hidden = frames[1 - visible];
   hidden.onload = () => {
     hidden.classList.add('shown');
     frames[visible].classList.remove('shown');
     visible = 1 - visible;
   };
   hidden.src = 'page.html?' + version;
 }

 const events = new EventSource('events');
 events.onmessage = (e) => {
   show(e.data);
   count.textContent = 'страниц: ' + e.data;
   when.textContent = new Date().toLocaleTimeString();
   dot.style.background = '#4c4';
 };
 events.onerror = () => { dot.style.background = '#c44'; };
</script>
"""


class LiveServer:
    """Serves the current page and pushes an event whenever it changes."""

    def __init__(self, directory: str | Path, port: int = 8765):
        """Start the server on a daemon thread.

        Args:
            directory: Where the recorder writes ``live.html``.
            port: Port on localhost to listen on.
        """
        self.directory = Path(directory)
        self.port = port
        self.pages = 0
        self._subscribers: list[queue.Queue[int]] = []
        self._lock = threading.Lock()
        ThreadingHTTPServer.allow_reuse_address = True
        self._server = ThreadingHTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        logger.info("Live view at http://127.0.0.1:%d/", port)

    @property
    def url(self) -> str:
        """Where to point a browser."""
        return f"http://127.0.0.1:{self.port}/"

    def notify(self) -> None:
        """Tell every connected browser that a new page is ready."""
        with self._lock:
            self.pages += 1
            current = self.pages
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            subscriber.put(current)

    def stop(self) -> None:
        """Shut the server down and release the port.

        Closing matters as much as stopping: without it the socket stays bound
        and the next server on the same port cannot start.
        """
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _subscribe(self) -> queue.Queue[int]:
        listener: queue.Queue[int] = queue.Queue()
        with self._lock:
            self._subscribers.append(listener)
        return listener

    def _unsubscribe(self, listener: queue.Queue[int]) -> None:
        with self._lock:
            if listener in self._subscribers:
                self._subscribers.remove(listener)

    def _handler(self):
        """Build a request handler bound to this server."""
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                """Silence the default stderr access log."""

            def do_GET(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
                if self.path.startswith("/events"):
                    self._stream()
                elif self.path.startswith("/page.html"):
                    self._send_page()
                else:
                    self._send(200, "text/html; charset=utf-8", SHELL.encode("utf-8"))

            def _send(self, status: int, content_type: str, body: bytes):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def _send_page(self):
                page = server.directory / "live.html"
                if not page.is_file():
                    waiting = "<p>Ожидание первой страницы…"
                    self._send(200, "text/html; charset=utf-8", waiting.encode("utf-8"))
                    return
                self._send(200, "text/html; charset=utf-8", page.read_bytes())

            def _stream(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                listener = server._subscribe()
                try:
                    self.wfile.write(f"data: {server.pages}\n\n".encode())
                    self.wfile.flush()
                    while True:
                        try:
                            number = listener.get(timeout=20)
                            self.wfile.write(f"data: {number}\n\n".encode())
                        except queue.Empty:
                            # Keep the connection from being reaped while idle.
                            self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    server._unsubscribe(listener)

        return Handler
