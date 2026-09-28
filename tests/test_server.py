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
        self.assertEqual((t["project"], t["item"]["id"], t["command"]), ("shop", x, "claude go"))
        self.assertIn('tell application "Terminal"', sent[0])
        self.assertIn('keystroke "t" using command down', sent[0])  # a new tab by default
        core.config_set(self.c, "launch_in", "window")
        server.launch_agent(self.c, runner=sent.append)
        self.assertNotIn("keystroke", sent[-1])
        core.config_set(self.c, "launch_in", "tab")
        self.assertIn("claude go", sent[0])
        self.assertIn('my \\"shop\\" app', sent[0])  # quotes escaped inside the AppleScript string
        core.config_set(self.c, "launch_command", "claude --model opus go")
        self.assertIn("claude --model opus go", server.launch_agent(self.c, runner=sent.append)["command"])
        core.item_add(self.c, "nofolder", "x")
        with self.assertRaises(RiverError):
            server.launch_agent(self.c, "nofolder", runner=sent.append)  # no folder to start in
        with self.assertRaises(RiverError):
            server.launch_agent(self.c, "nosuch", runner=sent.append)


if __name__ == "__main__":
    unittest.main()
