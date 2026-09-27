import os
import tempfile
import threading
import unittest
from datetime import timedelta

from river import core
from river.core import RiverError


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.db")
        self.c = core.connect(self.path)

    def tearDown(self):
        self.c.close()
        self.dir.cleanup()

    def add(self, project, title, p=2, after=(), doer="any"):
        return core.item_add(self.c, project, title, p, "", doer, after, "t")["id"]


class Ordering(Base):
    def test_priority_inherits_from_dependents(self):
        core.project_add(self.c, "a")
        low = self.add("a", "low prereq", 4)
        mid = self.add("a", "mid", 2)
        top = self.add("a", "top", 0, after=[low])
        ann = core.annotate(self.c)
        self.assertEqual(ann[low]["effective_priority"], 0)
        self.assertEqual(ann[low]["priority_from"], top)
        ready = [a["id"] for a in core.ready_list(self.c)]
        self.assertEqual(ready, [low, mid])  # top waits on low

    def test_project_rank_breaks_ties(self):
        core.project_add(self.c, "a")
        core.project_add(self.c, "b")
        ia = self.add("a", "x")
        ib = self.add("b", "y")
        self.assertEqual([a["id"] for a in core.ready_list(self.c)], [ia, ib])
        core.project_rank(self.c, "b", 1)
        self.assertEqual([a["id"] for a in core.ready_list(self.c)], [ib, ia])

    def test_unblock_count_breaks_ties(self):
        core.project_add(self.c, "a")
        lone = self.add("a", "lone")
        hub = self.add("a", "hub")
        self.add("a", "d1", after=[hub])
        self.add("a", "d2", after=[hub])
        self.assertEqual(core.ready_list(self.c)[0]["id"], hub)
        self.assertNotEqual(lone, hub)

    def test_cycle_refused(self):
        core.project_add(self.c, "a")
        x = self.add("a", "x")
        y = self.add("a", "y", after=[x])
        z = self.add("a", "z", after=[y])
        with self.assertRaises(RiverError):
            core.dep_add(self.c, x, [z])

    def test_areas(self):
        core.project_add(self.c, "a")
        core.project_add(self.c, "b")
        a1 = self.add("a", "a1", 0)
        b1 = self.add("b", "b1", 3)
        b2 = self.add("b", "b2", 3, after=[b1])
        far = self.add("b", "far", 1)
        self.assertEqual(core.ready_list(self.c, project="b")[0]["id"], far)
        self.assertEqual([a["id"] for a in core.ready_list(self.c, project="a,b")][0], a1)
        self.assertEqual([a["id"] for a in core.ready_list(self.c, unblocks=b2)], [b1])
        near = [a["id"] for a in core.ready_list(self.c, near=str(b2))]
        self.assertEqual(near, [b1])  # far and a1 are not linked to b2

    def test_outside_blocker(self):
        core.project_add(self.c, "a")
        x = self.add("a", "x")
        core.block(self.c, x, "waiting on Stripe")
        self.assertEqual(core.ready_list(self.c), [])
        core.unblock(self.c, x)
        self.assertEqual(len(core.ready_list(self.c)), 1)


