import json
import os
import re
import tempfile
import unittest
from pathlib import Path

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

    def test_item_models_through_the_page(self):
        i = self.op("item_add", None, project="a", title="x", models={"model": "opus", "effort": "high"})["id"]
        self.op("item_edit", None, id=i, models={"max_model": "fable", "effort": "none"})
        code, st = self.get("/api/state")
        it = next(x for x in st["items"] if x["id"] == i)
        self.assertEqual((it["model"], it["effort"], it["max_model"]), ("opus", None, "fable"))
        self.assertEqual(st["effort_levels"], ["low", "medium", "high", "xhigh", "max"])
        self.assertIn("claude", st["model_ladder"])

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
        self.assertRegex(sent[0], r"RIVER_DB=\S*q\.db'? RIVER_AGENT=\S+ claude go")  # the path is quoted when it needs it

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

    def test_launch_dialog_choices_fill_the_command(self):
        core.project_add(self.c, "shop", path=self.dir.name)
        x = core.item_add(self.c, "shop", "work", models={"min_model": "opus"})["id"]
        sent = []
        t = server.launch_agent(self.c, runner=sent.append, model="opus", effort="high", launch_in="window")
        self.assertEqual(t["command"], "claude --model opus --effort high go --remote-control")
        self.assertIn("RIVER_MODEL=opus claude --model opus --effort high go", sent[-1])
        self.assertNotIn("keystroke", sent[-1])  # launch_in window, although the setting says tab
        x = core.item_add(self.c, "shop", "more work", models={"min_model": "opus"})["id"]  # Start reserved the first
        t = server.launch_agent(self.c, runner=sent.append)
        self.assertEqual(t["command"], "claude go --remote-control")  # no choice: the flags drop out
        self.assertNotIn("RIVER_MODEL", sent[-1])
        x = core.item_add(self.c, "shop", "third", models={"min_model": "opus"})["id"]
        with self.assertRaisesRegex(RiverError, "needs at least opus"):
            server.dispatch_item(self.c, x, runner=sent.append, model="sonnet")
        # A session that waits for work gets the item only when its model is allowed.
        core.register(self.c, "w")
        core.set_agent_model(self.c, "w", "sonnet")
        self.c.execute("UPDATE agents SET role='waiting', waiting_in='shop', waiting_since='2026-01-01T00:00:00Z' WHERE name='w'")
        self.assertIsNone(core.waiting_agent_for(self.c, "shop", x))
        self.assertEqual(core.waiting_agent_for(self.c, "shop"), "w")
        core.set_agent_model(self.c, "w", "opus")
        self.assertEqual(server.dispatch_item(self.c, x, runner=sent.append)["pushed_to"], "w")
        core.cancel_push(self.c, x)
        core.unregister(self.c, "w")
        server.PLATFORM = "win32"  # tearDown restores it
        t = server.dispatch_item(self.c, x, runner=sent.append, model="fable")
        self.assertEqual(sent[-1]["env"]["RIVER_MODEL"], "fable")
        b = core.go(self.c, self.dir.name, t["session_name"], model=sent[-1]["env"]["RIVER_MODEL"])
        self.assertEqual((b["item"]["id"], core.agent_model(self.c, t["session_name"])), (x, "fable"))

    def test_a_deploy_opens_a_monitor_session(self):
        core.register(self.c, "ag")
        core.target_add(self.c, "web", "push")
        core.target_monitor(self.c, "web", "watch /health for 10 minutes")
        core.project_add(self.c, "site", target="web", path=self.dir.name)
        w = core.item_add(self.c, "site", "page")["id"]
        core.claim(self.c, w, "ag")
        core.done(self.c, w, "abc", "ag", ship_it=True)
        dep = core.item_show(self.c, w)["unblocks"][0]
        core.target_own(self.c, "web", "ag")
        core.claim(self.c, dep, "ag")
        (m,) = core.pending_monitors(self.c)
        with self.assertRaisesRegex(RiverError, "another queue"):
            server.open_monitors(self.c, runner=[].append, db=os.path.join(self.dir.name, "other.db"))
        sent = []
        (r,) = server.open_monitors(self.c, runner=sent.append, db=str(core.db_path()))
        self.assertEqual((r["id"], r["project"], r["model"]), (m["id"], "site", "sonnet"))
        self.assertIn(f"RIVER_FOCUS=monitor:{m['id']} RIVER_MODEL=sonnet claude --model sonnet --effort low go", sent[-1])
        self.assertEqual(server.open_monitors(self.c, runner=sent.append), [])  # opened once
        self.assertEqual(len(sent), 1)
        # No server answers: the command says how to start the session by hand.
        from river import cli
        self.c.execute("DELETE FROM events WHERE change LIKE 'monitor session opened%'")
        core.config_set(self.c, "serve_port", "1")
        got = cli.ask_server_for_monitors(self.c, timeout=1)
        self.assertIn("no river serve answers", got["error"])
        self.assertIn(f"RIVER_FOCUS=monitor:{m['id']} claude go", cli._monitor_lines(got)[0])

    def test_start_spreads_sessions_across_projects(self):
        os.makedirs(os.path.join(self.dir.name, "b"))
        core.project_add(self.c, "a", path=self.dir.name)
        core.project_add(self.c, "b", path=os.path.join(self.dir.name, "b"))
        a = [core.item_add(self.c, "a", f"a{n}", priority=0)["id"] for n in range(3)]
        b = [core.item_add(self.c, "b", f"b{n}", priority=3)["id"] for n in range(2)]
        sent = []
        self.assertEqual(core.state(self.c)["start_next"]["id"], a[0])
        t1 = server.launch_agent(self.c, runner=sent.append)
        self.assertEqual((t1["item"]["id"], t1["why"]), (a[0], "first agent for project a"))
        self.assertEqual(core.state(self.c)["start_next"]["project"], "b")
        t2 = server.launch_agent(self.c, runner=sent.append)
        self.assertEqual((t2["item"]["id"], t2["why"]), (b[0], "first agent for project b; a already has one"))
        t3 = server.launch_agent(self.c, runner=sent.append)
        self.assertEqual(t3["item"]["id"], a[1])
        self.assertIn("every project with ready work has an agent", t3["why"])
        # A project whose only sessions are gone counts as uncovered again.
        old = core.iso(core.now() - core.timedelta(days=3))
        self.c.execute("UPDATE agents SET last_seen=? WHERE name IN (?,?)", (old, t1["session_name"], t3["session_name"]))
        self.assertEqual(server.launch_agent(self.c, runner=sent.append)["item"]["id"], a[2])
        # A named project keeps its choice.
        t = server.launch_agent(self.c, "b", runner=sent.append)
        self.assertEqual((t["project"], t["why"]), ("b", None))

    def test_river_launch_from_the_command_line(self):
        import contextlib
        import io
        from river import cli
        core.project_add(self.c, "shop", path=self.dir.name)
        x = core.item_add(self.c, "shop", "work", models={"min_model": "opus"})["id"]
        y = core.item_add(self.c, "shop", "more")["id"]
        core.register(self.c, "mark", human=True)
        sent = []
        server.TERMINAL_RUNNER = sent.append
        self.addCleanup(setattr, server, "TERMINAL_RUNNER", None)

        def river(*args):
            with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.run(["-q", "--as", "mark", *args]), 0)
            return out.getvalue()
        out = river("launch", "--dry-run", "--model", "opus", "--effort", "high", "--window")
        self.assertRegex(out, rf"would start Claude Code in shop \(\S+\) for #{x} work")
        self.assertIn("RIVER_MODEL=opus claude --model opus --effort high go --remote-control   (in a new window)", out)
        self.assertEqual(sent, [])
        with self.assertRaisesRegex(RiverError, "needs at least opus"):
            cli.dispatch(self.c, cli.build_parser().parse_args(["launch", "--model", "sonnet"]), "mark")
        out = river("launch", "--item", str(y), "--model", "sonnet")
        self.assertRegex(out, rf"started Claude Code as shop-\w+ in shop for #{y} more")
        self.assertIn("RIVER_MODEL=sonnet claude --model sonnet go", sent[-1])
        out = river("launch", "--project", "shop", "--tab")
        self.assertIn(f"for #{x} work", out)
        self.assertIn('keystroke "t"', sent[-1])

    def test_manager_section_start_chat_queue_and_stop(self):
        core.project_add(self.c, "shop", path=self.dir.name)
        x = core.item_add(self.c, "shop", "work")["id"]
        core.register(self.c, "mark", human=True)
        core.register(self.c, "w1")
        self.assertIsNone(core.state(self.c)["manager"])
        sent = []
        t = server.start_manager(self.c, runner=sent.append, model="opus")
        self.assertEqual(t["command"], "claude --model opus manage --remote-control")
        self.assertIn(f"RIVER_AGENT={t['session_name']} RIVER_MODEL=opus claude --model opus manage", sent[-1])
        with self.assertRaisesRegex(RiverError, "is the active manager"):
            server.start_manager(self.c, runner=sent.append)
        self.assertEqual(server.manage_command('codex "run river go in this folder"'), 'codex "run river manage in this folder"')
        m = t["session_name"]
        core.queue_add(self.c, "w1", x, actor=m)
        st = core.state(self.c)
        self.assertEqual((st["manager"]["name"], st["manager"]["actions"][0]["change"][:9]), (m, "queued fo"))
        self.assertEqual(st["queues"]["w1"][0]["item"], x)
        # The page's ops: queue, stop, open chat.
        OPS = server.OPS
        OPS["queue_remove"](self.c, {"agent": "w1", "ref": str(x)}, "mark")
        OPS["queue_add"](self.c, {"agent": "w1", "message": "commit first"}, "mark")
        r = OPS["stop_agent"](self.c, {"agent": "w1", "reason": "done for today"}, "mark")
        self.assertIn("ends", r)
        self.assertEqual(OPS["open_chat"](self.c, {"agent": "w1"}, "mark")["hint"][:24], "No chat to open for w1: ")
        core.record_session_url(self.c, "w1", "https://claude.ai/code/session_x")
        self.assertEqual(OPS["open_chat"](self.c, {"agent": "w1"}, "mark")["url"], "https://claude.ai/code/session_x")

    def test_launch_options_follow_each_agents_platform(self):
        old = server._login_shell_which
        server._login_shell_which = lambda names: {n: "/bin/" + n for n in names}
        self.addCleanup(setattr, server, "_login_shell_which", old)
        server.setup_agent_add(self.c, "Codex")
        core.config_set(self.c, "launch_agents", core.setting(self.c, "launch_agents") + "; Mine=myagent go")
        opts = {o["label"]: o for o in core.state(self.c)["launch_options"]}
        self.assertEqual((opts["Claude Code"]["family"], [m["name"] for m in opts["Claude Code"]["models"]]),
                         ("claude", ["sonnet", "opus", "fable"]))
        self.assertEqual(opts["Claude Code"]["efforts"], ["low", "medium", "high", "xhigh", "max"])
        self.assertEqual((opts["Codex"]["family"], [m["name"] for m in opts["Codex"]["models"]]),
                         ("openai", ["luna", "terra", "sol", "astra"]))
        self.assertTrue(opts["Codex"]["takes_model"] and opts["Codex"]["takes_effort"])
        self.assertTrue(all(m["note"] for m in opts["Codex"]["models"]))
        self.assertEqual((opts["Mine"]["family"], len(opts["Mine"]["models"]), opts["Mine"]["takes_model"]), (None, 7, False))
        self.assertEqual([o["name"] for o in opts["Claude Code"]["options"]], ["remote_control", "permission_mode"])
        self.assertEqual([o["name"] for o in opts["Codex"]["options"]], ["sandbox", "approval"])
        self.assertEqual(opts["Mine"]["options"], [])
        c = core.fill_launch_command('codex -m {model} -c model_reasoning_effort={effort} "go"', "sol", "xhigh")
        self.assertEqual(c, 'codex -m sol -c model_reasoning_effort=xhigh "go"')
        self.assertEqual(core.fill_launch_command('codex -m {model} -c model_reasoning_effort={effort} "go"', None, None),
                         'codex "go"')
        self.assertEqual(core.fill_launch_command("x --v --effort={effort} go", None, None), "x --v go")

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
        for n in range(6):  # each Start reserves one item for the session it opens
            core.item_add(self.c, "shop", f"more work {n}")
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

    @unittest.skipIf(os.name == "nt", "the river command installer is for macOS and Linux shells")
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
        self.assertEqual(dict(core.parse_launch_agents(core.setting(self.c, "launch_agents")))["Codex"], "@codex")
        codex = core._launch_agent_cmd(self.c, None, "Codex")["command"]
        self.assertRegex(codex, r"--add-dir [\"']?" + re.escape(str(core.db_path().resolve().parent)))
        shown = {a["label"]: a["command"] for a in server.setup_status(self.c)["agent_clis"]}
        self.assertEqual(shown["Codex"], codex)
        self.assertEqual(shown["Claude Code"], "claude go --remote-control")
        with self.assertRaises(RiverError):
            server.setup_agent_add(self.c, "nope")

    def test_dismiss_is_a_setting(self):
        core.config_set(self.c, "setup_done", "on")
        self.assertTrue(server.setup_status(self.c)["done"])
        with self.assertRaises(RiverError):
            core.config_set(self.c, "setup_done", "maybe")


