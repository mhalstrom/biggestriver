import io
import json
import os
import tempfile
import unittest

from river import core, mcp


class Mcp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        os.environ.pop("RIVER_AGENT", None)
        c = core.connect()
        core.project_add(c, "demo", path=self.dir.name)
        core.item_add(c, "demo", "first job", context="start here")
        c.close()

    def tearDown(self):
        os.environ.pop("RIVER_DB", None)
        self.dir.cleanup()

    def talk(self, *msgs):
        out = io.StringIO()
        mcp.serve(io.StringIO("".join(json.dumps(m) + "\n" for m in msgs)), out)
        return [json.loads(x) for x in out.getvalue().splitlines()]

    def test_initialize_list_and_work_loop(self):
        call = lambda i, name, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                      "params": {"name": name, "arguments": args}}
        r = self.talk({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                      {"jsonrpc": "2.0", "method": "notifications/initialized"},
                      {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                      call(3, "go", {"cwd": self.dir.name}),
                      call(4, "done", {"id": 1, "output": "did it"}),
                      call(5, "river", {"args": ["show", "1"]}),
                      call(6, "river", {"args": ["claim", "99"]}),
                      call(7, "river", {"args": ["--nonsense"]}))
        self.assertEqual([x["id"] for x in r], [1, 2, 3, 4, 5, 6, 7])  # no reply to the notification
        self.assertEqual(r[0]["result"]["capabilities"], {"tools": {}})
        self.assertIn("go", [t["name"] for t in r[1]["result"]["tools"]])
        go = r[2]["result"]["content"][0]["text"]
        self.assertIn("YOUR ITEM #1: first job", go)
        self.assertFalse(r[3]["result"]["isError"])  # done ran as the agent go named
        self.assertIn("did it", r[4]["result"]["content"][0]["text"])
        self.assertTrue(r[5]["result"]["isError"])
        self.assertIn("no item 99", r[5]["result"]["content"][0]["text"])
        self.assertTrue(r[6]["result"]["isError"])

    def test_goal_tool(self):
        c = core.connect()
        core.goal_add(c, "demo", "ship", "it ships", "users install it")
        core.register(c, "ag")
        c.close()
        call = lambda i, args: {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                                "params": {"name": "goal", "arguments": dict(args, **{"as": "ag"})}}
        r = self.talk(call(1, {"action": "list"}), call(2, {"action": "own", "name": "ship"}),
                      call(3, {"action": "show", "name": "ship"}), call(4, {"action": "done", "name": "ship", "result": "shipped"}),
                      call(5, {"action": "own"}))
        text = [x["result"]["content"][0]["text"] for x in r]
        self.assertIn("ship", text[0])
        self.assertIn("ag", text[2])
        self.assertFalse(r[3]["result"]["isError"])
        self.assertTrue(r[4]["result"]["isError"])
        c = core.connect()
        self.assertEqual(core.goal_show(c, "ship")["status"], "complete")
        c.close()


def load(path):
    with open(path) as fh:
        return json.load(fh)


class ChatApp(unittest.TestCase):
    """A chat app with no folder (Claude desktop through river mcp): setup, go, plan, and manage."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        os.environ.pop("RIVER_AGENT", None)
        os.environ["RIVER_CHAT"] = "1"
        c = core.connect()
        core.project_add(c, "site", path=os.path.join(self.dir.name, "site"))
        core.project_add(c, "book")
        self.code = core.item_add(c, "site", "fix the header", priority=0, touches="src/header.js")["id"]
        self.test = core.item_add(c, "site", "speed up tests", priority=0, check="make test")["id"]
        self.text = core.item_add(c, "book", "draft chapter one", priority=2)["id"]
        c.close()
        self.old = os.getcwd()
        os.chdir("/")  # the app starts its servers with no project folder

    def tearDown(self):
        os.chdir(self.old)
        for k in ("RIVER_DB", "RIVER_CHAT"):
            os.environ.pop(k, None)
        self.dir.cleanup()

    def call(self, srv, name, args=None):
        text, bad = srv.call(name, args or {})
        self.assertFalse(bad, text)
        return text

    def test_go_takes_only_work_that_needs_no_folder(self):
        srv = mcp.Server()
        r = srv.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertIn("no folder, no shell", r["result"]["instructions"])
        tools = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
        self.assertTrue({"plan", "manage"} <= {t["name"] for t in tools})
        go = self.call(srv, "go")
        self.assertIn(f"YOUR ITEM #{self.text}: draft chapter one", go)
        self.assertRegex(srv.agent, r"^chat-")
        self.assertIn("Skipped in this chat", go)
        self.assertIn(f"#{self.code} fix the header: needs a folder", go)
        self.assertIn("IN A CHAT (no folder, no shell)", go)
        self.assertIn("edit <id> --notes", go)
        self.call(srv, "done", {"id": self.text, "output": "Chapter one: the river at dawn ..."})
        go = self.call(srv, "go")
        self.assertIn("NOTHING FOR THIS CHAT NOW", go)
        self.assertNotIn("river wait", go)
        # The code items stay for coding agents.
        c = core.connect()
        self.assertEqual({core.item_show(c, i)["status"] for i in (self.code, self.test)}, {"open"})
        c.close()

    def test_plan_and_manage_in_a_chat(self):
        srv = mcp.Server()
        plan = self.call(srv, "plan")
        self.assertIn("Focus: every project.", plan)
        self.assertNotIn("THIS FOLDER HAS NO PROJECT", plan)
        self.assertIn("IN A CHAT", plan)
        srv = mcp.Server()
        m = self.call(srv, "manage")
        self.assertRegex(srv.agent, r"^manager-")
        self.assertIn("manage --watch is for a terminal", m)
        self.call(srv, "river", {"args": ["needs-you"]})  # runs as the manager go named

    def test_setup_merges_into_the_claude_desktop_config(self):
        from river import cli
        f = os.path.join(self.dir.name, "Claude", "claude_desktop_config.json")
        os.makedirs(os.path.dirname(f))
        with open(f, "w") as fh:
            json.dump({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}, fh)
        self.assertIn("added river", cli.setup_claude_desktop(f))
        data = load(f)
        self.assertEqual(data["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(data["theme"], "dark")
        river = data["mcpServers"]["river"]
        self.assertEqual(river["args"][-1], "mcp")
        self.assertTrue(os.path.isabs(river["command"]))
        self.assertEqual(river["env"], {"RIVER_DB": str(core.db_path().resolve()), "RIVER_CHAT": "1"})
        self.assertTrue(os.path.exists(f + ".bak"))
        self.assertIn("nothing changed", cli.setup_claude_desktop(f))
        self.assertIn("took river out", cli.setup_claude_desktop(f, remove=True))
        self.assertEqual(load(f)["mcpServers"], {"other": {"command": "x"}})
        with open(f, "w") as fh:
            fh.write("{broken")
        with self.assertRaisesRegex(core.RiverError, "not valid JSON"):
            cli.setup_claude_desktop(f)
        new = os.path.join(self.dir.name, "fresh", "claude_desktop_config.json")
        cli.setup_claude_desktop(new)
        self.assertEqual(list(load(new)["mcpServers"]), ["river"])


if __name__ == "__main__":
    unittest.main()
