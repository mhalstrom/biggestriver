"""Local web page for Biggest River. Binds to 127.0.0.1 only."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import core
from .core import RiverError

STATIC = Path(__file__).resolve().parent / "static"
PKG = Path(__file__).resolve().parent
DEV = {"on": False}


def _watched():
    return sorted(PKG.glob("*.py")) + sorted(STATIC.glob("*"))


def _build_id():
    """Changes whenever a watched file changes; the page reloads itself in dev mode."""
    return str(max((f.stat().st_mtime_ns for f in _watched()), default=0))

# Operations the page may call. Each maps JSON args to one core function.
OPS = {
    "project_add": lambda c, a, who: core.project_add(c, a["name"], a.get("rank"), a.get("notes", ""), who),
    "project_rank": lambda c, a, who: core.project_rank(c, a["name"], a["rank"], who),
    "project_describe": lambda c, a, who: core.project_describe(c, a["name"], a["text"], who),
    "item_add": lambda c, a, who: core.item_add(c, a["project"], a["title"], int(a.get("priority", 2)),
                                                a.get("notes", ""), a.get("doer", "any"),
                                                [int(x) for x in a.get("after", [])], who,
                                                a.get("context", ""), a.get("touches"), a.get("check", ""),
                                                a.get("blocks"), a.get("mode"), a.get("found_during")),
    "item_edit": lambda c, a, who: core.item_edit(c, a["id"], a.get("title"), a.get("notes"), a.get("doer"),
                                                  a.get("project"), who, a.get("context"), a.get("touches"),
                                                  a.get("check")),
    "prio": lambda c, a, who: core.item_prio(c, a["id"], a["priority"], who),
    "move": lambda c, a, who: core.item_move(c, a["id"], a.get("before"), a.get("after"), who),
    "dep_add": lambda c, a, who: core.dep_add(c, a["id"], [int(x) for x in a["on"]], who, a.get("kind", "blocks")),
    "dep_remove": lambda c, a, who: core.dep_remove(c, a["id"], [int(x) for x in a["on"]], who),
    "claim": lambda c, a, who: core.claim(c, a["id"], who),
    "push": lambda c, a, who: core.push(c, a["id"], a["to"], a.get("note"), who),
    "push_cancel": lambda c, a, who: core.cancel_push(c, a["id"], who),
    "undo_takeover": lambda c, a, who: core.undo_takeover(c, a["id"], who),
    "takeover_seen": lambda c, a, who: core.takeover_seen(c, a["id"], who),
    "accept": lambda c, a, who: core.accept(c, a["id"], who),
    "decline": lambda c, a, who: core.decline(c, a["id"], a.get("note"), who),
    "next_claim": lambda c, a, who: core.next_item(c, a.get("project"), a.get("unblocks"), True, who, 1, a.get("near"),
                                                   bool(a.get("mine"))),
    "done": lambda c, a, who: core.done(c, a["id"], a.get("output"), who),
    "release": lambda c, a, who: core.release(c, a["id"], a.get("note"), who),
    "drop": lambda c, a, who: core.drop(c, a["id"], who),
    "reopen": lambda c, a, who: core.reopen(c, a["id"], who),
    "block": lambda c, a, who: core.block(c, a["id"], a["reason"], who),
    "unblock": lambda c, a, who: core.unblock(c, a["id"], who),
    "register": lambda c, a, who: core.register(c, a["name"], bool(a.get("human")), a.get("note", "")),
    "config_set": lambda c, a, who: core.config_set(c, a["key"], str(a["value"]), a.get("project"), a.get("item"),
                                                    a.get("agent"), who),
    "config_unset": lambda c, a, who: core.config_unset(c, a["key"], a.get("project"), a.get("item"), a.get("agent"), who),
    "answer": lambda c, a, who: core.answer(c, int(a["msg"]), a["body"], who),
    "message_read": lambda c, a, who: _message_read(c, int(a["msg"])),
}


def _message_read(conn, msg_id):
    """Marks one alert or note read, which closes its needs-you event. Questions stay open until answered."""
    core.message_show(conn, msg_id)  # refuses an unknown id
    with core.tx(conn):
        core._mark_read(conn, [msg_id])
    return core.message_show(conn, msg_id)


def needs_you_view(conn, human=None):
    """Open needs-you events with what a person needs to act: the item's context and notes, the message's id."""
    rows = core.needs_you(conn, human or None)
    for r in rows:
        if r["item_id"] is not None:
            it = conn.execute("SELECT context, notes, status FROM items WHERE id=?", (r["item_id"],)).fetchone()
            if it:
                r.update(item_context=it["context"], item_notes=it["notes"], item_status=it["status"])
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            conn = core.connect()
            try:
                core.activity(conn, None)
                st = core.state(conn)
                if DEV["on"]:
                    st["dev_build"] = _build_id()
                return self._send(200, st)
            finally:
                conn.close()
        if path == "/api/log":
            from urllib.parse import parse_qs, urlsplit
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            conn = core.connect()
            try:
                since = q.get("since", "7d")
                return self._send(200, core.completed(conn, q.get("project") or None, None if since == "all" else since))
            except RiverError as e:
                return self._send(400, {"error": str(e)})
            finally:
                conn.close()
        if path == "/api/needs-you":
            from urllib.parse import parse_qs, urlsplit
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            conn = core.connect()
            try:
                return self._send(200, {"events": needs_you_view(conn, q.get("human"))})
            finally:
                conn.close()
        if path.startswith("/api/item/"):
            conn = core.connect()
            try:
                iid = int(path.rsplit("/", 1)[1])
                res = core.item_show(conn, iid)
                res["tree"] = core.blockers(conn, iid)
                return self._send(200, res)
            except (RiverError, ValueError) as e:
                return self._send(404, {"error": str(e)})
            finally:
                conn.close()
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/action":
            return self._send(404, {"error": "not found"})
        # Same-origin check: the page is the only intended caller.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and origin not in (f"http://{host}",):
            return self._send(403, {"error": "cross-origin request refused"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            op = OPS.get(body.get("op"))
            if not op:
                return self._send(400, {"error": f"unknown op {body.get('op')!r}"})
            actor = body.get("actor") or None
            conn = core.connect()
            try:
                core.activity(conn, actor)
                result = op(conn, body.get("args", {}), actor)
                with core.tx(conn):
                    core.sync_needs_you(conn)
                return self._send(200, {"ok": True, "result": result})
            finally:
                conn.close()
        except RiverError as e:
            return self._send(409, {"error": str(e)})
        except (KeyError, ValueError, TypeError) as e:
            return self._send(400, {"error": f"bad request: {e}"})


def _restart_on_change(httpd):
    """Dev mode: when a Python file changes, stop serving and start the process again."""
    code = {f: f.stat().st_mtime_ns for f in PKG.glob("*.py")}
    while True:
        time.sleep(1)
        now = {f: f.stat().st_mtime_ns for f in PKG.glob("*.py")}
        if now != code:
            print("code changed; restarting", flush=True)
            # Replace the process in place. Python sockets are not inherited across exec,
            # so the port is free for the new process. Shutting down first would let the
            # main thread exit before this line runs.
            time.sleep(0.3)  # let an editor finish writing
            os.execv(sys.executable, [sys.executable] + sys.argv)


def serve(port: int, open_browser=False, dev=False):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    DEV["on"] = dev
    print(f"Biggest River on {url} (database {core.db_path()}){' [dev: restarts on code change]' if dev else ''}",
          flush=True)
    if dev:
        threading.Thread(target=_restart_on_change, args=(httpd,), daemon=True).start()
    from . import notify
    notify.SERVE_PORT["port"] = port
    stop = threading.Event()
    threading.Thread(target=notify.loop, args=(stop,), daemon=True, name="river-notify").start()
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
