import os
import tempfile
import unittest

from river import core, server
from river.core import RiverError


class PageMessages(unittest.TestCase):
    """The page's message routes: inbox, thread, item messages, and the send/offer/give/split/decline actions."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        self.c = core.connect()
        core.project_add(self.c, "a")
        for n in ("ag", "bo"):
            core.register(self.c, n)
        core.register(self.c, "mark", human=True)
        self.big = core.item_add(self.c, "a", "big", actor="ag")["id"]
        core.claim(self.c, self.big, "ag")

    def tearDown(self):
        self.c.close()
        os.environ.pop("RIVER_DB", None)
        self.dir.cleanup()

    def op(self, name, who, **args):
        return server.OPS[name](self.c, args, who)

    def get(self, path):
        h = server.Handler.__new__(server.Handler)
        h.path, out = path, {}
        h._send = lambda code, body, ctype=None: out.update(code=code, body=body)
        h.do_GET()
        return out["code"], out["body"]

    def test_offer_split_give_and_inbox(self):
        offer = self.op("offer", "bo", item=self.big, body="I can take the tests")
        code, body = self.get("/api/inbox?agent=ag")
        self.assertEqual((code, [m["id"] for m in body["messages"]]), (200, [offer["id"]]))
        self.assertTrue(body["messages"][0]["unread"])  # the page only looks
        res = self.op("split", "ag", id=self.big, titles=["tests", " ", "docs"])
        self.assertEqual(len(res["split_into"]), 2)
        self.assertEqual(core.message_show(self.c, offer["id"])["state"], "accepted")
        self.op("give", "ag", id=self.big, to="bo")
        self.assertEqual(core._item(self.c, self.big)["assignee"], "bo")

    def test_question_reply_decline_thread_and_item_messages(self):
        q = self.op("send", "bo", kind="question", body="Which port?", to="mark", item=self.big)
        self.op("send", "mark", kind="note", body="8765", reply=q["id"])
        alert = self.op("send", "ag", kind="alert", body="stop", to="bo")
        self.op("decline_message", "bo", msg=alert["id"], note="busy")
        self.assertEqual(core.message_show(self.c, alert["id"])["state"], "declined")
        code, body = self.get(f"/api/thread/{q['id']}")
        self.assertEqual([m["body"] for m in body["messages"]], ["Which port?", "8765"])
        code, body = self.get(f"/api/item/{self.big}")
        self.assertEqual(len(body["messages"]), 2)
        self.assertEqual(self.get("/api/inbox")[0], 400)  # no agent named


class PageGoals(unittest.TestCase):
    """Goals on the page: the state carries goals with progress; the goal ops and item tags work."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        self.c = core.connect()
        core.project_add(self.c, "a")
        core.register(self.c, "ag")

    def tearDown(self):
        self.c.close()
        os.environ.pop("RIVER_DB", None)
        self.dir.cleanup()

    def op(self, _op, who, **args):
        return server.OPS[_op](self.c, args, who)

    def get(self, path):
        h = server.Handler.__new__(server.Handler)
        h.path, out = path, {}
        h._send = lambda code, body, ctype=None: out.update(code=code, body=body)
        h.do_GET()
        return out["code"], out["body"]

    def test_goal_ops_tags_and_state(self):
        self.op("goal_add", None, project="a", name="ship", outcome="it ships", done_when="users can install it")
        self.op("goal_add", None, project="a", name="docs")
        one = self.op("item_add", None, project="a", title="build", goals=["ship"])["id"]
        two = self.op("item_add", None, project="a", title="loose")["id"]
        self.op("item_edit", None, id=two, goals=["docs"])
        self.op("item_edit", None, id=two, untag=["docs"])
        self.op("goal_rank", None, name="docs", rank=1)
        self.op("goal_own", "ag", name="ship")
        code, st = self.get("/api/state")
        goals = {g["name"]: g for g in st["goals"]}
        self.assertEqual([g["name"] for g in st["goals"]], ["docs", "ship"])
        self.assertEqual((goals["ship"]["owner"], goals["ship"]["items_open"]), ("ag", [one]))
        self.assertEqual({i["id"]: i["goals"] for i in st["items"]}, {one: ["ship"], two: []})
        self.op("goal_done", "ag", name="ship", result="shipped", drop_open=True)
        self.op("goal_edit", None, name="docs", outcome="readers find answers", done_when="guide exists")
        code, st = self.get("/api/state")
        goals = {g["name"]: g for g in st["goals"]}
        self.assertEqual((goals["ship"]["status"], goals["ship"]["result"]), ("complete", "shipped"))
        self.assertEqual(goals["docs"]["outcome"], "readers find answers")
        self.op("goal_reopen", None, name="ship")
        self.assertEqual(core.goal_show(self.c, "ship")["status"], "open")



