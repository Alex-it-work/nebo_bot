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
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote

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
 button{font:inherit;border:0;border-radius:.25rem;padding:.25rem .6rem;
        margin-right:.3rem;cursor:pointer;color:#fff}
 .go{background:#285688}.go:hover{background:#4775a7}
 .stop{background:#8a3a3a}.stop:hover{background:#a94a4a}
 .plain{background:#4775a7}.plain:hover{background:#5b8bc0}
 .settings{display:flex;gap:1rem;align-items:center;flex-wrap:wrap;
           padding:.6rem;background:#043264;border-radius:.4rem}
 .settings label{display:flex;gap:.4rem;align-items:center;white-space:nowrap}
 .settings input[type=text]{width:9rem}
 .settings input[type=checkbox]{width:auto}
 input{font:inherit;width:4rem;padding:.2rem .3rem;border:1px solid #4775a7;
       border-radius:.25rem;background:#043264;color:#ddd}
 input.saving{border-color:#ffdf8c}
 input.bad{border-color:#c44}
 .add input{width:11rem}
 .add{margin-top:1.5rem;padding:.8rem;background:#043264;border-radius:.4rem;
      max-width:60rem;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
 .hint{max-width:60rem;line-height:1.6;color:#bcd}
"""

_OVERVIEW = """<!doctype html><meta charset="utf-8"><title>Боты играют</title>
<style>STYLE</style>
<header><span class="dot" id="dot"></span><span>Профилей: <b id="n">0</b></span>
<span id="when">ожидание…</span>
<button class="go" data-all="maze">Лабиринт всем</button>
<button class="go" data-all="collect">Забрать всё</button>
<button class="stop" data-all="stop">Остановить всех</button></header>
<main>
<table><thead><tr>
<th title="Имя в игре. Нажмите, чтобы смотреть игру этого профиля">Профиль
<th title="Чем профиль занят прямо сейчас, либо чем закончил">Что делает
<th title="Страница игры, открытая последней">Где сейчас
<th title="Когда бот в последний раз открывал страницу">Последний ход
<th title="Сколько лабиринтов пройти за один запуск">Кругов
<th>Действия
</tr></thead><tbody id="rows"></tbody></table>

<p class="hint">
 <b>Лабиринт</b> — пройти лабиринт столько раз, сколько указано в «Кругов».
 Один круг стоит около 130 ключей.<br>
 <b>Награды</b> — забрать созревшие награды с личных заданий и марафона.<br>
 <b>Стоп</b> — закончить после текущей попытки. Начатый лабиринт не бросается,
 иначе потраченные на него ключи пропадут.
</p>

<form id="add" class="add">
 <b>Добавить профиль:</b>
 <input name="username" placeholder="имя в игре" autocomplete="off">
 <input name="password" placeholder="пароль" type="password" autocomplete="off">
 <button class="go" type="submit">Добавить</button>
 <span id="added"></span>
</form>
</main>
<script>
 var rows = document.getElementById('rows');

 function post(path, body) {
   return fetch(path, {method: 'POST', body: body});
 }

 // One listener for the whole page rather than inline handlers. Building
 // handler attributes by string concatenation nests three levels of quotes,
 // and getting one wrong is a syntax error that kills the whole script — which
 // is exactly how the table came up empty while the server had six accounts.
 document.addEventListener('click', function (event) {
   var button = event.target.closest('button');
   // Forms handle their own submit buttons; everything else is ours.
   if (!button || button.closest('form')) return;

   var everyone = button.dataset.all;
   if (everyone === 'stop') return void post('stop-all');
   if (everyone) {
     document.querySelectorAll('tr[data-name]').forEach(function (row) {
       post('run/' + encodeURIComponent(row.dataset.name) + '/' + everyone);
     });
     return;
   }

   var row = button.closest('tr');
   if (!row || !button.dataset.do) return;
   var plain = row.dataset.name;
   var name = encodeURIComponent(plain);
   var what = button.dataset.do;

   if (what === 'stop') return void post('stop/' + name);
   if (what === 'settings') {
     open_settings = (open_settings === plain) ? null : plain;
     return void draw(last_state);
   }
   if (what === 'remove') {
     if (!confirm('Удалить профиль ' + plain + '? Настройки для него сотрутся.')) return;
     return void post('remove/' + name).then(function (r) { return r.text(); })
       .then(function (text) { if (text !== 'удалён') alert(text); });
   }
   // No round count rides along: it is saved, and the run re-reads it before
   // every attempt, so changing it mid-run changes the run.
   post('run/' + name + '/' + what);
 });

 document.addEventListener('input', function (event) {
   var field = event.target;
   if (field.type !== 'number') return;
   var row = field.closest('tr[data-name]');
   if (row) chosen_rounds[row.dataset.name] = field.value;
 });

 // Scrolling the page with the pointer over a number field makes the browser
 // change its value, silently and without anyone meaning to. Now that the
 // field is saved, that turned a scroll past the table into a rewritten
 // setting: nine rounds quietly became twelve.
 document.addEventListener('wheel', function (event) {
   if (event.target.type === 'number') event.preventDefault();
 }, {passive: false});

 // Saved as soon as the field is left, so it survives a reload, a trip to a
 // profile page, and a restart. Keeping it in the browser only was why the
 // number kept coming back as 1.
 document.addEventListener('change', function (event) {
   var field = event.target;
   if (field.type !== 'number') return;
   var row = field.closest('tr[data-name]');
   if (!row) return;
   var name = row.dataset.name;
   // Nothing to save when it already says that; a write nobody asked for is
   // how a setting drifts without anyone seeing it happen.
   if (field.value === String(saved_rounds[name])) {
     delete chosen_rounds[name];
     return;
   }
   field.classList.add('saving');
   post('rounds/' + encodeURIComponent(name) + '?rounds=' + field.value)
     .then(function (response) { return response.text(); })
     .then(function (text) {
       field.classList.remove('saving');
       if (text === 'сохранено') {
         saved_rounds[name] = field.value;
         delete chosen_rounds[name];
         return;
       }
       field.classList.add('bad');
       alert(text);
     });
 });

 document.getElementById('add').addEventListener('submit', function (event) {
   event.preventDefault();
   var note = document.getElementById('added');
   note.textContent = 'добавляю…';
   post('add', new FormData(event.target)).then(function (response) {
     return response.text();
   }).then(function (text) {
     note.textContent = text;
     if (text === 'добавлен') event.target.reset();
   });
 });

 var open_settings = null;
 var last_state = [];
 // What is being typed right now, before it has been saved. Cleared once the
 // server confirms, after which the saved value is the one shown.
 var chosen_rounds = {};
 // What the file says, so a "change" that changes nothing is not written back.
 var saved_rounds = {};
 var row_by_name = {};

 function field(form, key, label, value, type) {
   var wrap = document.createElement('label');
   wrap.textContent = label + ' ';
   var input = document.createElement('input');
   input.name = key;
   input.type = type;
   if (type === 'checkbox') input.checked = !!value; else input.value = value;
   wrap.appendChild(input);
   form.appendChild(wrap);
 }

 function settingsRow(account) {
   var row = document.createElement('tr');
   row.className = 'settings-row';
   row.dataset.forName = account.name;
   var cellHolder = document.createElement('td');
   cellHolder.colSpan = 6;

   var form = document.createElement('form');
   form.className = 'settings';
   var settings = account.settings || {};
   field(form, 'maze_rounds', 'Кругов за запуск', settings.maze_rounds, 'number');
   field(form, 'min_keys', 'Не тратить ключи ниже', settings.min_keys, 'number');
   field(form, 'active_hours', 'Часы работы (09:00-23:30)', settings.active_hours || '', 'text');
   field(form, 'spend_baksy', 'Тратить баксы на ускорения', settings.spend_baksy, 'checkbox');
   field(form, 'fast', 'Быстрый темп', settings.fast, 'checkbox');

   var save = document.createElement('button');
   save.className = 'go';
   save.type = 'submit';
   save.textContent = 'Сохранить';
   form.appendChild(save);

   var note = document.createElement('span');
   form.appendChild(note);

   form.addEventListener('submit', function (event) {
     event.preventDefault();
     note.textContent = 'сохраняю…';
     var data = new FormData();
     form.querySelectorAll('input').forEach(function (input) {
       data.append(input.name, input.type === 'checkbox'
         ? (input.checked ? 'on' : 'off') : input.value);
     });
     post('settings/' + encodeURIComponent(account.name), data)
       .then(function (r) { return r.text(); })
       .then(function (text) { note.textContent = text; });
   });

   cellHolder.appendChild(form);
   row.appendChild(cellHolder);
   return row;
 }

 function cell(text) {
   var td = document.createElement('td');
   td.textContent = text;
   return td;
 }

 function button(label, kind, action) {
   var element = document.createElement('button');
   // A created button defaults to type="submit", which the click handler
   // deliberately ignores; without this every row button was inert.
   element.type = 'button';
   element.className = kind;
   element.dataset.do = action;
   element.textContent = label;
   return element;
 }

 function buildRow(account) {
   var row = document.createElement('tr');
   row.dataset.name = account.name;

   var first = document.createElement('td');
   var link = document.createElement('a');
   link.href = 'watch/' + encodeURIComponent(account.name);
   link.textContent = account.name;
   first.appendChild(link);
   row.appendChild(first);

   row.appendChild(cell(''));
   row.appendChild(cell(''));
   row.appendChild(cell(''));

   var roundsCell = document.createElement('td');
   var rounds = document.createElement('input');
   rounds.type = 'number';
   rounds.min = '1';
   rounds.value = account.rounds;
   rounds.title = 'Сколько лабиринтов пройти за один запуск';
   roundsCell.appendChild(rounds);
   row.appendChild(roundsCell);

   var actions = document.createElement('td');
   actions.appendChild(button('Лабиринт', 'go', 'maze'));
   actions.appendChild(button('Награды', 'go', 'collect'));
   actions.appendChild(button('Стоп', 'stop', 'stop'));
   actions.appendChild(button('Настройки', 'plain', 'settings'));
   actions.appendChild(button('Удалить', 'stop', 'remove'));
   row.appendChild(actions);
   return row;
 }

 // Rows are updated in place, never rebuilt. Replacing them wholesale threw
 // away whatever was being typed several times a second, which made the round
 // count impossible to set while anything was running.
 function draw(state) {
   last_state = state;
   document.getElementById('n').textContent = state.length;
   document.getElementById('when').textContent = new Date().toLocaleTimeString();

   var seen = {};
   state.forEach(function (account) {
     seen[account.name] = true;
     var row = row_by_name[account.name];
     if (!row || !row.isConnected) {
       row = buildRow(account);
       row_by_name[account.name] = row;
       rows.appendChild(row);
     }

     var cells = row.children;
     setText(cells[1], account.busy || '—');
     setText(cells[2], account.title);
     setText(cells[3], account.when);

     var rounds = cells[4].querySelector('input');
     saved_rounds[account.name] = account.rounds;
     // Only touch the field when nobody is typing in it.
     if (document.activeElement !== rounds && chosen_rounds[account.name] === undefined) {
       rounds.value = account.rounds;
     }

     var settings = row.nextElementSibling;
     var isSettings = settings && settings.classList.contains('settings-row') &&
                      settings.dataset.forName === account.name;
     if (open_settings === account.name && !isSettings) {
       row.after(settingsRow(account));
     } else if (open_settings !== account.name && isSettings) {
       settings.remove();
     }
   });

   rows.querySelectorAll('tr[data-name]').forEach(function (row) {
     if (!seen[row.dataset.name]) {
       var settings = row.nextElementSibling;
       if (settings && settings.classList.contains('settings-row')) settings.remove();
       delete row_by_name[row.dataset.name];
       row.remove();
     }
   });
 }

 function setText(cell, text) {
   if (cell.textContent !== text) cell.textContent = text;
 }

 var events = new EventSource('events');
 events.onmessage = function (e) { draw(JSON.parse(e.data)); };
 events.onerror = function () { document.getElementById('dot').className = 'dot gone'; };
</script>
"""

_WATCH = """<!doctype html><meta charset="utf-8"><title>NAME</title>
<style>STYLE</style>
<header><span class="dot" id="dot"></span><a href="/">← все профили</a>
<b>NAME</b><span id="count">страниц: 0</span><span id="when"></span>
<button class="go" id="open" title="Открыть этот профиль в обычной вкладке, чтобы играть самому">Войти в игру</button></header>
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

 // Hands this profile to a real browser tab. The window is opened on the
 // click itself — opening it later, once the answer arrives, is what a popup
 // blocker stops.
 var opener = document.getElementById('open');
 opener.addEventListener('click', function () {
   var tab = window.open('', '_blank');
   var label = opener.textContent;
   opener.disabled = true;
   opener.textContent = 'вхожу…';
   fetch('/open/ENCNAME', {method: 'POST'}).then(function (response) {
     return response.text();
   }).then(function (text) {
     opener.disabled = false;
     opener.textContent = label;
     if (text.indexOf('http') === 0) { tab.location = text; return; }
     if (tab) tab.close();
     alert(text);
   });
 });
</script>
"""


def _form_fields(body: str) -> dict[str, str]:
    """Pull the fields out of a multipart form body.

    FormData posts multipart, and pulling two text fields out of it by hand
    is less trouble than dragging in a parser for the purpose.
    """
    fields: dict[str, str] = {}
    newline = chr(13) + chr(10)
    for part in body.split("--"):
        name = re.search(r'name="([^"]+)"', part)
        if name is None:
            continue
        halves = part.split(newline + newline, 1)
        if len(halves) == 2:
            fields[name.group(1)] = halves[1].strip(newline)
    return fields


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

    def attach(self, controller) -> None:
        """Give the dashboard something to drive."""
        self.controller = controller
        # Progress moves without a new page arriving — an attempt that ends in
        # a dead end changes the count but not the screen — so it needs its
        # own nudge or the table would sit still between pages.
        controller.on_progress = self.refresh
        for name in controller.names():
            self.channel(name)

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

    def __init__(self, port: int = 8765, controller=None):
        """Start the server on a daemon thread.

        Args:
            port: Port on localhost to listen on.
            controller: Optional object able to start and stop per-account
                jobs, which is what turns the dashboard from a window into
                a set of controls.
        """
        self.port = port
        self.controller = controller
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

    def refresh(self) -> None:
        """Push the current state to the overview without a new page."""
        with self._lock:
            listeners = list(self._subscribers.get(None, []))
            summary = self._summary()
        for listener in listeners:
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
                    "busy": html_module.escape(
                        self.controller.status(c.name) if self.controller else ""
                    ),
                    "rounds": self.controller.rounds_for(c.name) if self.controller else 1,
                    "settings": self.controller.settings_for(c.name) if self.controller else {},
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

            def do_POST(self):  # noqa: N802 - name fixed by BaseHTTPRequestHandler
                path, _, query = self.path.partition("?")
                parts = [unquote(p) for p in path.split("/") if p]

                # Every POST is somebody pressing something. Silence here left
                # no way to tell who kept changing a saved round count.
                logger.info("Panel: %s", self.path)

                if server.controller is None:
                    self._send("<p>Управление недоступно", status=503)
                elif parts == ["add"]:
                    self._add()
                elif len(parts) == 2 and parts[0] == "remove":
                    problem = server.controller.remove_account(parts[1])
                    self._send(problem or "удалён", status=400 if problem else 200)
                elif len(parts) == 2 and parts[0] == "settings":
                    self._settings(parts[1])
                elif len(parts) == 2 and parts[0] == "rounds":
                    self._rounds(parts[1], parse_qs(query).get("rounds", [""])[0])
                elif len(parts) == 2 and parts[0] == "open":
                    self._open_game(parts[1])
                elif parts == ["stop-all"]:
                    stopped = server.controller.stop_all()
                    self._send(f"<p>Остановлено: {stopped}")
                elif len(parts) == 2 and parts[0] == "stop":
                    ok = server.controller.stop(parts[1])
                    self._send("<p>Останавливаю" if ok else "<p>Нечего останавливать")
                elif len(parts) == 3 and parts[0] == "run":
                    wanted = parse_qs(query).get("rounds", [""])[0]
                    rounds = int(wanted) if wanted.isdigit() and int(wanted) > 0 else None
                    ok = server.controller.start(parts[1], parts[2], rounds)
                    self._send("<p>Запущено" if ok else "<p>Занят или неизвестен",
                               status=200 if ok else 409)
                else:
                    self._send("<p>Нет такой команды", status=404)

                # A control action changes what the table should say.
                server.refresh()

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

            def _add(self):
                """Take a new account from the panel's form."""
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8", "replace")
                fields = _form_fields(body)
                problem = server.controller.add_account(
                    fields.get("username", ""), fields.get("password", "")
                )
                if problem:
                    self._send(problem, status=400)
                    return
                server.channel(fields["username"].strip())
                self._send("добавлен")

            def _rounds(self, account: str, wanted: str):
                """Save how many mazes this account plays.

                The number used to live only in the browser, so opening a
                profile and coming back showed the saved 1 again. It is a
                setting like any other and belongs in the file.
                """
                if not wanted.isdigit() or int(wanted) < 1:
                    self._send("нужно целое число от 1", status=400)
                    return
                problem = server.controller.update_account(account, {"maze_rounds": wanted})
                self._send(problem or "сохранено", status=400 if problem else 200)

            def _open_game(self, account: str):
                """Hand this profile to an ordinary browser tab.

                Signing in can take a while at a human pace, so this answers
                only once there is a link to answer with.
                """
                result = server.controller.game_url(account)
                ok = result.startswith("http")
                logger.info(
                    "Panel: handing %s to a browser: %s",
                    account,
                    "ok" if ok else result,
                )
                self._send(result, status=200 if ok else 400)

            def _settings(self, account: str):
                """Save the settings form for one account."""
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode("utf-8", "replace")
                problem = server.controller.update_account(account, _form_fields(body))
                self._send(problem or "сохранено", status=400 if problem else 200)

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
