import os
import tempfile
import unittest

from river import core, server


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


if __name__ == "__main__":
    unittest.main()