class LaunchAgent(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        self.c = core.connect()

    def tearDown(self):
        self.c.close()
        os.environ.pop("RIVER_DB", None)
        self.dir.cleanup()

    def test_opens_terminal_in_the_folder_of_the_top_ready_item(self):
        folder = os.path.join(self.dir.name, 'my "shop" app')
        core.project_add(self.c, "shop", path=self.dir.name)
        core.project_path(self.c, "shop", folder)
        core.project_add(self.c, "nofolder")
        core.item_add(self.c, "shop", "person step", doer="human")
        x = core.item_add(self.c, "shop", "agent step", priority=1)["id"]
        sent = []
        t = server.launch_agent(self.c, runner=sent.append)
        self.assertEqual((t["project"], t["item"]["id"], t["command"]), ("shop", x, "claude go --remote-control"))
        self.assertIn('tell application "Terminal"', sent[0])
        self.assertIn('keystroke "t" using command down', sent[0])  # a new tab by default
        core.config_set(self.c, "launch_in", "window")
        server.launch_agent(self.c, runner=sent.append)
        self.assertNotIn("keystroke", sent[-1])
        core.config_set(self.c, "launch_in", "tab")
        self.assertIn("claude go", sent[0])
        self.assertIn('my \\"shop\\" app', sent[0])  # quotes escaped inside the AppleScript string
        core.config_set(self.c, "launch_agents", 'Claude Code=claude go; Codex=codex "run river go and follow it"')
        t = server.launch_agent(self.c, runner=sent.append, agent="Codex")
        self.assertEqual((t["agent"], t["command"]), ("Codex", 'codex "run river go and follow it"'))
        self.assertIn('codex \\"run river go and follow it\\"', sent[-1])
        self.assertEqual(server.launch_agent(self.c, runner=sent.append)["agent"], "Claude Code")  # first is default
        self.assertEqual(core.state(self.c)["launch_agents"], ["Claude Code", "Codex"])
        with self.assertRaises(RiverError):
            server.launch_agent(self.c, runner=sent.append, agent="Nope")
        with self.assertRaises(RiverError):
            core.config_set(self.c, "launch_agents", "just a command")
        core.item_add(self.c, "nofolder", "x")
        with self.assertRaises(RiverError):
            server.launch_agent(self.c, "nofolder", runner=sent.append)  # no folder to start in
        with self.assertRaises(RiverError):
            server.launch_agent(self.c, "nosuch", runner=sent.append)

    def test_new_tab_waits_for_terminal_in_front_and_for_the_new_tab(self):
        core.project_add(self.c, "shop", path=self.dir.name)
        core.item_add(self.c, "shop", "agent step")
        sent = []
        server.launch_agent(self.c, runner=sent.append)
        s = sent[0]
        # Command-T goes to the app in front: the script waits for Terminal first, not a fixed delay.
        self.assertLess(s.index('frontmost of process "Terminal"'), s.index('keystroke "t"'))
        self.assertNotIn("delay 0.5", s)
        # The command runs only after the tab count grew; otherwise it falls back to a new window.
        self.assertLess(s.index("set {windowsBefore, tabsBefore}"), s.index('keystroke "t"'))
        # Terminal lists each tab of a tabbed window as its own window, so a new window counts as the new tab.
        self.assertLess(s.index("(count of windows) > windowsBefore or"), s.index("in selected tab of front window"))
        self.assertIn('error "Terminal opened no new tab"', s)
        self.assertEqual(s.count("do script"), 3)  # the tab, the fallback window, and the no-window case


class Watched(unittest.TestCase):
    def test_dev_reload_sees_subfolders_but_not_vendor(self):
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            static = Path(d)
            for f in ("app.js", "components/chip.js", "vendor/lib/big.js", ".hidden/x.js"):
                (static / f).parent.mkdir(parents=True, exist_ok=True)
                (static / f).write_text("x")
            old, server.STATIC = server.STATIC, static
            try:
                names = [str(f.relative_to(static)) for f in server._watched() if f.suffix == ".js"]
                before = server._build_id()
                os.utime(static / "components/chip.js", ns=(1, 2 ** 62))
                self.assertNotEqual(server._build_id(), before)
            finally:
                server.STATIC = old
        self.assertEqual(names, ["app.js", "components/chip.js"])


if __name__ == "__main__":
    unittest.main()


class SetupGuide(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        self.c = core.connect()
        self.folder = os.path.join(self.dir.name, "shop")
        os.mkdir(self.folder)
        core.project_add(self.c, "shop", path=self.folder)

    def tearDown(self):
        self.c.close()
        os.environ.pop("RIVER_DB", None)
        self.dir.cleanup()

    def test_block_fix_writes_both_files_and_status_follows(self):
        before = server.setup_status(self.c)
        self.assertFalse(before["done"])
        self.assertEqual(before["folders"][0]["claude_md"], "missing")
        server.setup_block(self.c, self.folder)
        f = server.setup_status(self.c)["folders"][0]
        self.assertEqual((f["claude_md"], f["agents_md"]), ("current", "current"))

    def test_block_fix_can_move_claude_rules_to_agents_md(self):
        with open(os.path.join(self.folder, "CLAUDE.md"), "w") as f:
            f.write("# Rules\n")
        self.assertEqual(server.setup_status(self.c)["folders"][0]["layout"], "claude_only")
        server.setup_block(self.c, self.folder, move=True)
        f = server.setup_status(self.c)["folders"][0]
        self.assertEqual((f["claude_md"], f["agents_md"], f["layout"]), ("current", "current", "shared"))

    def test_block_fix_refuses_a_folder_that_is_not_a_project(self):
        with self.assertRaises(RiverError):
            server.setup_block(self.c, self.dir.name)
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "CLAUDE.md")))

    def test_add_agent_appends_to_launch_agents_once(self):
        server.setup_agent_add(self.c, "Codex")
        labels = server.setup_agent_add(self.c, "Codex")
        self.assertEqual(labels, ["Claude Code", "Codex"])
        with self.assertRaises(RiverError):
            server.setup_agent_add(self.c, "nope")

    def test_dismiss_is_a_setting(self):
        core.config_set(self.c, "setup_done", "on")
        self.assertTrue(server.setup_status(self.c)["done"])
        with self.assertRaises(RiverError):
            core.config_set(self.c, "setup_done", "maybe")