class Claims(Base):
    def setUp(self):
        super().setUp()
        core.project_add(self.c, "a")
        core.register(self.c, "ag1")
        core.register(self.c, "ag2")
        core.register(self.c, "sam", human=True)

    def test_claim_needs_ready(self):
        x = self.add("a", "x")
        y = self.add("a", "y", after=[x])
        with self.assertRaises(RiverError):
            core.claim(self.c, y, "ag1")

    def test_max_leases(self):
        self.add("a", "x")
        self.add("a", "y")
        core.next_item(self.c, claim=True, actor="ag1")
        with self.assertRaises(RiverError):
            core.next_item(self.c, claim=True, actor="ag1")
        core.config_set(self.c, "max_leases", "2", agent="ag1")
        self.assertEqual(len(core.next_item(self.c, claim=True, actor="ag1")), 1)

    def test_doer_filter(self):
        h = self.add("a", "bank", 0, doer="human")
        ai = self.add("a", "code", 3, doer="ai")
        self.assertEqual(core.next_item(self.c, actor="ag1")[0]["id"], ai)
        self.assertEqual(core.next_item(self.c, actor="sam")[0]["id"], h)

    def test_lease_expiry(self):
        x = self.add("a", "x")
        core.claim(self.c, x, "ag1")
        past = core.iso(core.now() - timedelta(minutes=1))
        self.c.execute("UPDATE items SET lease_expires_at=? WHERE id=?", (past, x))
        core.activity(self.c, None)
        self.assertEqual(core._item(self.c, x)["status"], "open")

    def test_done_reports_newly_ready(self):
        x = self.add("a", "x")
        y = self.add("a", "y", after=[x])
        core.claim(self.c, x, "ag1")
        self.assertEqual(core.done(self.c, x, "ok", "ag1")["now_ready"], [y])

    def test_parallel_claims_get_different_items(self):
        for i in range(6):
            self.add("a", f"i{i}")
        got, errs = [], []

        def worker(name):
            c = core.connect(self.path)
            core.register(c, name)
            try:
                got.extend(r["id"] for r in core.next_item(c, claim=True, actor=name))
            except RiverError as e:
                errs.append(e)
            finally:
                c.close()

        ts = [threading.Thread(target=worker, args=(f"w{i}",)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(errs, [])
        self.assertEqual(len(got), 6)
        self.assertEqual(len(set(got)), 6)


class Areas(Base):
    def test_mine_uses_history(self):
        core.project_add(self.c, "a")
        core.project_add(self.c, "b")
        core.register(self.c, "ag")
        x = self.add("a", "x")
        linked = self.add("b", "linked", 3, after=[x])
        same = self.add("a", "same project", 3)
        other = self.add("b", "unrelated", 0)
        with self.assertRaises(RiverError):
            core.next_item(self.c, actor="ag", mine=True)
        core.claim(self.c, x, "ag")
        core.done(self.c, x, "ok", "ag")
        got = [a["id"] for a in core.next_item(self.c, actor="ag", mine=True, limit=5)]
        self.assertEqual(got, [linked, same])
        self.assertNotIn(other, got)

    def test_project_describe_and_show(self):
        core.project_add(self.c, "a", notes="old")
        core.register(self.c, "ag")
        x = self.add("a", "x")
        core.project_describe(self.c, "a", "Checkout pages in web/; know React")
        core.claim(self.c, x, "ag")
        p = core.project_show(self.c, "a")
        self.assertEqual(p["description"], "Checkout pages in web/; know React")
        self.assertEqual(p["working_now"], ["ag"])


class Go(Base):
    def setUp(self):
        super().setUp()
        self.web = os.path.join(self.dir.name, "web")
        self.api = os.path.join(self.dir.name, "api")
        os.makedirs(os.path.join(self.web, "src"))
        os.makedirs(self.api)
        core.project_add(self.c, "web", path=self.web)
        core.project_add(self.c, "api", path=self.api)

    def test_worker_from_folder_and_resume(self):
        x = self.add("web", "x", doer="ai")
        b = core.go(self.c, os.path.join(self.web, "src"))
        self.assertEqual(b["role"], "worker")
        self.assertEqual(b["item"]["id"], x)
        self.assertTrue(b["agent"].startswith("web-"))
        again = core.go(self.c, self.web, actor=b["agent"])
        self.assertTrue(again.get("resumed"))

    def test_unblocker(self):
        a1 = self.add("api", "endpoint", doer="ai")
        self.add("web", "page", after=[a1], doer="ai")
        b = core.go(self.c, self.web)
        self.assertEqual(b["role"], "unblocker")
        self.assertEqual(b["item"]["id"], a1)

    def test_planner_when_empty_or_outside_blocked(self):
        self.assertEqual(core.go(self.c, self.web)["role"], "planner")
        x = self.add("web", "x")
        core.block(self.c, x, "waiting on design")
        self.assertEqual(core.go(self.c, self.web)["role"], "planner")

    def test_idle_when_others_hold_everything(self):
        self.add("web", "x", doer="ai")
        first = core.go(self.c, self.web)
        self.assertEqual(first["role"], "worker")
        self.assertEqual(core.go(self.c, self.web)["role"], "idle")

    def test_unlinked_folder_refused(self):
        with self.assertRaises(RiverError):
            core.go(self.c, self.dir.name)


class Capacity(Base):
    def test_slots_and_excess(self):
        core.project_add(self.c, "a")
        self.add("a", "x", doer="ai")
        self.add("a", "y", doer="ai")
        self.add("a", "h", doer="human")
        cap = core.capacity(self.c)
        self.assertEqual(cap["spare_slots"], 2)
        for n in ("s1", "s2", "s3", "s4"):
            core.register(self.c, n)
        cap = core.capacity(self.c)
        self.assertEqual(cap["spare_slots"], 0)
        self.assertEqual(cap["excess_sessions"], 2)
        self.assertEqual(len(cap["ready_for_humans"]), 1)

    def test_settings_precedence(self):
        core.project_add(self.c, "a")
        x = self.add("a", "x")
        core.config_set(self.c, "lease_ttl", "1h")
        core.config_set(self.c, "lease_ttl", "2h", project="a")
        core.config_set(self.c, "lease_ttl", "3h", agent="sam")
        core.config_set(self.c, "lease_ttl", "4h", item=x)
        self.assertEqual(core.setting(self.c, "lease_ttl", item_id=x, agent="sam"), "4h")
        core.config_unset(self.c, "lease_ttl", item=x)
        self.assertEqual(core.setting(self.c, "lease_ttl", item_id=x, agent="sam"), "3h")
        self.assertEqual(core.setting(self.c, "lease_ttl", item_id=x), "2h")
        with self.assertRaises(RiverError):
            core.config_set(self.c, "lease_ttl", "soon")


class Messages(Base):
    def setUp(self):
        super().setUp()
        core.project_add(self.c, "a")
        core.register(self.c, "alice")
        core.register(self.c, "bob")
        self.x = self.add("a", "x")

    def test_question_to_item_reaches_next_holder_and_answer_closes_it(self):
        q = core.send(self.c, "question", "why?", item=self.x, actor="bob")
        self.assertIsNone(q["to_agent"])
        self.assertEqual(core.unread(self.c, "alice")["unread"], 0)
        core.claim(self.c, self.x, "alice")
        self.assertEqual(core.unread(self.c, "alice"), {"unread": 1, "alerts": 0, "questions": 1})
        self.assertEqual([m["id"] for m in core.inbox(self.c, "alice")], [q["id"]])
        # Read, but still waiting for an answer, so it stays in the inbox.
        self.assertEqual(core.unread(self.c, "alice"), {"unread": 0, "alerts": 0, "questions": 1})
        self.assertEqual(len(core.inbox(self.c, "alice")), 1)
        a = core.answer(self.c, q["id"], "because", actor="alice")
        self.assertEqual(a["to_agent"], "bob")
        self.assertEqual(core.message_show(self.c, q["id"])["state"], "answered")
        self.assertEqual(core.inbox(self.c, "alice"), [])
        self.assertEqual(core.unread(self.c, "bob")["unread"], 1)
        with self.assertRaises(RiverError):
            core.answer(self.c, q["id"], "again", actor="alice")

    def test_send_to_held_item_goes_to_holder(self):
        core.claim(self.c, self.x, "alice")
        m = core.send(self.c, "alert", "stop", item=self.x, actor="bob")
        self.assertEqual(m["to_agent"], "alice")
        self.assertEqual(core.unread(self.c, "alice")["alerts"], 1)
        inbox = core.inbox(self.c, "alice")
        self.assertEqual(core.message_show(self.c, inbox[0]["id"])["state"], "read")
        self.assertEqual(core.inbox(self.c, "alice"), [])
        self.assertEqual(len(core.inbox(self.c, "alice", include_read=True)), 1)

    def test_reply_goes_to_sender_and_joins_thread(self):
        n = core.send(self.c, "note", "fyi", to="alice", actor="bob")
        r = core.send(self.c, "note", "thanks", reply_to=n["id"], actor="alice")
        self.assertEqual(r["to_agent"], "bob")
        self.assertEqual(r["thread_id"], n["id"])
        self.assertEqual(core.unread(self.c, "alice")["unread"], 0)  # replying reads it
        t = core.thread(self.c, r["id"], "bob")
        self.assertEqual([m["id"] for m in t["messages"]], [n["id"], r["id"]])
        self.assertEqual(core.unread(self.c, "bob")["unread"], 0)

    def test_refusals(self):
        with self.assertRaises(RiverError):
            core.send(self.c, "note", "hi", actor="bob")  # no recipient
        with self.assertRaises(RiverError):
            core.send(self.c, "note", "hi", to="nobody", actor="bob")
        with self.assertRaises(RiverError):
            core.send(self.c, "answer", "hi", to="alice", actor="bob")
        n = core.send(self.c, "note", "hi", to="alice", actor="bob")
        with self.assertRaises(RiverError):
            core.answer(self.c, n["id"], "no", actor="alice")
        q = core.send(self.c, "question", "?", to="alice", actor="bob")
        with self.assertRaises(RiverError):
            core.answer(self.c, q["id"], "self", actor="bob")

    def test_lease_expiry_sends_notice(self):
        core.claim(self.c, self.x, "alice")
        past = core.iso(core.now() - timedelta(minutes=1))
        self.c.execute("UPDATE items SET lease_expires_at=? WHERE id=?", (past, self.x))
        core.activity(self.c, "bob")
        box = core.inbox(self.c, "alice")
        self.assertEqual([(m["kind"], m["from_agent"], m["item_id"]) for m in box], [("notice", "river", self.x)])


class Targets(Base):
    def test_target_groups_projects(self):
        core.target_add(self.c, "web", "rsync, then restart")
        core.project_add(self.c, "site", target="web")
        core.project_add(self.c, "api")
        core.project_target(self.c, "api", "web")
        core.project_add(self.c, "tool")
        t = core.target_show(self.c, "web")
        self.assertEqual([p["name"] for p in t["projects"]], ["site", "api"])
        self.assertEqual(t["description"], "rsync, then restart")
        self.assertIsNone(t["owner"])
        self.assertEqual([(t["name"], t["projects"]) for t in core.target_list(self.c)], [("web", 2)])
        core.project_target(self.c, "api", None)
        self.assertEqual([p["name"] for p in core.target_show(self.c, "web")["projects"]], ["site"])

    def test_refusals(self):
        core.project_add(self.c, "a")
        with self.assertRaises(RiverError):
            core.project_target(self.c, "a", "nowhere")
        core.target_add(self.c, "web")
        with self.assertRaises(RiverError):
            core.target_add(self.c, "web")
        with self.assertRaises(RiverError):
            core.target_add(self.c, "Bad Name")

    def test_old_database_gains_target_column(self):
        self.c.close()
        import sqlite3
        raw = sqlite3.connect(self.path)
        raw.executescript("DROP TABLE projects; CREATE TABLE projects (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, "
                          "rank INTEGER NOT NULL, notes TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0, "
                          "created_at TEXT NOT NULL);")
        raw.close()
        self.c = core.connect(self.path)
        cols = {r["name"] for r in self.c.execute("PRAGMA table_info(projects)")}
        self.assertTrue({"path", "target"} <= cols)


if __name__ == "__main__":
    unittest.main()