class LaunchProfiles(unittest.TestCase):
    """Claude Code and Codex commands built from options (launch profiles), and the move of old command strings."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        os.environ["RIVER_DB"] = os.path.join(self.dir.name, "t.db")
        self.c = core.connect()
        self.platform = server.PLATFORM
        server.PLATFORM = "darwin"
        core.project_add(self.c, "shop", path=self.dir.name)

    def tearDown(self):
        server.PLATFORM = self.platform
        self.c.close()
        os.environ.pop("RIVER_DB", None)
        self.dir.cleanup()

    def test_each_option_set_builds_the_real_flags(self):
        claude = core.profile_options(self.c, "claude-code")
        self.assertEqual(core.build_command("claude-code", claude), "claude go --remote-control")
        self.assertEqual(core.build_command("claude-code", claude, "opus", "high"),
                         "claude --model opus --effort high go --remote-control")
        self.assertEqual(core.build_command("claude-code", {**claude, "remote_control": "off", "permission_mode": "plan",
                                                            "args": "--verbose", "prompt": "run river go now"}, "opus"),
                         "claude --model opus --permission-mode plan --verbose 'run river go now'")
        rd = core.river_dir()
        codex = core.profile_options(self.c, "codex")
        self.assertEqual(core.build_command("codex", codex, "sol", "xhigh"),
                         f"codex -m sol -c model_reasoning_effort=xhigh --add-dir {rd} "
                         "'run river go in this folder and follow the briefing'")
        self.assertEqual(core.build_command("codex", {**codex, "sandbox": "workspace-write", "approval": "never",
                                                      "prompt": "go"}),
                         f"codex --sandbox workspace-write --ask-for-approval never --add-dir {rd} go")

    def test_options_come_from_settings_the_entry_and_the_dialog(self):
        core.config_set(self.c, "launch_agents", "Claude Code=@claude-code; Plan=@claude-code permission_mode=plan; Codex=@codex")
        core.config_set(self.c, "claude_remote_control", "off")
        core.config_set(self.c, "claude_remote_control", "on", project="shop")  # most specific wins
        pid = core._project(self.c, "shop")["id"]
        self.assertEqual(core._launch_agent_cmd(self.c, None, None)["command"], "claude go")
        self.assertEqual(core._launch_agent_cmd(self.c, pid, None)["command"], "claude go --remote-control")
        self.assertEqual(core._launch_agent_cmd(self.c, pid, "Plan")["command"],
                         "claude --permission-mode plan go --remote-control")
        t = core._launch_agent_cmd(self.c, pid, "Plan", "opus", options={"remote_control": "off", "permission_mode": "auto"})
        self.assertEqual((t["command"], t["platform"]), ("claude --model opus --permission-mode auto go", "claude-code"))
        with self.assertRaisesRegex(RiverError, "no option 'args' to choose at launch"):
            core._launch_agent_cmd(self.c, pid, None, options={"args": "--x"})
        with self.assertRaisesRegex(RiverError, "sandbox is one of"):
            core._launch_agent_cmd(self.c, pid, "Codex", options={"sandbox": "wide-open"})
        core.config_set(self.c, "launch_agents", "Claude Code=@claude-code; Grok=grok go")
        with self.assertRaisesRegex(RiverError, "custom command"):
            core._launch_agent_cmd(self.c, pid, "Grok", options={"remote_control": "off"})
        # Settings and entries are checked when set.
        for key, value, msg in (("claude_remote_control", "yes", "on or off"), ("codex_approval", "always", "one of"),
                                ("claude_prompt", " ", "first prompt"), ("launch_agents", "X=@gemini", "platform is one of"),
                                ("launch_agents", "X=@codex colour=red", "no option 'colour'"),
                                ("launch_agents", "X=@codex sandbox", "option=value")):
            with self.assertRaisesRegex(RiverError, msg):
                core.config_set(self.c, key, value)
        # The page: the dialog's options per agent, and Setup's options per profile.
        opts = {o["label"]: o for o in core.state(self.c)["launch_options"]}
        self.assertEqual(opts["Claude Code"]["options"][0]["value"], "off")  # the global value
        self.assertEqual((opts["Grok"]["options"], opts["Grok"]["takes_model"]), ([], False))
        (prof,) = server.setup_status(self.c)["launch_profiles"]
        self.assertEqual([o["setting"] for o in prof["options"]],
                         ["claude_remote_control", "claude_permission_mode", "claude_model_ids", "claude_args", "claude_prompt"])

    def test_the_cli_gets_its_own_model_id_and_river_keeps_the_name(self):
        pid = core._project(self.c, "shop")["id"]
        rd = core.river_dir()
        codex_home = tempfile.TemporaryDirectory()
        self.addCleanup(codex_home.cleanup)
        os.environ["CODEX_HOME"] = codex_home.name
        self.addCleanup(os.environ.pop, "CODEX_HOME", None)
        core.config_set(self.c, "launch_agents", "Claude Code=@claude-code; Codex=@codex; Mine=codex -m {model} go")
        t = core._launch_agent_cmd(self.c, pid, "Codex", "astra", "high")
        self.assertEqual(t["command"], f"codex -m gpt-6-astra -c model_reasoning_effort=high --add-dir {rd} "
                                       "'run river go in this folder and follow the briefing'")
        self.assertEqual((t["model"], t["model_id"], t["env"]), ("astra", "gpt-6-astra", {"RIVER_MODEL": "astra"}))
        self.assertEqual(core._launch_agent_cmd(self.c, pid, "Mine", "terra")["command"], "codex -m gpt-5.6-terra go")
        # Claude Code takes the ladder names as they are.
        self.assertEqual(core._launch_agent_cmd(self.c, pid, "Claude Code", "fable", "max")["command"],
                         "claude --model fable --effort max go --remote-control")
        # A new release: change the list, per project if needed; a name with no entry goes as it is.
        core.config_set(self.c, "codex_model_ids", "astra=gpt-7-astra", project="shop")
        self.assertIn("-m gpt-7-astra ", core._launch_agent_cmd(self.c, pid, "Codex", "astra")["command"])
        self.assertIn("-m sol ", core._launch_agent_cmd(self.c, pid, "Codex", "sol")["command"])
        self.assertIn("-m gpt-6.1-sol ", core._launch_agent_cmd(self.c, None, "Codex", "sol")["command"])
        with self.assertRaisesRegex(RiverError, "name=id"):
            core.config_set(self.c, "codex_model_ids", "astra gpt-6-astra")
        # Effort: Codex levels, or the model's own when the Codex model cache lists it.
        with self.assertRaisesRegex(RiverError, "not minimal"):
            core._launch_agent_cmd(self.c, None, "Codex", "sol", "minimal")
        Path(codex_home.name, "models_cache.json").write_text(json.dumps({"models": [
            {"slug": "gpt-6-luna", "supported_reasoning_levels": [{"effort": e} for e in ("low", "medium", "high")]}]}))
        self.assertIn("model_reasoning_effort=high", core._launch_agent_cmd(self.c, None, "Codex", "luna", "high")["command"])
        with self.assertRaisesRegex(RiverError, "gpt-6-luna takes effort low, medium, high, not max"):
            core._launch_agent_cmd(self.c, None, "Codex", "luna", "max")
        # The dialog offers each agent the models of its family only, and the CLI's effort levels.
        opts = {o["label"]: o for o in core.state(self.c)["launch_options"]}
        self.assertEqual([m["name"] for m in opts["Codex"]["models"]], ["luna", "terra", "sol", "astra"])
        self.assertEqual([m["name"] for m in opts["Claude Code"]["models"]], ["sonnet", "opus", "fable"])
        self.assertEqual(opts["Codex"]["efforts"], ["low", "medium", "high", "xhigh", "max"])

    def test_a_launch_with_options_through_a_fake_runner(self):
        x = core.item_add(self.c, "shop", "work")["id"]
        sent = []
        server.TERMINAL_RUNNER = sent.append
        self.addCleanup(setattr, server, "TERMINAL_RUNNER", None)
        t = server.OPS["dispatch_item"](self.c, {"id": x, "model": "opus", "options": {"remote_control": "off"}}, "mark")
        self.assertEqual(t["command"], "claude --model opus go")
        self.assertIn(f"RIVER_AGENT={t['session_name']} RIVER_MODEL=opus claude --model opus go\"", sent[-1])
        y = core.item_add(self.c, "shop", "more")["id"]
        t = server.dispatch_item(self.c, y, runner=sent.append, options={"permission_mode": "acceptEdits"})
        self.assertIn("claude --permission-mode acceptEdits go --remote-control", sent[-1])
        # A manager: manage in place of go, the options still apply.
        t = server.start_manager(self.c, runner=sent.append, options={"remote_control": "off"})
        self.assertEqual(t["command"], "claude manage")
        # Open chat says why a session has no web link.
        core.register(self.c, "w1")
        core.config_set(self.c, "claude_remote_control", "off")
        self.assertIn("Remote Control is off", server.open_chat(self.c, "w1")["hint"])

    def test_the_command_line_takes_options(self):
        import contextlib
        import io
        from river import cli
        core.item_add(self.c, "shop", "work")
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.run(["-q", "launch", "--dry-run", "--option", "remote_control=off",
                                      "--option", "permission_mode=plan"]), 0)
        self.assertIn("command: claude --permission-mode plan go   (in a new tab)", out.getvalue())

    def migrate(self, value, scope="global"):
        self.c.execute("INSERT INTO settings(scope,key,value) VALUES (?,?,?) "
                       "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value", (scope, "launch_agents", value))
        self.c.execute("DELETE FROM meta WHERE key='launch_profiles'")
        self.c.close()
        self.c = core.connect()
        return self.c.execute("SELECT value FROM settings WHERE scope=? AND key='launch_agents'", (scope,)).fetchone()["value"]

    def test_old_command_strings_become_profiles(self):
        # The report: a value saved before the launch dialog never got {model} and {effort}.
        self.assertEqual(self.migrate("Claude Code=claude go --remote-control"), "Claude Code=@claude-code")
        self.assertEqual(core._launch_agent_cmd(self.c, None, None, "opus", "high")["command"],
                         "claude --model opus --effort high go --remote-control")
        self.assertIn("Claude Code: claude go --remote-control -> @claude-code", core.launch_migration_note(self.c))
        # It runs once: a custom command set later stays as it is.
        core.config_set(self.c, "launch_agents", "Claude Code=claude go")
        self.c.close()
        self.c = core.connect()
        self.assertEqual(core.setting(self.c, "launch_agents"), "Claude Code=claude go")
        # Options: the first entry of a platform sets the scope's settings, a second one keeps its own.
        core.config_unset(self.c, "launch_agents")
        rd = core.river_dir()
        got = self.migrate(f"Claude Code=claude --model {{model}} --effort {{effort}} go; "
                           f"Planner=claude --permission-mode plan go --remote-control; "
                           f"Codex=codex -m {{model}} -c model_reasoning_effort={{effort}} --add-dir {rd} -s read-only "
                           f"\"run river go and follow it\"; "
                           f"Fixed=claude --model sonnet go; Odd=claude --verbose go; Grok=grok \"run river go\"", "project:shop")
        self.assertEqual(got, "Claude Code=@claude-code; Planner=@claude-code remote_control=on permission_mode=plan; "
                              "Codex=@codex; Fixed=claude --model sonnet go; Odd=claude --verbose go; Grok=grok \"run river go\"")
        pid = core._project(self.c, "shop")["id"]
        self.assertEqual(core.setting(self.c, "claude_remote_control", project_id=pid), "off")
        self.assertEqual(core.setting(self.c, "claude_remote_control"), "on")  # other projects keep the default
        self.assertEqual((core.setting(self.c, "codex_sandbox", project_id=pid),
                          core.setting(self.c, "codex_prompt", project_id=pid)), ("read-only", "run river go and follow it"))
        self.assertEqual(core._launch_agent_cmd(self.c, pid, "Codex", "sol")["command"],
                         f"codex -m gpt-6.1-sol --sandbox read-only --add-dir {rd} 'run river go and follow it'")
        self.assertEqual(core._launch_agent_cmd(self.c, pid, "Planner")["command"],
                         "claude --permission-mode plan go --remote-control")
        # A remote-control name, or no prompt: custom, since a profile would change them.
        self.assertIsNone(core.profile_from_command("claude --remote-control mine go"))
        self.assertIsNone(core.profile_from_command("claude --model {model}"))
        self.assertIsNone(core.profile_from_command("codex --add-dir /elsewhere go"))
        from river import cli
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.run(["config", "get", "launch_agents", "--project", "shop"]), 0)
        self.assertIn("launch_agents migrated to launch profiles (project:shop)", out.getvalue())
        self.assertIn("claude_remote_control=off", out.getvalue())


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
        core.item_add(self.c, "shop", "agent step")
        x = core.item_add(self.c, "shop", "next step")["id"]
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