class StartPushesToWaiting(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        self.c = core.connect()
        core.project_add(self.c, "shop", path=self.dir.name)
        core.project_add(self.c, "other", path=self.dir.name + "/x")
        core.register(self.c, "w")

    def tearDown(self):
        self.c.close()
        os.environ.pop("RIVER_DB", None)
        self.dir.cleanup()

    def test_waiting_session_gets_the_item_and_no_terminal_opens(self):
        core.wait(self.c, self.dir.name, "w", project="other", step="0s", sleep=lambda s: None)
        x = core.item_add(self.c, "shop", "agent step")["id"]
        sent = []
        t = server.launch_agent(self.c, runner=sent.append)  # w waits in another project: a new session
        self.assertNotIn("pushed_to", t)
        self.assertEqual(len(sent), 1)
        core.wait(self.c, self.dir.name, "w", project="shop", step="0s", sleep=lambda s: None)
        t = server.launch_agent(self.c, runner=sent.append)
        self.assertEqual((t["pushed_to"], len(sent)), ("w", 1))
        self.assertEqual(core.item_show(self.c, x)["reserved_for"], "w")


class PageUpdate(unittest.TestCase):
    """The Update button: status of the river clone against its upstream, and a fast-forward that restarts."""

    def setUp(self):
        import subprocess
        self.dir = tempfile.TemporaryDirectory()
        d = self.dir.name
        self.git = lambda repo, *a: subprocess.run(["git", "-C", repo, *a], check=True, capture_output=True, text=True).stdout.strip()
        self.up, self.clone = os.path.join(d, "up"), os.path.join(d, "clone")
        subprocess.run(["git", "init", "-q", "-b", "main", self.up], check=True)
        self.commit(self.up, "one")
        subprocess.run(["git", "clone", "-q", self.up, self.clone], check=True)

    def tearDown(self):
        self.dir.cleanup()

    def commit(self, repo, msg):
        with open(os.path.join(repo, msg), "w") as f:
            f.write(msg)
        self.git(repo, "add", msg)
        self.git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg)

    def test_behind_then_update_fast_forwards_and_restarts(self):
        self.assertEqual(server.update_status(self.clone)["behind"], 0)
        restarts = []
        self.assertFalse(server.update_apply(self.clone, lambda: restarts.append(1))["updated"])
        self.commit(self.up, "two")
        st = server.update_status(self.clone)
        self.assertEqual((st["behind"], len(st["commits"])), (1, 1))
        res = server.update_apply(self.clone, lambda: restarts.append(1))
        self.assertTrue(res["updated"])
        self.assertEqual(res["head"], self.git(self.up, "rev-parse", "--short", "HEAD"))
        self.assertEqual((res["behind"], restarts), (0, [1]))

    def test_refuses_a_clone_with_its_own_commits(self):
        self.commit(self.up, "two")
        self.commit(self.clone, "mine")
        with self.assertRaises(RiverError):
            server.update_apply(self.clone, lambda: self.fail("restarted"))

    def test_local_changes_in_the_way_say_so(self):
        self.commit(self.up, "two")
        with open(os.path.join(self.clone, "two"), "w") as f:
            f.write("mine, not committed")
        with self.assertRaises(RiverError) as e:
            server.update_apply(self.clone, lambda: self.fail("restarted"))
        self.assertIn("commit or stash", str(e.exception))

    def test_stale_when_the_code_changes_after_start(self):
        self.assertFalse(server.code_stale())
        old = server.BOOT_CODE
        try:
            server.BOOT_CODE = "0"
            self.assertTrue(server.code_stale())
        finally:
            server.BOOT_CODE = old

    def test_not_a_git_clone(self):
        self.assertEqual(server.update_status(self.dir.name), {"git": False})
        with self.assertRaises(RiverError):
            server.update_apply(self.dir.name, lambda: None)


