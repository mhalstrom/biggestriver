"""An MCP server (stdio) for agents that cannot run shell commands.

Run it with `river mcp`. Every tool runs the same code as the `river` command
and returns the same text, so an agent reads the same briefings either way.
The server remembers the agent name that `go` or `plan` gives, and passes it
as --as on later calls that do not name one. Standard library only.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import sys

from . import __version__, cli
from .core import RiverError

PROTOCOL = "2025-06-18"

_AS = {"type": "string", "description": "your river agent name (default: the one go or plan gave you)"}
TOOLS = [
    {"name": "river",
     "description": "Run any river command and get its text output, for example [\"go\"], "
                    "[\"done\", \"12\", \"--output\", \"what changed, commit id\"], or [\"guide\"].",
     "inputSchema": {"type": "object", "required": ["args"], "properties": {
         "args": {"type": "array", "items": {"type": "string"}, "description": "the words after `river`"},
         "as": _AS,
         "cwd": {"type": "string", "description": "run in this folder (the project folder, for go and add)"}}}},
    {"name": "go",
     "description": "Start or continue work: river names you, picks a role, claims an item, and prints a briefing "
                    "that ends with what to run next.",
     "inputSchema": {"type": "object", "properties": {
         "as": _AS, "project": {"type": "string"}, "cwd": {"type": "string", "description": "the project folder"}}}},
    {"name": "done",
     "description": "Finish an item you hold. Put what changed and the commit id in output.",
     "inputSchema": {"type": "object", "required": ["id", "output"], "properties": {
         "id": {"type": "integer"}, "output": {"type": "string"}, "as": _AS}}},
    {"name": "show",
     "description": "One item with its notes, links, and history.",
     "inputSchema": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}, "as": _AS}}},
    {"name": "goal",
     "description": "Goals: outcomes in a project that one agent owns. list (open goals with owner and progress), "
                    "show (a goal and its items), own (you plan and take the items that reach it), release, "
                    "or done (declare it complete with a one-line result; refused while its items are open).",
     "inputSchema": {"type": "object", "required": ["action"], "properties": {
         "action": {"type": "string", "enum": ["list", "show", "own", "release", "done"]},
         "name": {"type": "string", "description": "the goal (all actions except list)"},
         "project": {"type": "string", "description": "list: only this project's goals"},
         "result": {"type": "string", "description": "done: what the goal achieved, one line"},
         "as": _AS}}},
    {"name": "inbox",
     "description": "Your unread messages and the questions that wait for your answer.",
     "inputSchema": {"type": "object", "properties": {"as": _AS}}},
]


class Server:
    def __init__(self):
        self.agent = os.environ.get("RIVER_AGENT")

    def argv(self, name, a):
        if name == "river":
            words = [str(x) for x in a.get("args") or []]
        elif name == "go":
            words = ["go"] + (["--project", a["project"]] if a.get("project") else [])
        elif name == "done":
            words = ["done", str(a["id"]), "--output", a["output"]]
        elif name == "show":
            words = ["show", str(a["id"])]
        elif name == "goal":
            act = a["action"]
            if act not in ("list", "show", "own", "release", "done"):
                raise RiverError("goal action is one of list, show, own, release, done")
            words = ["goal", act]
            if act == "list":
                words += ["--project", a["project"]] if a.get("project") else []
            else:
                if not a.get("name"):
                    raise RiverError(f"goal {act} needs the goal name")
                words.append(a["name"])
            if act == "done":
                words += ["--result", a.get("result") or ""]
        elif name == "inbox":
            words = ["inbox"]
        else:
            raise RiverError(f"unknown tool {name!r}")
        who = a.get("as") or self.agent
        if who and "--as" not in words:
            words = ["--as", who] + words
        return words

    def call(self, name, a):
        """Run one river command in-process; return (text, is_error)."""
        words = self.argv(name, a)
        out, err = io.StringIO(), io.StringIO()
        old = os.getcwd()
        code = 0
        try:
            if a.get("cwd"):
                os.chdir(os.path.expanduser(a["cwd"]))
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                try:
                    code = cli.run(words) or 0
                except RiverError as e:
                    print(f"river: {e}", file=sys.stderr)
                    code = 2
                except SystemExit as e:  # argparse errors and --help
                    code = e.code if isinstance(e.code, int) else 2
        except OSError as e:
            err.write(f"river: {e}\n")
            code = 2
        finally:
            os.chdir(old)
        text = out.getvalue() + err.getvalue()
        m = re.search(r"^You are river agent (\S+)\.", text, re.M)
        if m:
            self.agent = m.group(1)
        return text.strip() or "(no output)", code != 0

    def handle(self, msg):
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            return None  # a notification, such as notifications/initialized
        if method == "initialize":
            result = {"protocolVersion": msg.get("params", {}).get("protocolVersion") or PROTOCOL,
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "biggest-river", "version": __version__},
                      "instructions": "Call go to take work from the Biggest River queue, and follow its briefing."}
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            p = msg.get("params") or {}
            try:
                text, bad = self.call(p.get("name"), p.get("arguments") or {})
            except (RiverError, KeyError) as e:
                text, bad = f"river: {e}", True
            result = {"content": [{"type": "text", "text": text}], "isError": bad}
        elif method == "ping":
            result = {}
        else:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def serve(stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    srv = Server()
    for line in stdin:
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            reply = srv.handle(msg)
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
