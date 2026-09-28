import os
import re
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
        self.platform, server.PLATFORM = server.PLATFORM, "darwin"  # the Terminal tests; Windows has its own
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        self.c = core.connect()

    def tearDown(self):
        server.PLATFORM = self.platform
        self.c.close()
        os.environ.pop("RIVER_DB", None)
        self.dir.cleanup()

    def test_a_page_on_a_riverdb_queue_starts_agents_on_it(self):
        core.project_add(self.c, "shop", path=self.dir.name)
        core.item_add(self.c, "shop", "first")
        sent, old = [], os.environ.get("RIVER_DB")
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "q.db")
        try:
            server.launch_agent(self.c, runner=sent.append)
        finally:
            if old is None:
                del os.environ["RIVER_DB"]
            else:
                os.environ["RIVER_DB"] = old
        self.assertRegex(sent[0], r"RIVER_DB=\S*q\.db'? claude go")  # the path is quoted when it needs it

    def test_dispatch_starts_a_named_session_for_one_item(self):
        core.project_add(self.c, "shop", path=self.dir.name)
        top = core.item_add(self.c, "shop", "first", priority=0)["id"]
        x = core.item_add(self.c, "shop", "second", priority=3)["id"]
        sent = []
        t = server.dispatch_item(self.c, x, runner=sent.append)
        name = t["session_name"]
        self.assertEqual((t["item"]["id"], core._item(self.c, x)["reserved_for"]), (x, name))
        self.assertIn(f"RIVER_AGENT={name} claude go", sent[0])
        b = core.go(self.c, self.dir.name, name)  # the new session's first go takes that item, not the top one
        self.assertEqual(b["item"]["id"], x)
        with self.assertRaises(RiverError):
            server.dispatch_item(self.c, x, runner=sent.append)  # held now
        core.register(self.c, "idle")
        with core.tx(self.c):
            self.c.execute("UPDATE agents SET role='waiting', waiting_in='shop', waiting_since=? WHERE name='idle'",
                           (core.iso(core.now()),))
        r = server.dispatch_item(self.c, top, runner=sent.append)
        self.assertEqual((r["pushed_to"], len(sent)), ("idle", 1))  # a waiting session gets it; no new Terminal

    def test_open_agent_on_a_person_item_or_a_waiting_item(self):
        from river import cli
        import contextlib, io
        core.project_add(self.c, "shop", path=self.dir.name)
        h = core.item_add(self.c, "shop", "sign the contract", doer="human")["id"]
        blocker = core.item_add(self.c, "shop", "draft the contract")["id"]
        waits = core.item_add(self.c, "shop", "ship it", after=[blocker])["id"]
        sent = []
        core.register(self.c, "mark", human=True)
        t = server.open_agent_on(self.c, h, runner=sent.append, person="mark")
        self.assertIn(f"RIVER_FOCUS=help:{h}@mark claude go", sent[-1])
        b = core.go(self.c, self.dir.name, None, focus=t["focus"])
        self.assertEqual((b["role"], b["item"]), ("helper", None))
        self.assertEqual(b["help_prompt"], core.prompt_for(self.c, h, "mark"))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.render_go(b)
        self.assertIn(f"Item #{h}: sign the contract", out.getvalue())
        t = server.open_needs_you(self.c, runner=sent.append, person="mark")
        self.assertIn("RIVER_FOCUS=needs:@mark claude go", sent[-1])
        b = core.go(self.c, self.dir.name, None, focus=t["focus"])
        self.assertEqual(b["help_prompt"], core.prompt_for_all(self.c, "mark"))
        t = server.open_agent_on(self.c, waits, runner=sent.append)
        self.assertIn(f"RIVER_FOCUS=unblock:{waits}", sent[-1])
        b = core.go(self.c, self.dir.name, None, focus=t["focus"])
        self.assertEqual((b["role"], b["item"]["id"]), ("unblocker", blocker))
        with self.assertRaises(RiverError):
            server.open_agent_on(self.c, blocker, runner=sent.append)  # held now: message the holder

    def test_deploy_now_without_an_owner_opens_a_deployer(self):
        core.target_add(self.c, "web")
        core.project_add(self.c, "site", target="web", path=self.dir.name)
        core.register(self.c, "dev")
        x = core.item_add(self.c, "site", "page")["id"]
        core.claim(self.c, x, "dev")
        core.done(self.c, x, "commit", "dev", ship_it=True)
        sent = []
        r = server.deploy_now(self.c, "web", runner=sent.append)
        self.assertIn("RIVER_FOCUS=deploy:web claude go", sent[-1])
        b = core.go(self.c, self.dir.name, None, focus=r["focus"])
        self.assertEqual((b["role"], b["item"]["id"]), ("deployer", r["deploy"]["id"]))

    def test_claim_next_by_a_person_opens_an_agent_that_helps(self):
        core.project_add(self.c, "shop", path=self.dir.name)
        core.register(self.c, "mark", human=True)
        x = core.item_add(self.c, "shop", "write the copy")["id"]  # anyone can do it
        core.claim(self.c, x, "mark")
        sent = []
        t = server.open_agent_on(self.c, x, runner=sent.append, person="mark")
        self.assertIn(f"RIVER_FOCUS=help:{x}@mark claude go", sent[-1])
        b = core.go(self.c, self.dir.name, None, focus=t["focus"])
        self.assertEqual(b["role"], "helper")
        self.assertIn("mark took it to do themselves", b["help_prompt"])
        with self.assertRaisesRegex(RiverError, "in progress by mark"):
            server.open_agent_on(self.c, x, runner=sent.append, person="someone-else")

    def test_windows_opens_a_console_window_with_the_env_set(self):
        core.project_add(self.c, "shop", path=self.dir.name)
        x = core.item_add(self.c, "shop", "work")["id"]
        server.PLATFORM = "win32"  # tearDown restores it
        sent = []
        t = server.dispatch_item(self.c, x, runner=sent.append)
        self.assertEqual(sent[0], {"args": "cmd /k claude go --remote-control", "cwd": t["path"],
                                   "env": {"RIVER_DB": str(core.db_path()), "RIVER_AGENT": t["session_name"]}})
        server.PLATFORM = "linux"
        core.item_add(self.c, "shop", "more work")
        with self.assertRaisesRegex(RiverError, "macOS and Windows"):
            server.launch_agent(self.c)

    def test_opens_terminal_in_the_folder_of_the_top_ready_item(self):
        folder = os.path.join(self.dir.name, 'my "shop" app')
        core.project_add(self.c, "shop", path=self.dir.name)
        core.project_path(self.c, "shop", folder, move=True)
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
                names = [f.relative_to(static).as_posix() for f in server._watched() if f.suffix == ".js"]
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

    def test_add_a_project_folder_from_the_page(self):
        blog = os.path.join(self.dir.name, "My Blog")
        os.mkdir(blog)
        with open(os.path.join(blog, "CLAUDE.md"), "w") as f:
            f.write("# Rules\nNo tabs.\n")
        r = server.folder_add(self.c, blog, description="my writing")
        self.assertEqual((r["project"], r["layout"]), ("my-blog", "claude_only"))
        self.assertEqual(core._project(self.c, "my-blog")["path"], os.path.realpath(blog))
        self.assertEqual(core._project(self.c, "my-blog")["notes"], "my writing")
        self.assertIn("Biggest River", open(os.path.join(blog, "AGENTS.md")).read())
        # the rules choice, then the same folder again: nothing new, still one project
        r = server.folder_add(self.c, blog, move=True)
        self.assertEqual((r["project"], r["layout"]), ("my-blog", "shared"))
        self.assertEqual(open(os.path.join(blog, "CLAUDE.md")).read().strip(), "@AGENTS.md")
        self.assertEqual(sum(p["name"] == "my-blog" for p in core.project_list(self.c)), 1)

    def test_add_a_project_folder_refuses_bad_paths_and_a_name_in_use_elsewhere(self):
        for bad in ("", "relative/path", os.path.join(self.dir.name, "missing")):
            with self.assertRaises(RiverError):
                server.folder_add(self.c, bad)
        other = os.path.join(self.dir.name, "other")
        os.mkdir(other)
        with self.assertRaisesRegex(RiverError, "--move"):
            server.folder_add(self.c, other, name="shop")
        self.assertEqual(core._project(self.c, "shop")["path"], os.path.realpath(self.folder))

    def test_install_the_river_command_writes_the_launcher_and_the_path_once(self):
        home = os.path.join(self.dir.name, "home")
        os.mkdir(home)
        found = {"path": None}
        old = (os.environ.get("HOME"), os.environ.get("SHELL"), server._login_shell_river, server.PLATFORM)
        os.environ["HOME"], os.environ["SHELL"], server.PLATFORM = home, "/bin/zsh", "darwin"
        server._login_shell_river = lambda: found["path"]
        try:
            self.assertFalse(server.river_command_status()["ok"])
            r = server.install_river_command()
            launcher = os.path.join(home, ".local", "bin", "river")
            self.assertTrue(os.access(launcher, os.X_OK))
            self.assertIn(server.LAUNCHER_MARK, open(launcher).read())
            prof = open(os.path.join(home, ".zprofile")).read()
            self.assertIn('$HOME/.local/bin', prof)
            self.assertEqual(len(r["changed"]), 2)
            found["path"] = launcher  # a new terminal now finds it
            self.assertTrue(server.river_command_status()["ok"])
            server.install_river_command()  # again: the profile line is not added twice
            self.assertEqual(open(os.path.join(home, ".zprofile")).read(), prof)
            # a river command the person installed (a clone, pip) is left alone
            found["path"] = "/opt/elsewhere/river"
            self.assertTrue(server.river_command_status()["ok"])
            self.assertIn("already installed", server.install_river_command()["note"])
        finally:
            for k, v in (("HOME", old[0]), ("SHELL", old[1])):
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            server._login_shell_river, server.PLATFORM = old[2], old[3]

    def test_block_fix_refuses_a_folder_that_is_not_a_project(self):
        with self.assertRaises(RiverError):
            server.setup_block(self.c, self.dir.name)
        self.assertFalse(os.path.exists(os.path.join(self.dir.name, "CLAUDE.md")))

    def test_an_added_agent_becomes_the_default_when_the_first_is_not_installed(self):
        old = server._login_shell_which, server.PLATFORM
        server._login_shell_which, server.PLATFORM = (lambda names: {n: None for n in names}), "darwin"
        try:
            self.assertEqual(server.setup_agent_add(self.c, "Codex"), ["Codex", "Claude Code"])
        finally:
            server._login_shell_which, server.PLATFORM = old

    def test_add_agent_appends_to_launch_agents_once(self):
        old = server._login_shell_which
        server._login_shell_which = lambda names: {n: "/bin/" + n for n in names}
        self.addCleanup(setattr, server, "_login_shell_which", old)
        server.setup_agent_add(self.c, "Codex")
        labels = server.setup_agent_add(self.c, "Codex")
        self.assertEqual(labels, ["Claude Code", "Codex"])
        # Codex's sandbox writes only in the project folder: the command lets it write the queue too.
        codex = dict(core.parse_launch_agents(core.setting(self.c, "launch_agents")))["Codex"]
        self.assertRegex(codex, r"--add-dir [\"']?" + re.escape(str(core.db_path().resolve().parent)))
        self.assertNotIn("{river_dir}", codex)
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


class ServerBind(unittest.TestCase):
    def test_the_server_starts_without_a_dns_lookup_of_its_name(self):
        # HTTPServer asks DNS for the full host name; on a Mac with a slow network that hung for minutes.
        import socket
        from unittest import mock
        with mock.patch.object(socket, "getfqdn", side_effect=AssertionError("getfqdn called")):
            httpd = server._Server(("127.0.0.1", 0), server.Handler)
        try:
            self.assertEqual(httpd.server_name, "127.0.0.1")
            self.assertGreater(httpd.server_port, 0)
        finally:
            httpd.server_close()
