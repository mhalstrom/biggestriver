"""Local web page for Biggest River. Binds to 127.0.0.1 only."""

from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import core
from .core import RiverError

STATIC = Path(__file__).resolve().parent / "static"

# Operations the page may call. Each maps JSON args to one core function.
OPS = {
    "project_add": lambda c, a, who: core.project_add(c, a["name"], a.get("rank"), a.get("notes", ""), who),
    "project_rank": lambda c, a, who: core.project_rank(c, a["name"], a["rank"], who),
    "project_describe": lambda c, a, who: core.project_describe(c, a["name"], a["text"], who),
    "item_add": lambda c, a, who: core.item_add(c, a["project"], a["title"], int(a.get("priority", 2)),
                                                a.get("notes", ""), a.get("doer", "any"),
                                                [int(x) for x in a.get("after", [])], who),
    "item_edit": lambda c, a, who: core.item_edit(c, a["id"], a.get("title"), a.get("notes"), a.get("doer"),
                                                  a.get("project"), who),
    "prio": lambda c, a, who: core.item_prio(c, a["id"], a["priority"], who),
    "move": lambda c, a, who: core.item_move(c, a["id"], a.get("before"), a.get("after"), who),
    "dep_add": lambda c, a, who: core.dep_add(c, a["id"], [int(x) for x in a["on"]], who),
    "dep_remove": lambda c, a, who: core.dep_remove(c, a["id"], [int(x) for x in a["on"]], who),
    "claim": lambda c, a, who: core.claim(c, a["id"], who),
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
}


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
                return self._send(200, core.state(conn))
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
                return self._send(200, {"ok": True, "result": op(conn, body.get("args", {}), actor)})
            finally:
                conn.close()
        except RiverError as e:
            return self._send(409, {"error": str(e)})
        except (KeyError, ValueError, TypeError) as e:
            return self._send(400, {"error": f"bad request: {e}"})


def serve(port: int, open_browser=False):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    print(f"Biggest River on {url} (database {core.db_path()})", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