class StaticFiles(unittest.TestCase):
    """The page's .js/.css/vendor files come from river/static; nothing outside it is served."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        base = os.path.realpath(self.dir.name)
        self.root = os.path.join(base, "static")
        os.makedirs(os.path.join(self.root, "vendor", "tab"))
        for rel, body in (("app.js", "export const a = 1;"), ("app.css", "b{}"), ("vendor/tab/LICENSE", "MIT"),
                          ("vendor/tab/t.min.js.map", "{}"), (".hidden", "x")):
            with open(os.path.join(self.root, rel), "w") as f:
                f.write(body)
        with open(os.path.join(base, "secret.txt"), "w") as f:
            f.write("no")
        os.symlink(os.path.join(base, "secret.txt"), os.path.join(self.root, "link.txt"))

    def tearDown(self):
        self.dir.cleanup()

    def test_types_and_refusals(self):
        from pathlib import Path
        get = lambda p: server.static_file(p, Path(self.root))
        self.assertEqual(get("/app.js"), (b"export const a = 1;", "text/javascript; charset=utf-8"))
        self.assertEqual(get("/app.css")[1], "text/css; charset=utf-8")
        self.assertEqual(get("/vendor/tab/LICENSE")[1], "text/plain; charset=utf-8")
        self.assertEqual(get("/vendor/tab/t.min.js.map")[1], "application/json")
        for bad in ("/../secret.txt", "/vendor/../../secret.txt", "/%2e%2e/secret.txt", "/vendor%2ftab%2fLICENSE",
                    "/link.txt", "/.hidden", "/vendor", "/", "/nope.js"):
            self.assertIsNone(get(bad), bad)

    def test_handler_serves_the_real_static_folder(self):
        h = server.Handler.__new__(server.Handler)
        out = {}
        h._send = lambda code, body, ctype=None: out.update(code=code, ctype=ctype)
        h.path = "/index.html"
        h.do_GET()
        self.assertEqual((out["code"], out["ctype"]), (200, "text/html; charset=utf-8"))
        h.path = "/../server.py"
        h.do_GET()
        self.assertEqual(out["code"], 404)
