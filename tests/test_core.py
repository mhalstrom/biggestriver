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

    def test_timed_blocker_ends_by_itself(self):
        core.project_add(self.c, "a")
        core.register(self.c, "sam", human=True)
        core.register(self.c, "ag")
        x = self.add("a", "file the EIN form", doer="human")
        y = self.add("a", "agent step")
        core.block(self.c, x, "IRS form closed", "ag", until="2m")
        core.block(self.c, y, None, "ag", until="3h")
        a = core.annotate(self.c)
        self.assertFalse(a[x]["ready"])
        self.assertTrue(a[x]["blocked_text"].startswith("blocked until "))
        self.assertEqual(a[y]["blocked_reason"], "waiting for a set time")
        self.assertEqual(core.needs_you(self.c), [])
        with self.assertRaises(RiverError):
            core.block(self.c, x, "x", "ag", until="2020-01-01T00:00Z")
        with self.assertRaises(RiverError):
            core.block(self.c, x, None, "ag")
        # Time passes: the sweep that every command runs clears both blockers.
        past = core.iso(core.now() - timedelta(seconds=1))
        self.c.execute("UPDATE items SET blocked_until=?", (past,))
        core.activity(self.c, "ag")
        a = core.annotate(self.c)
        self.assertTrue(a[x]["ready"] and a[y]["ready"])
        self.assertIsNone(a[x]["blocked_until"])
        self.assertEqual([n["item_id"] for n in core.needs_you(self.c)], [x])
        notes = self.c.execute("SELECT to_agent, body FROM messages WHERE kind='notice'").fetchall()
        self.assertEqual([(n["to_agent"], n["body"].split(" ended")[0]) for n in notes],
                         [("ag", f"the wait on #{y} agent step")])

    def test_parse_when(self):
        start = core.parse_iso("2026-09-27T15:00:00Z")  # a Sunday
        w = lambda s: core.iso(core.parse_when(s, start=start))
        self.assertEqual(w("2h"), "2026-09-27T17:00:00Z")
        self.assertEqual(w("mon 07:00 America/New_York"), "2026-09-28T11:00:00Z")
        self.assertEqual(w("Monday 7am America/New_York"), "2026-09-28T11:00:00Z")
        self.assertEqual(w("2026-09-28T07:00-04:00"), "2026-09-28T11:00:00Z")
        self.assertEqual(w("tomorrow 9 UTC"), "2026-09-28T09:00:00Z")
        self.assertEqual(w("sun 10:00 UTC"), "2026-10-04T10:00:00Z")  # today's has passed
        self.assertEqual(w("16:00 UTC"), "2026-09-27T16:00:00Z")
        for bad in ("soon", "25:00", "mon 07:00 Mars/Base"):
            with self.assertRaises(RiverError):
                w(bad)
        self.assertEqual(core.show_time("2026-09-28T11:00:00Z", "America/New_York")[-9:], " 7:00 EDT")


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

    def test_auto_continue_setting(self):
        self.add("web", "x")
        self.assertTrue(core.go(self.c, self.web)["auto_continue"])
        core.config_set(self.c, "auto_continue", "off")
        self.assertFalse(core.go(self.c, self.web)["auto_continue"])
        with self.assertRaises(RiverError):
            core.config_set(self.c, "auto_continue", "maybe")

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

    def test_alert_accept_claims_now_or_keeps_for_later(self):
        found = self.add("a", "found work")
        m = core.message(self.c, "alert", "you need this", to="alice", item=found, actor="bob")[0]
        core.inbox(self.c, "alice")  # reading an alert does not answer it
        core.accept_message(self.c, m["id"], "alice")  # alice holds nothing: she takes it now
        self.assertEqual(core._item(self.c, found)["assignee"], "alice")
        self.assertEqual(core.message_show(self.c, m["id"])["state"], "accepted")
        later = self.add("a", "later work")
        m2 = core.message(self.c, "alert", "and this", holder_of=found, item=later, actor="bob")[0]
        self.assertEqual(m2["to_agent"], "alice")
        core.accept_message(self.c, m2["id"], "alice")  # busy: kept for after the current item
        it = core._item(self.c, later)
        self.assertEqual((it["status"], it["reserved_for"], it["reserved_by"]), ("open", "alice", "bob"))
        self.assertEqual([r["body"][:10] for r in core.inbox(self.c, "bob")], ["yes to you", "yes to you"])
        q = core.message(self.c, "question", "why?", to="bob", actor="alice")[0]
        with self.assertRaises(RiverError):
            core.accept_message(self.c, q["id"], "bob")  # only alerts
        with self.assertRaises(RiverError):
            core.message(self.c, "alert", "x", holder_of=self.x, actor="bob")  # nobody holds x

    def test_ask_by_file_and_prerequisite_done_notice(self):
        core.project_add(self.c, "p", path=self.dir.name)
        pre = core.item_add(self.c, "p", "pre", actor="t")["id"]
        big = core.item_add(self.c, "p", "big", actor="t", touches="app.py")["id"]
        core.claim(self.c, big, "alice")
        core.dep_add(self.c, big, [pre], "t")
        qs = core.message(self.c, "question", "is app.py yours?", file="app.py", actor="bob")
        self.assertEqual([q["to_agent"] for q in qs], ["alice"])
        core.claim(self.c, pre, "bob")
        core.done(self.c, pre, "made the table", "bob")
        notes = [m["body"] for m in core.inbox(self.c, "alice") if m["kind"] == "notice"]
        self.assertEqual(len(notes), 1)
        self.assertIn(f"#{pre} pre is done (output: made the table); your #{big} waits on it", notes[0])

    def test_unanswered_question_nudges(self):
        q = core.send(self.c, "question", "why?", to="alice", actor="bob")
        self.assertEqual(core.unread(self.c, "alice")["questions_waiting"], 0)
        old = core.iso(core.now() - timedelta(hours=2))
        self.c.execute("UPDATE messages SET created_at=? WHERE id=?", (old, q["id"]))
        self.assertEqual(core.unread(self.c, "alice")["questions_waiting"], 1)
        core.activity(self.c, "bob")  # alice is active: no notice
        self.assertEqual([m for m in core.inbox(self.c, "bob") if m["kind"] == "notice"], [])
        self.c.execute("UPDATE agents SET last_seen=? WHERE name='alice'", (old,))
        core.activity(self.c, "bob")
        core.activity(self.c, "bob")
        notes = [m["body"] for m in core.inbox(self.c, "bob") if m["kind"] == "notice"]
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("alice is away"))

    def test_unread_message_to_gone_agent_tells_sender_once(self):
        m = core.send(self.c, "alert", "look at this", to="alice", actor="bob")
        self.c.execute("UPDATE agents SET last_seen=? WHERE name='alice'", (core.iso(core.now() - timedelta(hours=2)),))
        core.activity(self.c, "bob")  # away is not gone
        self.assertEqual([x for x in core.inbox(self.c, "bob") if x["kind"] == "notice"], [])
        self.c.execute("UPDATE agents SET last_seen=? WHERE name='alice'", (core.iso(core.now() - timedelta(days=2)),))
        core.activity(self.c, "bob")
        core.activity(self.c, "bob")
        notes = [x["body"] for x in core.inbox(self.c, "bob") if x["kind"] == "notice"]
        self.assertEqual(len(notes), 1)
        self.assertIn(f"never read your alert #{m['id']}", notes[0])

    def test_question_to_item_reaches_next_holder_and_answer_closes_it(self):
        q = core.send(self.c, "question", "why?", item=self.x, actor="bob")
        self.assertIsNone(q["to_agent"])
        self.assertEqual(core.unread(self.c, "alice")["unread"], 0)
        core.claim(self.c, self.x, "alice")
        self.assertEqual({k: v for k, v in core.unread(self.c, "alice").items() if k in ("unread", "alerts", "questions")}, {"unread": 1, "alerts": 0, "questions": 1})
        self.assertEqual([m["id"] for m in core.inbox(self.c, "alice")], [q["id"]])
        # Read, but still waiting for an answer, so it stays in the inbox.
        self.assertEqual({k: v for k, v in core.unread(self.c, "alice").items() if k in ("unread", "alerts", "questions")}, {"unread": 0, "alerts": 0, "questions": 1})
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


class Log(Base):
    def test_completed_by_day_with_progress(self):
        core.project_add(self.c, "a")
        core.project_add(self.c, "b")
        core.register(self.c, "ag")
        x, y, z = self.add("a", "x"), self.add("a", "y"), self.add("b", "z")
        dropped = self.add("a", "d")
        core.claim(self.c, x, "ag")
        core.done(self.c, x, "commit abc", "ag")
        core.done(self.c, z, None, "t")
        core.drop(self.c, dropped, "t")
        old = core.iso(core.now() - timedelta(days=10))
        self.c.execute("UPDATE items SET closed_at=? WHERE id=?", (old, z))
        log = core.completed(self.c)
        self.assertEqual([i["id"] for i in log["items"]], [x])
        self.assertEqual(log["items"][0]["by_agent"], "ag")
        self.assertEqual(log["items"][0]["output"], "commit abc")
        self.assertEqual(log["by_day"][0]["day"], log["items"][0]["closed_at"][:10])
        prog = {p["project"]: p for p in log["progress"]}
        self.assertEqual((prog["a"]["done"], prog["a"]["total"], prog["a"]["done_in_window"]), (1, 2, 1))
        self.assertEqual((prog["b"]["done"], prog["b"]["done_in_window"]), (1, 0))
        every = core.completed(self.c, since=None)
        self.assertEqual({i["id"] for i in every["items"]}, {x, z})
        self.assertEqual([i["id"] for i in core.completed(self.c, "b", None)["items"]], [z])
        self.assertNotIn(y, {i["id"] for i in every["items"]})
        with self.assertRaises(RiverError):
            core.completed(self.c, since="soon")


class Context(Base):
    def test_context_touches_check(self):
        core.project_add(self.c, "a")
        i = core.item_add(self.c, "a", "x", context="why", touches=["b.py", "a.py", "b.py", " "], check="make test")
        self.assertEqual((i["context"], i["touches"], i["check"]), ("why", ["b.py", "a.py"], "make test"))
        core.item_edit(self.c, i["id"], touches="c.py, d.py")
        self.assertEqual(core.item_show(self.c, i["id"])["touches"], ["c.py", "d.py"])
        core.item_edit(self.c, i["id"], touches=[], check="")
        got = core.item_show(self.c, i["id"])
        self.assertEqual((got["touches"], got["check"], got["context"]), ([], "", "why"))
        self.assertEqual(core.next_item(self.c)[0]["context"], "why")

    def test_old_database_gains_context_columns(self):
        self.c.close()
        import sqlite3
        raw = sqlite3.connect(self.path)
        for col in ("context", "touches", "check"):
            raw.execute(f'ALTER TABLE items DROP COLUMN "{col}"')
        raw.commit()
        raw.close()
        self.c = core.connect(self.path)
        cols = {r["name"] for r in self.c.execute("PRAGMA table_info(items)")}
        self.assertTrue({"context", "touches", "check"} <= cols)


class Kinds(Base):
    def setUp(self):
        super().setUp()
        core.project_add(self.c, "a")
        core.register(self.c, "ag")
        core.register(self.c, "bo")

    def test_overlapping_touches_conflict_automatically(self):
        api = core.item_add(self.c, "a", "api", touches=["src/api.py"])["id"]
        whole = core.item_add(self.c, "a", "src dir", touches=["src/"])["id"]
        docs = core.item_add(self.c, "a", "docs", touches=["README.md"])["id"]
        ann = core.annotate(self.c)
        self.assertEqual(ann[whole]["conflicts"], [api])
        self.assertEqual(ann[docs]["conflicts"], [])
        core.claim(self.c, api, "ag")
        ann = core.annotate(self.c)
        self.assertFalse(ann[whole]["ready"])
        self.assertEqual(ann[whole]["busy_conflicts"], [api])
        self.assertTrue(ann[docs]["ready"])
        with self.assertRaises(RiverError):
            core.claim(self.c, whole, "bo")
        self.assertEqual([a["id"] for a in core.ready_list(self.c)], [docs])
        core.done(self.c, api, "ok", "ag")
        self.assertTrue(core.annotate(self.c)[whole]["ready"])

    def test_add_with_feeds(self):
        first = core.item_add(self.c, "a", "make the table")["id"]
        other = core.item_add(self.c, "a", "other")["id"]
        n = core.item_add(self.c, "a", "use the table", after=[other], feeds=[first])["id"]
        show = core.item_show(self.c, n)
        self.assertEqual({d["id"]: d["kind"] for d in show["waits_on_detail"]}, {first: "feeds", other: "blocks"})

    def test_touches_compare_within_each_project_folder(self):
        d = self.dir.name
        core.project_add(self.c, "site1", path=os.path.join(d, "one"))
        core.project_add(self.c, "site2", path=os.path.join(d, "two"))
        core.project_add(self.c, "same", path=os.path.join(d, "one"))  # a second project in the same folder
        core.project_add(self.c, "nofolder")
        core.project_add(self.c, "nofolder2")
        a = core.item_add(self.c, "site1", "a", touches="public/")["id"]
        b = core.item_add(self.c, "site2", "b", touches="public/index.html")["id"]
        c = core.item_add(self.c, "same", "c", touches="public/index.html")["id"]
        e = core.item_add(self.c, "nofolder", "e", touches="public/")["id"]
        f = core.item_add(self.c, "nofolder2", "f", touches="public/x")["id"]
        g = core.item_add(self.c, "nofolder", "g", touches="public/x")["id"]
        h = core.item_add(self.c, "site2", "h", touches=os.path.join(d, "one", "public", "y"))["id"]
        ann = core.annotate(self.c)
        self.assertEqual(ann[a]["conflicts"], [c, h])  # same folder, and an absolute path into it
        self.assertEqual(ann[b]["conflicts"], [])  # another repository's public/
        self.assertEqual(ann[e]["conflicts"], [g])  # no folder: only its own project
        self.assertEqual(ann[f]["conflicts"], [])

    def test_touch_edit_adds_and_removes_auto_conflicts(self):
        x = core.item_add(self.c, "a", "x", touches=["a.py"])["id"]
        y = core.item_add(self.c, "a", "y", touches=["b.py"])["id"]
        self.assertEqual(core.annotate(self.c)[x]["conflicts"], [])
        core.item_edit(self.c, y, touches=["a.py"])
        self.assertEqual(core.annotate(self.c)[x]["conflicts"], [y])
        core.item_edit(self.c, y, touches=["c.py"])
        self.assertEqual(core.annotate(self.c)[x]["conflicts"], [])

    def test_manual_conflict_and_order_replaces_it(self):
        x, y = self.add("a", "x"), self.add("a", "y")
        core.dep_add(self.c, x, [y], kind="conflicts")
        self.assertEqual(core.annotate(self.c)[y]["conflicts"], [x])
        self.assertEqual(core.annotate(self.c)[y]["waits_on"], [])
        core.dep_add(self.c, y, [x])  # an order between them replaces the conflict link
        ann = core.annotate(self.c)
        self.assertEqual((ann[y]["conflicts"], ann[y]["waits_on"]), ([], [x]))
        with self.assertRaises(RiverError):
            core.dep_add(self.c, x, [y], kind="conflicts")
        core.dep_remove(self.c, y, [x])
        core.dep_add(self.c, y, [x], kind="conflicts")
        core.dep_remove(self.c, x, [y])  # either side removes a conflict
        self.assertEqual(core.annotate(self.c)[y]["conflicts"], [])

    def test_feeds_waits_then_shows_output(self):
        up = self.add("a", "up")
        down = self.add("a", "down")
        core.dep_add(self.c, down, [up], kind="feeds")
        self.assertFalse(core.annotate(self.c)[down]["ready"])
        core.done(self.c, up, "made get_user(id)", "t")
        got = core.item_show(self.c, down)
        self.assertTrue(got["ready"])
        self.assertEqual([(f["id"], f["output"]) for f in got["fed_by_detail"]], [(up, "made get_user(id)")])

    def test_conflicting_ready_items_are_one_slot(self):
        core.item_add(self.c, "a", "x", touches=["f.py"])
        core.item_add(self.c, "a", "y", touches=["f.py"])
        core.item_add(self.c, "a", "z", touches=["g.py"])
        cap = core.capacity(self.c)  # setUp registers two idle agents
        self.assertEqual(cap["spare_slots"] + len(cap["agents_idle"]) - cap["excess_sessions"], 2)
        self.assertEqual(len(cap["ready_for_agents"]), 3)

    def test_conflicts_do_not_count_as_cycles(self):
        x, y = self.add("a", "x"), self.add("a", "y")
        z = self.add("a", "z", after=[x])
        core.dep_add(self.c, z, [y], kind="conflicts")
        core.dep_add(self.c, x, [y])  # no loop: conflicts have no direction
        self.assertEqual(core.annotate(self.c)[x]["waits_on"], [y])


class Ownership(Base):
    def setUp(self):
        super().setUp()
        core.target_add(self.c, "web")
        for n in ("ag", "bo"):
            core.register(self.c, n)

    def test_parallel_own_has_one_winner(self):
        names = [f"w{i}" for i in range(8)]
        for n in names:
            core.register(self.c, n)
        won, refused = [], []
        start = threading.Barrier(len(names))

        def worker(name):
            c = core.connect(self.path)
            try:
                start.wait()
                core.target_own(c, "web", name)
                won.append(name)
            except RiverError as e:
                refused.append(str(e))
            finally:
                c.close()

        ts = [threading.Thread(target=worker, args=(n,)) for n in names]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(won), 1)
        self.assertEqual(len(refused), len(names) - 1)
        self.assertTrue(all(f"owned by {won[0]}" in e for e in refused))
        self.assertEqual(core.target_show(self.c, "web")["owner"], won[0])

    def test_own_is_renewed_and_repeatable(self):
        core.target_own(self.c, "web", "ag")
        self.c.execute("UPDATE targets SET owner_expires_at=? WHERE name='web'",
                       (core.iso(core.now() + timedelta(minutes=5)),))
        core.activity(self.c, "ag")
        left = core.parse_iso(core.target_show(self.c, "web")["owner_expires_at"]) - core.now()
        self.assertGreater(left, timedelta(hours=7))
        core.target_own(self.c, "web", "ag")  # owning again is fine

    def test_expiry_frees_target_and_notifies(self):
        core.target_own(self.c, "web", "ag")
        self.c.execute("UPDATE targets SET owner_expires_at=? WHERE name='web'",
                       (core.iso(core.now() - timedelta(minutes=1)),))
        core.target_own(self.c, "web", "bo")
        self.assertEqual(core.target_show(self.c, "web")["owner"], "bo")
        box = core.inbox(self.c, "ag")
        self.assertEqual([(m["kind"], m["from_agent"]) for m in box], [("notice", "river")])
        self.assertIn("web", box[0]["body"])

    def test_give_release_and_refusals(self):
        with self.assertRaises(RiverError):
            core.target_give(self.c, "web", "bo", "ag")  # nobody owns it
        core.target_own(self.c, "web", "ag")
        with self.assertRaises(RiverError):
            core.target_release(self.c, "web", "bo")
        with self.assertRaises(RiverError):
            core.unregister(self.c, "ag")
        core.target_give(self.c, "web", "bo", "ag")
        self.assertEqual(core.target_show(self.c, "web")["owner"], "bo")
        self.assertEqual(core.unread(self.c, "bo")["unread"], 1)
        self.assertEqual([o["name"] for o in core.agent_status(self.c, "bo")["owns"]], ["web"])
        core.target_release(self.c, "web", "bo")
        self.assertIsNone(core.target_show(self.c, "web")["owner"])


class Status(Base):
    def test_status_counts_and_recent(self):
        core.project_add(self.c, "a")
        core.register(self.c, "ag")
        x, y = self.add("a", "x"), self.add("a", "y")
        h = self.add("a", "sign", doer="human")
        w = self.add("a", "later", after=[y])
        core.claim(self.c, x, "ag")
        core.done(self.c, x, "ok", "ag")
        core.claim(self.c, y, "ag")
        st = core.status(self.c)
        row = st["projects"][0]
        self.assertEqual({k: row[k] for k in ("done", "open", "ready", "in_progress", "human_waiting", "blocked")},
                         {"done": 1, "open": 3, "ready": 1, "in_progress": 1, "human_waiting": 1, "blocked": 1})
        self.assertEqual([r["id"] for r in st["recent"]], [x])
        self.assertEqual([h_["id"] for h_ in st["human_waiting"]], [h])
        self.assertEqual([a["holds"][0]["id"] for a in st["agents"] if a["name"] == "ag"], [y])
        self.assertNotEqual(w, h)


class Ship(Base):
    def setUp(self):
        super().setUp()
        core.target_add(self.c, "web", "rsync, then smoke test")
        core.project_add(self.c, "site", target="web")
        core.project_add(self.c, "api", target="web")
        for n in ("dev", "ops"):
            core.register(self.c, n)

    def test_two_projects_share_one_deploy_item_only_owner_claims(self):
        a = self.add("site", "page", p=1)
        b = self.add("api", "endpoint")
        core.done(self.c, a, "abc", "t", ship_it=True)
        d1 = core.ship(self.c, b, "dev")
        self.assertEqual(d1["kind"], "deploy")
        self.assertEqual(d1["project"], "deploy-web")
        self.assertEqual(d1["waits_on"], sorted([a, b]))
        self.assertEqual(d1["priority"], 1)
        self.assertEqual(d1["context"], "rsync, then smoke test")
        core.done(self.c, b, "def", "t")
        self.assertEqual([x["id"] for x in core.next_item(self.c, actor="dev")], [])
        with self.assertRaises(RiverError):
            core.claim(self.c, d1["id"], "dev")
        core.target_own(self.c, "web", "ops")
        self.assertEqual([x["id"] for x in core.next_item(self.c, actor="ops")], [d1["id"]])
        core.claim(self.c, d1["id"], "ops")
        c = self.add("site", "later")
        d2 = core.ship(self.c, c, "dev")  # the first deploy item is taken: a new one starts
        self.assertNotEqual(d2["id"], d1["id"])
        done = core.done(self.c, d1["id"], "release r-42", "ops")
        self.assertEqual([x["id"] for x in done["ships"]], sorted([a, b]))
        self.assertEqual(core.unread(self.c, "ops")["unread"], 1)  # notice for the request made while ops owned it
        t = core.state(self.c)["targets"][0]
        self.assertEqual((t["name"], t["owner"], t["project_names"]), ("web", "ops", ["site", "api"]))
        self.assertEqual([(d["id"], [x["id"] for x in d["ships"]]) for d in t["pending"]], [(d2["id"], [c])])
        self.assertEqual((t["last_deploy"]["id"], t["last_deploy"]["output"]), (d1["id"], "release r-42"))
        self.assertEqual([x["id"] for x in t["last_deploy"]["ships"]], sorted([a, b]))

    def test_ship_refusals_and_repeat(self):
        core.project_add(self.c, "misc")
        with self.assertRaises(RiverError):
            core.ship(self.c, self.add("misc", "x"), "dev")
        a = self.add("site", "page")
        d = core.ship(self.c, a, "dev")
        self.assertEqual(core.ship(self.c, a, "dev")["id"], d["id"])
        with self.assertRaises(RiverError):
            core.ship(self.c, d["id"], "dev")


class Plan(Base):
    def test_plan_brief_refuses_claims_and_go_switches_back(self):
        core.project_add(self.c, "a", notes="thing")
        core.project_add(self.c, "b")
        x = self.add("a", "x")
        b = core.plan(self.c, self.dir.name)
        me = b["agent"]
        self.assertTrue(me.startswith("planner-"))
        q = b["questions"]
        self.assertEqual(q["projects_without_description"], ["b"])
        self.assertEqual([i["id"] for i in q["items_without_notes"]], [x])
        self.assertEqual(core.agent_status(self.c, me)["role"], "planner")
        self.assertEqual(core.capacity(self.c)["agents_idle"], [])
        with self.assertRaises(RiverError):
            core.claim(self.c, x, me)
        core.project_path(self.c, "a", self.dir.name)
        g = core.go(self.c, self.dir.name, me)
        self.assertEqual((g["role"], g["item"]["id"]), ("worker", x))

    def test_plan_refuses_a_session_that_holds_work(self):
        core.project_add(self.c, "a")
        core.register(self.c, "ag")
        core.claim(self.c, self.add("a", "x"), "ag")
        with self.assertRaises(RiverError):
            core.plan(self.c, self.dir.name, "ag")


class DueDates(Base):
    def test_due_inherits_warns_once_per_stage_and_never_reorders(self):
        core.project_add(self.c, "a")
        core.register(self.c, "mark", human=True)
        pre = self.add("a", "prerequisite", p=3)
        other = self.add("a", "other", p=2)
        goal = core.item_add(self.c, "a", "outcome", 3, after=[pre], due="2099-01-01 UTC")["id"]
        ann = core.annotate(self.c)
        self.assertEqual((ann[pre]["effective_due"], ann[pre]["due_from"]), ("2099-01-01T23:59:00Z", goal))
        self.assertIsNone(ann[pre]["due_state"])
        self.assertEqual([x["id"] for x in core.ready_list(self.c)], [other, pre])  # order unchanged
        core.item_edit(self.c, goal, due=core.iso(core.now() + timedelta(days=1)))  # inside due_warn_before
        core.activity(self.c, "mark")
        core.activity(self.c, "mark")
        alerts = [m["body"] for m in core.inbox(self.c, "mark") if m["kind"] == "alert"]
        self.assertEqual(len(alerts), 1)
        self.assertIn(f"#{goal} outcome is due soon", alerts[0])
        self.assertIn(f"Still open before it: #{pre}", alerts[0])
        self.assertEqual(core.annotate(self.c)[pre]["due_state"], "soon")
        self.c.execute("UPDATE items SET due=? WHERE id=?", (core.iso(core.now() - timedelta(minutes=1)), goal))
        core.activity(self.c, "mark")
        alerts = [m["body"] for m in core.inbox(self.c, "mark") if m["kind"] == "alert"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("is past its due date", alerts[0])
        self.assertEqual(core.status(self.c)["due"][0]["due_state"], "overdue")
        core.item_edit(self.c, goal, due="none")
        self.assertIsNone(core._item(self.c, goal)["due"])

    def test_parse_due(self):
        self.assertEqual(core.parse_due("2026-10-15 UTC"), "2026-10-15T23:59:00Z")
        self.assertEqual(core.parse_due("2026-10-15 17:00 UTC"), "2026-10-15T17:00:00Z")
        self.assertIsNone(core.parse_due("none"))


class EditHistory(Base):
    def test_save_without_changes_logs_nothing(self):
        core.project_add(self.c, "a")
        x = self.add("a", "x")
        before = self.c.execute("SELECT COUNT(*) FROM events WHERE item_id=?", (x,)).fetchone()[0]
        core.item_edit(self.c, x, title="x", notes="", doer="any", project="a", context="", touches=[], check="")
        after = self.c.execute("SELECT COUNT(*) FROM events WHERE item_id=?", (x,)).fetchone()[0]
        self.assertEqual(after, before)
        core.item_edit(self.c, x, title="y", doer="ai")
        changes = [r[0] for r in self.c.execute("SELECT change FROM events WHERE item_id=? ORDER BY id", (x,))][-2:]
        self.assertEqual(changes, ["title changed", "doer any -> ai"])


class PlanFile(Base):
    PLAN = """# Launch
- P0 Live test order (human)
  - Deploy the site (human)
    1. Menu page
    2. [ ] Preorder form (ai)
        - Friday list
    3. [x] Pick a host
        - Stale child

> a quote
"""

    def test_parse_plan(self):
        rows = core.parse_plan(self.PLAN)
        self.assertEqual([(r["title"], r["priority"], r["doer"], r["parent"]) for r in rows], [
            ("Live test order", 0, "human", None), ("Deploy the site", 2, "human", 0), ("Menu page", 2, "any", 1),
            ("Preorder form", 2, "ai", 1), ("Friday list", 2, "any", 3), ("Stale child", 2, "any", 1)])
        with self.assertRaises(RiverError):
            core.parse_plan("# only a heading\n")

    def test_add_plan_links_children_to_parents(self):
        core.project_add(self.c, "shop")
        res = core.add_plan(self.c, "shop", self.PLAN, "t")
        ids = {r["title"]: r["id"] for r in res["items"]}
        ann = core.annotate(self.c)
        self.assertEqual(ann[ids["Live test order"]]["waits_on"], [ids["Deploy the site"]])
        self.assertEqual(ann[ids["Deploy the site"]]["waits_on"],
                         sorted([ids["Menu page"], ids["Preorder form"], ids["Stale child"]]))
        self.assertEqual(ann[ids["Preorder form"]]["waits_on"], [ids["Friday list"]])
        self.assertEqual(ann[ids["Friday list"]]["effective_priority"], 0)  # the outcome's P0 flows down
        self.assertEqual(core.add_plan(self.c, "shop", "a\n  b\n", dry_run=True)["items"][1]["parent"], 0)
        self.assertEqual(len(core.item_list(self.c, "shop")), 6)


class Goals(Base):
    def setUp(self):
        super().setUp()
        core.project_add(self.c, "shop")
        core.register(self.c, "ag")
        core.register(self.c, "bo")

    def test_goal_lifecycle(self):
        core.goal_add(self.c, "shop", "checkout", "customers can pay", "a test order succeeds", "t")
        core.goal_add(self.c, "shop", "speed", "pages load in 1s", actor="t")
        core.goal_rank(self.c, "speed", 1)
        self.assertEqual([g["name"] for g in core.goal_list(self.c)], ["speed", "checkout"])
        core.goal_own(self.c, "checkout", "ag")
        with self.assertRaises(RiverError):
            core.goal_own(self.c, "checkout", "bo")  # one owner at a time
        a = core.item_add(self.c, "shop", "cart", actor="ag")["id"]  # tagged with the goal ag owns
        b = core.item_add(self.c, "shop", "pay", actor="bo", goals=["checkout", "speed"])["id"]
        c = core.item_add(self.c, "shop", "untagged", actor="bo")["id"]
        self.assertEqual(core.annotate(self.c)[a]["goals"], ["checkout"])
        self.assertEqual([x["id"] for x in core.item_list(self.c, goal="checkout")], [a, b])
        core.item_edit(self.c, b, untag=["speed"])
        core.item_edit(self.c, c, goals=["speed"])
        g = core.goal_show(self.c, "checkout")
        self.assertEqual((g["owner"], g["items_open"]), ("ag", [a, b]))
        with self.assertRaises(RiverError):
            core.goal_done(self.c, "checkout", "shipped", "ag")  # items still open
        with self.assertRaises(RiverError):
            core.goal_done(self.c, "checkout", "shipped", "bo")  # not the owner
        core.claim(self.c, a, "ag")
        core.done(self.c, a, "ok", "ag")
        g = core.goal_done(self.c, "checkout", "cart works; pay dropped for now", "ag", drop_open=True)
        self.assertEqual((g["status"], g["owner"], g["items_dropped"]), ("complete", None, [b]))
        self.assertEqual([x["name"] for x in core.project_show(self.c, "shop")["goals"]], ["speed"])
        core.goal_reopen(self.c, "checkout")
        self.assertEqual(core.goal_show(self.c, "checkout")["status"], "open")

    def test_owner_lease_expires_with_notice(self):
        core.goal_add(self.c, "shop", "g1", actor="t")
        core.goal_own(self.c, "g1", "ag")
        self.c.execute("UPDATE goals SET owner_expires_at=?", (core.iso(core.now() - timedelta(minutes=1)),))
        core.activity(self.c, "bo")
        self.assertIsNone(core.goal_show(self.c, "g1")["owner"])
        self.assertIn("goal g1 expired", core.inbox(self.c, "ag")[0]["body"].replace("ownership of ", ""))


class Sessions(Base):
    def test_session_name_recorded_and_shown(self):
        core.project_add(self.c, "a", path=self.dir.name)
        core.register(self.c, "ag", session="toolscaledcore-60")
        self.assertEqual(core.who(self.c)[0]["session"], "toolscaledcore-60")
        b = core.go(self.c, self.dir.name)
        self.assertIsNone(b["session"])  # a new agent: the briefing asks for it
        b = core.go(self.c, self.dir.name, actor=b["agent"], session="web-7")
        self.assertEqual(b["session"], "web-7")
        with self.assertRaises(RiverError):
            core.set_session(self.c, "ag", "two words")
        r = core.set_session(self.c, "ag", "toolscaledcore-90 [46d1d3]")
        self.assertEqual((r["session"], r["session_ref"]), ("toolscaledcore-90", "46d1d3"))
        r = core.set_session(self.c, "ag", "toolscaledcore-90", ref="922c83")
        self.assertEqual(r["session_ref"], "922c83")


class WhoFile(Base):
    def test_who_file_matches_held_touches(self):
        root = self.dir.name
        core.project_add(self.c, "a", path=root)
        core.register(self.c, "ag")
        core.register(self.c, "bo")
        x = core.item_add(self.c, "a", "core work", actor="ag", touches="river/core.py, tests/")["id"]
        y = core.item_add(self.c, "a", "page work", actor="bo", touches="river/static/index.html")["id"]
        core.item_add(self.c, "a", "open, not held", actor="bo", touches="river/core.py")
        core.claim(self.c, x, "ag")
        core.claim(self.c, y, "bo")
        names = lambda f, cwd=None: [(a["name"], [t["id"] for t in a["touching"]]) for a in core.who(self.c, file=f, cwd=cwd)]
        self.assertEqual(names("river/core.py"), [("ag", [x])])
        self.assertEqual(names("tests/test_core.py"), [("ag", [x])])  # inside a touched directory
        self.assertEqual(names("river"), [("ag", [x]), ("bo", [y])])  # a directory holds touched files
        self.assertEqual(names(os.path.join(root, "river", "core.py")), [("ag", [x])])
        self.assertEqual(names("core.py", cwd=os.path.join(root, "river")), [("ag", [x])])
        self.assertEqual(names("/elsewhere/river/core.py"), [])
        self.assertEqual(names("README.md"), [])


class KeepRelease(Base):
    def setUp(self):
        super().setUp()
        core.project_add(self.c, "a", path=self.dir.name)
        core.register(self.c, "ag")
        core.register(self.c, "bo")
        self.p = self.add("a", "parent")
        core.claim(self.c, self.p, "ag")

    def test_keep_reserves_and_resumes(self):
        n = core.item_add(self.c, "a", "fix", actor="ag", blocks=self.p, mode="keep")["id"]
        st = core._item(self.c, self.p)
        self.assertEqual((st["status"], st["assignee"]), ("held", "ag"))
        self.assertEqual(core._item(self.c, n)["reserved_for"], "ag")
        self.assertEqual(core.next_item(self.c, actor="bo"), [])
        with self.assertRaises(RiverError):
            core.claim(self.c, n, "bo")
        g = core.go(self.c, self.dir.name, "ag")  # max_leases 1: the hold does not use it up
        self.assertEqual(g["item"]["id"], n)
        res = core.done(self.c, n, "ok", "ag")
        self.assertEqual(res["resumed"], [self.p])
        st = core._item(self.c, self.p)
        self.assertEqual((st["status"], st["assignee"]), ("in_progress", "ag"))
        self.assertIsNotNone(st["lease_expires_at"])

    def test_default_mode_releases(self):
        n = core.item_add(self.c, "a", "big", actor="ag", blocks=self.p)["id"]
        st = core._item(self.c, self.p)
        self.assertEqual((st["status"], st["assignee"]), ("open", None))
        self.assertIsNone(core._item(self.c, n)["reserved_for"])
        self.assertEqual([x["id"] for x in core.next_item(self.c, actor="bo")], [n])

    def test_keep_over_limit_releases_and_marks_replan(self):
        core.config_set(self.c, "keep_prereq_limit", "1")
        core.item_add(self.c, "a", "one", actor="ag", blocks=self.p, mode="keep")
        core.item_add(self.c, "a", "two", actor="ag", blocks=self.p, mode="keep")
        st = core._item(self.c, self.p)
        self.assertEqual((st["status"], st["replan"]), ("open", 1))
        self.assertEqual([r for r in self.c.execute("SELECT id FROM items WHERE reserved_for IS NOT NULL")], [])

    def test_replan_threshold_marks_item_with_late_prerequisites(self):
        core.config_set(self.c, "replan_threshold", "2")
        core.item_add(self.c, "a", "one", actor="ag", blocks=self.p, mode="keep")
        self.assertEqual(core._item(self.c, self.p)["replan"], 0)
        core.item_add(self.c, "a", "two", actor="ag", blocks=self.p, mode="keep")
        st = core._item(self.c, self.p)
        self.assertEqual((st["status"], st["replan"], st["late_prereqs"]), ("held", 1, 2))  # still kept
        q = core.plan(self.c, self.dir.name, "bo")["questions"]
        self.assertEqual([(x["id"], x["late_prereqs"]) for x in q["replan"]], [(self.p, 2)])
        core.replanned(self.c, self.p, "split into two", "ag")
        st = core._item(self.c, self.p)
        self.assertEqual((st["replan"], st["late_prereqs"]), (0, 0))
        # Prerequisites added to an item nobody holds are planning, not late work.
        other = self.add("a", "unclaimed")
        for t in ("x", "y", "z"):
            core.item_add(self.c, "a", t, actor="bo", blocks=other)
        self.assertEqual(core._item(self.c, other)["late_prereqs"], 0)

    def test_hold_expiry_releases_with_notice_and_keep_restores(self):
        n = core.item_add(self.c, "a", "fix", actor="ag", blocks=self.p, mode="keep")["id"]
        self.c.execute("UPDATE items SET hold_expires_at=? WHERE id=?", (core.iso(core.now() - timedelta(minutes=1)), self.p))
        core.activity(self.c, "bo")
        self.assertEqual(core._item(self.c, self.p)["status"], "open")
        self.assertIsNone(core._item(self.c, n)["reserved_for"])
        self.assertEqual([m["kind"] for m in core.inbox(self.c, "ag")], ["notice"])
        core.keep(self.c, self.p, "ag")
        self.assertEqual(core._item(self.c, self.p)["status"], "held")
        self.assertEqual(core._item(self.c, n)["reserved_for"], "ag")
        core.release(self.c, self.p, actor="ag")
        self.assertIsNone(core._item(self.c, n)["reserved_for"])
        core.claim(self.c, n, "bo")
        with self.assertRaises(RiverError):
            core.keep(self.c, self.p, "ag")  # bo holds the prerequisite now

    def test_dep_with_mode_and_without(self):
        other = self.add("a", "existing")
        core.dep_add(self.c, self.p, [other], "ag")  # no mode: the parent stays as it was
        self.assertEqual(core._item(self.c, self.p)["status"], "in_progress")
        core.dep_add(self.c, self.p, [other], "ag", mode="release")
        self.assertEqual(core._item(self.c, self.p)["status"], "open")


class NeedsYou(Base):
    def setUp(self):
        super().setUp()
        core.project_add(self.c, "a")
        core.register(self.c, "mark", human=True)
        core.register(self.c, "ag")

    def sync(self):
        with core.tx(self.c):
            core.sync_needs_you(self.c)

    def test_human_item_opens_when_ready_and_closes_when_claimed(self):
        core.config_set(self.c, "notify_channels", "mac,ntfy")
        prep = self.add("a", "prep")
        sign = self.add("a", "sign", doer="human", after=[prep])
        self.sync()
        self.assertEqual(core.needs_you(self.c), [])
        core.claim(self.c, prep, "ag")
        core.done(self.c, prep, None, "ag")
        self.sync()
        self.sync()  # idempotent: still one event
        ev = core.needs_you(self.c)
        self.assertEqual([(e["kind"], e["item_id"]) for e in ev], [("item", sign)])
        self.assertEqual(sorted(n["channel"] for n in ev[0]["notifications"]), ["mac", "ntfy"])
        self.assertEqual(len(core.outbox(self.c)), 2)
        core.claim(self.c, sign, "mark")
        self.sync()
        self.assertEqual(core.needs_you(self.c), [])
        closed = core.needs_you(self.c, include_closed=True)
        self.assertEqual(closed[0]["close_reason"], "claimed")
        self.assertEqual(core.outbox(self.c), [])  # closed before it was sent: nothing to send

    def test_most_important_first(self):
        low = self.add("a", "low", p=3, doer="human")
        mid = self.add("a", "mid", p=2, doer="human")
        feeds = self.add("a", "feeds a P0", p=3, doer="human")
        top = self.add("a", "top", p=0, after=[feeds])
        q = core.send(self.c, "question", "which plan?", to="mark", actor="ag")
        alert = core.send(self.c, "alert", "disk full", to="mark", actor="ag")
        self.sync()
        ev = core.needs_you(self.c, human="mark")
        self.assertEqual([e["message_id"] or e["item_id"] for e in ev], [q["id"], alert["id"], feeds, mid, low])
        first_item = ev[2]
        self.assertEqual((first_item["priority"], first_item["priority_from"], first_item["unblocks_count"]), (0, top, 1))
        self.assertEqual(ev[4]["priority"], 3)
        self.assertIsNone(ev[0]["priority"])

    def test_question_to_human_and_answer(self):
        q = core.send(self.c, "question", "which plan?", to="mark", actor="ag")
        core.send(self.c, "question", "agents only", to="ag", actor="mark")
        self.sync()
        ev = core.needs_you(self.c, human="mark")
        self.assertEqual([(e["kind"], e["message_id"], e["human"]) for e in ev], [("message", q["id"], "mark")])
        self.assertEqual(core.outbox(self.c), [])  # no channels configured: nothing to send
        core.answer(self.c, q["id"], "B", actor="mark")
        self.sync()
        self.assertEqual(core.needs_you(self.c, human="mark"), [])
        self.assertEqual(core.needs_you(self.c, include_closed=True)[0]["close_reason"], "answered")

    def test_outbox_retry_then_give_up(self):
        core.config_set(self.c, "notify_channels", "ntfy")
        self.add("a", "sign", doer="human")
        self.sync()
        (n,) = core.outbox(self.c)
        core.outbox_mark(self.c, n["id"], False, "timeout")
        self.assertEqual(core.outbox(self.c)[0]["attempts"], 1)
        for _ in range(core.NOTIFY_MAX_ATTEMPTS - 1):
            core.outbox_mark(self.c, n["id"], False, "timeout")
        self.assertEqual(core.outbox(self.c), [])
        with self.assertRaises(RiverError):
            core.config_set(self.c, "notify_channels", "Bad Name")

    def test_sent_once(self):
        core.config_set(self.c, "notify_channels", "mac")
        self.add("a", "sign", doer="human")
        self.sync()
        (n,) = core.outbox(self.c)
        core.outbox_mark(self.c, n["id"], True)
        self.sync()
        self.assertEqual(core.outbox(self.c), [])


class Dispatch(Base):
    def setUp(self):
        super().setUp()
        from river import notify
        self.notify = notify
        self.sent, self.fail = [], []
        notify.register_channel("fake", lambda conn: self._send)
        core.project_add(self.c, "a")
        core.config_set(self.c, "notify_channels", "fake")

    def tearDown(self):
        self.notify.ADAPTERS.pop("fake", None)
        super().tearDown()

    def _send(self, title, body, url):
        if self.fail:
            raise OSError(self.fail[0])
        self.sent.append((title, body, url))

    def age_outbox(self, seconds):
        self.c.execute("UPDATE notifications SET created_at=?", (core.iso(core.now() - timedelta(seconds=seconds)),))

    def test_batches_after_window_and_sends_once(self):
        i = self.add("a", "sign", doer="human")
        self.add("a", "pay", doer="human")
        res = self.notify.run(self.c)
        self.assertEqual((res[0]["sent"], res[0]["rows"]), (False, 2))  # inside the batch window
        self.assertEqual(self.sent, [])
        self.age_outbox(61)
        res = self.notify.run(self.c)
        self.assertTrue(res[0]["sent"])
        self.assertEqual(len(self.sent), 1)
        self.assertIn("2 things need you", self.sent[0][0])
        self.assertEqual(self.notify.run(self.c, now_=True), [])  # nothing left
        self.assertEqual(len(self.sent), 1)
        self.assertNotEqual(i, None)

    def test_single_links_to_item_and_failures_retry(self):
        i = self.add("a", "sign", doer="human")
        self.fail.append("network down")
        res = self.notify.run(self.c, now_=True)
        self.assertIn("network down", res[0]["error"])
        st = self.notify.status(self.c)["channels"][0]
        self.assertEqual((st["channel"], st["pending"]), ("fake", 1))
        self.assertIn("network down", st["last_error"])
        self.fail.clear()
        self.notify.run(self.c, now_=True)
        self.assertEqual(self.sent[0][2], f"http://127.0.0.1:8765/#item-{i}")
        self.assertEqual(self.notify.status(self.c)["channels"][0]["pending"], 0)

    def test_test_and_unknown_channel(self):
        self.assertTrue(self.notify.test(self.c, "fake")["ok"])
        with self.assertRaises(RiverError):
            self.notify.test(self.c, "nope")
        core.config_set(self.c, "notify_batch_window", "0s")
        with self.assertRaises(RiverError):
            core.config_set(self.c, "notify_interval", "often")


class Ntfy(Base):
    def test_setup_masks_and_posts(self):
        from unittest import mock
        from river import notify
        out = notify.setup_ntfy(self.c)
        topic = core.setting(self.c, "ntfy_topic")
        self.assertTrue(topic.startswith("river-") and len(topic) > 12)
        self.assertIn(topic, "\n".join(out["subscribe"]))
        self.assertIn("ntfy", core._channels(core.setting(self.c, "notify_channels")))
        listed = [o for o in core.config_list(self.c)["overrides"] if o["key"] == "ntfy_topic"]
        self.assertNotIn(topic, listed[0]["value"])
        self.assertNotIn(topic, " ".join(e["change"] for e in core.recent_events(self.c)))
        core.config_set(self.c, "ntfy_token", "tk_secret_123")
        seen = []

        class Resp:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake(req, timeout):
            seen.append(req)
            return Resp()

        with mock.patch("urllib.request.urlopen", fake):
            notify.ADAPTERS["ntfy"](self.c)("Überweisung fällig", "body ✓", "http://127.0.0.1:8765/#item-3")
        (req,) = seen
        self.assertEqual(req.full_url, f"https://ntfy.sh/{topic}")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.data, "body ✓".encode())
        self.assertTrue(req.get_header("Title").startswith("=?UTF-8?B?"))
        self.assertEqual(req.get_header("Click"), "http://127.0.0.1:8765/#item-3")
        self.assertEqual(req.get_header("Authorization"), "Bearer tk_secret_123")

    def test_no_topic_and_bad_values(self):
        from river import notify
        with self.assertRaises(RiverError):
            notify.ADAPTERS["ntfy"](self.c)
        with self.assertRaises(RiverError):
            core.config_set(self.c, "ntfy_topic", "has space")
        with self.assertRaises(RiverError):
            core.config_set(self.c, "ntfy_url", "ntfy.sh")


class Email(Base):
    def setUp(self):
        super().setUp()
        from unittest import mock
        from pathlib import Path
        from river import notify
        self.notify, self.mock = notify, mock
        self.pw = Path(self.dir.name) / "smtp-password"
        self._p = mock.patch.object(notify, "PASSWORD_FILE", self.pw)
        self._p.start()
        self._e = mock.patch.dict(os.environ, {}, clear=False)
        self._e.start()
        os.environ.pop("RIVER_SMTP_PASSWORD", None)
        for k, v in (("email_to", "me@example.com"), ("smtp_host", "smtp.example.com"),
                     ("smtp_user", "bot@example.com")):
            core.config_set(self.c, k, v)

    def tearDown(self):
        self._p.stop()
        self._e.stop()
        super().tearDown()

    def test_sends_with_starttls_and_login(self):
        self.pw.write_text("s3cret\n")
        self.pw.chmod(0o600)
        box = []

        class FakeSMTP:
            def __init__(self, host, port, timeout): box.append(("open", host, port))
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def starttls(self): box.append(("starttls",))
            def login(self, u, p): box.append(("login", u, p))
            def send_message(self, m): box.append(("send", m["To"], m["From"], m["Subject"], m.get_content()))

        with self.mock.patch("smtplib.SMTP", FakeSMTP):
            self.notify.ADAPTERS["email"](self.c)("River: needs you", "#3 sign", "http://127.0.0.1:8765/#item-3")
        self.assertEqual(box[0], ("open", "smtp.example.com", 587))
        self.assertEqual(box[1:3], [("starttls",), ("login", "bot@example.com", "s3cret")])
        self.assertEqual(box[3][1:4], ("me@example.com", "bot@example.com", "River: needs you"))
        self.assertIn("#item-3", box[3][4])

    def test_password_rules(self):
        with self.assertRaises(RiverError):
            self.notify.ADAPTERS["email"](self.c)  # user set, no password
        self.pw.write_text("x")
        self.pw.chmod(0o644)
        with self.assertRaises(RiverError):
            self.notify.smtp_password()
        os.environ["RIVER_SMTP_PASSWORD"] = "from-env"
        self.assertEqual(self.notify.smtp_password(), "from-env")
        self.assertNotIn("from-env", str(core.config_list(self.c)))
        with self.assertRaises(RiverError):
            core.config_set(self.c, "email_to", "not an address")

    def test_email_has_its_own_batch_window(self):
        from river import notify
        sent = []
        notify.register_channel("email", lambda conn: lambda t, b, u: sent.append(t))
        try:
            core.project_add(self.c, "a")
            core.config_set(self.c, "notify_channels", "email")
            self.add("a", "sign", doer="human")
            self.c.execute("UPDATE notifications SET created_at=?", (core.iso(core.now() - timedelta(minutes=2)),))
            self.assertFalse(notify.run(self.c)[0]["sent"])  # 2m old, email waits 10m
            self.c.execute("UPDATE notifications SET created_at=?", (core.iso(core.now() - timedelta(minutes=11)),))
            self.assertTrue(notify.run(self.c)[0]["sent"])
        finally:
            notify.register_channel("email", notify._email_channel)


class Mac(Base):
    def run_mac(self, notifier, returncode=0):
        from unittest import mock
        from river import notify
        seen = []

        class Done:
            def __init__(self): self.returncode, self.stderr = returncode, "boom"

        def fake_run(cmd, **kw):
            seen.append(cmd)
            return Done()

        with mock.patch("sys.platform", "darwin"), mock.patch("shutil.which", lambda name: notifier), \
                mock.patch("subprocess.run", fake_run):
            notify.ADAPTERS["mac"](self.c)('Say "yes"', "#3 sign", "http://127.0.0.1:8765/#item-3")
        return seen[0]

    def test_osascript_passes_texts_as_arguments(self):
        cmd = self.run_mac(None)
        self.assertEqual(cmd[0], "osascript")
        self.assertEqual(cmd[-3:], ['Say "yes"', "#3 sign", "http://127.0.0.1:8765/#item-3"])
        self.assertNotIn('Say "yes"', " ".join(cmd[:-3]))

    def test_terminal_notifier_opens_the_page(self):
        cmd = self.run_mac("/opt/homebrew/bin/terminal-notifier")
        self.assertEqual(cmd[0], "/opt/homebrew/bin/terminal-notifier")
        self.assertEqual(cmd[cmd.index("-open") + 1], "http://127.0.0.1:8765/#item-3")

    def test_failure_and_other_platforms(self):
        from unittest import mock
        from river import notify
        with self.assertRaises(OSError):
            self.run_mac(None, returncode=1)
        with mock.patch("sys.platform", "linux"), self.assertRaises(RiverError):
            notify.ADAPTERS["mac"](self.c)


class AddingWork(Base):
    def test_found_during_links_and_project_inference(self):
        core.project_add(self.c, "a", path=self.dir.name)
        core.project_add(self.c, "b")
        src = self.add("b", "work")
        self.assertEqual(core.project_for_add(self.c, "/", related=src), "b")
        self.assertEqual(core.project_for_add(self.c, self.dir.name), "a")
        with self.assertRaises(RiverError):
            core.project_for_add(self.c, "/")
        found = core.item_add(self.c, "b", "odd bug", found_during=src, actor="t")
        self.assertEqual(found["found_during"], src)
        self.assertEqual(found["waits_on"], [])
        self.assertEqual([x["id"] for x in core.item_show(self.c, src)["found_here"]], [found["id"]])
        with self.assertRaises(RiverError):
            core.item_add(self.c, "b", "x", found_during=999)


class Deployer(Base):
    def setUp(self):
        super().setUp()
        core.target_add(self.c, "web", "push, then smoke test")
        core.project_add(self.c, "site", target="web", path=self.dir.name)
        for n in ("dev", "ops"):
            core.register(self.c, n)
        w = self.add("site", "page")
        core.claim(self.c, w, "dev")
        core.done(self.c, w, "abc", "dev", ship_it=True)
        self.work = self.add("site", "other")
        self.deploy = core.item_show(self.c, w)["unblocks"][0]

    def test_owner_gets_deploy_item_first(self):
        core.target_own(self.c, "web", "ops")
        b = core.go(self.c, self.dir.name, "ops")
        self.assertEqual((b["role"], b["item"]["id"]), ("deployer", self.deploy))
        self.assertEqual(b["target"]["name"], "web")
        self.assertEqual([d["title"] for d in b["ships"]], ["page"])
        again = core.go(self.c, self.dir.name, "ops")
        self.assertEqual((again["role"], again.get("resumed")), ("deployer", True))

    def test_role_deployer_takes_a_free_target(self):
        b = core.go(self.c, self.dir.name, "ops", role="deployer")
        self.assertEqual((b["role"], b["item"]["id"]), ("deployer", self.deploy))
        self.assertEqual(core.target_show(self.c, "web")["owner"], "ops")
        # A non-owner gets normal work, never the deploy item.
        b2 = core.go(self.c, self.dir.name, "dev")
        self.assertEqual((b2["role"], b2["item"]["id"]), ("worker", self.work))

    def test_role_deployer_refused_when_owned(self):
        core.target_own(self.c, "web", "dev")
        b = core.go(self.c, self.dir.name, "ops", role="deployer")
        self.assertEqual(b["role"], "idle")
        self.assertIn("owned by dev", b["why"])


class Push(Base):
    def setUp(self):
        super().setUp()
        core.project_add(self.c, "a", path=self.dir.name)
        for n in ("boss", "aa", "bb"):
            core.register(self.c, n)
        self.first = self.add("a", "first", p=0)
        self.x = self.add("a", "pushed one", p=3)

    def test_push_then_go_claims_it_and_others_cannot(self):
        core.push(self.c, self.x, "aa", "you know this code", "boss")
        self.assertEqual(core.unread(self.c, "aa")["alerts"], 1)
        with self.assertRaises(RiverError):
            core.claim(self.c, self.x, "bb")
        self.assertEqual([i["id"] for i in core.next_item(self.c, actor="bb", limit=5)], [self.first])
        self.assertEqual(core.next_item(self.c, actor="aa")[0]["id"], self.x)  # pushed first, despite P3
        b = core.go(self.c, self.dir.name, "aa")
        self.assertEqual((b["item"]["id"], b["item"]["assignee"]), (self.x, "aa"))
        self.assertIn("pushed to you by boss", b["why"])
        alert = [m for m in core.inbox(self.c, "aa", include_read=True) if m["kind"] == "alert"][0]
        self.assertEqual(alert["state"], "accepted")

    def test_decline_reopens_and_tells_pusher(self):
        core.push(self.c, self.x, "aa", None, "boss")
        with self.assertRaises(RiverError):
            core.decline(self.c, self.x, "no", "bb")
        core.decline(self.c, self.x, "busy", "aa")
        it = core._item(self.c, self.x)
        self.assertEqual((it["reserved_for"], it["reserved_until"]), (None, None))
        self.assertIn("busy", core.inbox(self.c, "boss")[0]["body"])
        core.claim(self.c, self.x, "bb")

    def test_expiry_reopens_with_notice(self):
        core.push(self.c, self.x, "aa", None, "boss")
        self.c.execute("UPDATE items SET reserved_until=? WHERE id=?", (core.iso(core.now() - timedelta(minutes=1)), self.x))
        core.activity(self.c, "bb")
        self.assertIsNone(core._item(self.c, self.x)["reserved_for"])
        self.assertTrue(any(m["kind"] == "notice" for m in core.inbox(self.c, "boss")))
        with self.assertRaises(RiverError):
            core.accept(self.c, self.x, "aa")

    def test_cancel_push_tells_the_agent(self):
        core.push(self.c, self.x, "aa", None, "boss")
        core.cancel_push(self.c, self.x, "boss")
        self.assertIsNone(core._item(self.c, self.x)["reserved_for"])
        self.assertTrue(any("cancelled" in m["body"] for m in core.inbox(self.c, "aa")))
        with self.assertRaises(RiverError):
            core.cancel_push(self.c, self.x, "boss")

    def test_push_refusals(self):
        core.claim(self.c, self.first, "bb")
        with self.assertRaises(RiverError):
            core.push(self.c, self.first, "aa", None, "boss")  # not open
        core.push(self.c, self.x, "aa", None, "boss")
        with self.assertRaises(RiverError):
            core.push(self.c, self.x, "bb", None, "boss")  # already pushed to someone else
        with self.assertRaises(RiverError):
            core.push(self.c, self.x, "nobody", None, "boss")


class HumanSteps(Base):
    def test_go_brief_names_the_people(self):
        core.project_add(self.c, "a", path=self.dir.name)
        core.register(self.c, "mark", human=True)
        self.add("a", "x")
        b = core.go(self.c, self.dir.name, "ag")
        self.assertEqual(b["humans"], ["mark"])


class NeedsYouPage(Base):
    """The page's needs-you list and its two message actions (river/server.py)."""

    def setUp(self):
        super().setUp()
        from river import server
        self.server = server
        core.register(self.c, "mark", human=True)
        core.register(self.c, "bot")
        core.project_add(self.c, "a")

    def test_item_events_carry_context_and_close_when_done(self):
        iid = core.item_add(self.c, "a", "approve policy", 2, "notes here", "human", (), "bot",
                            "decide the five points at the end of the draft")["id"]
        (ev,) = self.server.needs_you_view(self.c, "mark")
        self.assertEqual((ev["item_id"], ev["item_context"], ev["item_notes"]),
                         (iid, "decide the five points at the end of the draft", "notes here"))
        core.done(self.c, iid, "approved", "mark")
        self.assertEqual(self.server.needs_you_view(self.c, "mark"), [])

    def test_answer_and_read_close_message_events(self):
        q = core.send(self.c, "question", "which server?", to="mark", actor="bot")
        a = core.send(self.c, "alert", "look at #3", to="mark", actor="bot")
        kinds = sorted(e["message_kind"] for e in self.server.needs_you_view(self.c, "mark"))
        self.assertEqual(kinds, ["alert", "question"])
        self.server.OPS["answer"](self.c, {"msg": q["id"], "body": "OVH"}, "mark")
        self.server.OPS["message_read"](self.c, {"msg": a["id"]}, "mark")
        self.assertEqual(self.server.needs_you_view(self.c, "mark"), [])
        with self.assertRaises(RiverError):
            self.server.OPS["message_read"](self.c, {"msg": 9999}, "mark")


class Takeover(Base):
    def setUp(self):
        super().setUp()
        core.project_add(self.c, "a")
        core.register(self.c, "mark", human=True)
        core.register(self.c, "ag")
        self.h = self.add("a", "pick a color", doer="human")

    def test_takeover_clears_the_users_list_with_one_notice_and_undo(self):
        q = core.send(self.c, "question", "which color?", to="mark", item=self.h, actor="ag")
        with self.assertRaises(RiverError):
            core.claim(self.c, self.h, "ag")
        with self.assertRaises(RiverError):
            core.takeover(self.c, self.h, " ", "ag")
        it = core.takeover(self.c, self.h, "brand guide says blue", "ag")
        self.assertEqual((it["doer"], it["status"], it["assignee"]), ("ai", "in_progress", "ag"))
        self.assertEqual(core.needs_you(self.c), [])
        self.assertEqual(core.message_show(self.c, q["id"])["state"], "read")
        notes = [m for m in core.inbox(self.c, "mark") if m["kind"] == "notice"]
        self.assertEqual(len(notes), 1)
        self.assertIn("undo-takeover", notes[0]["body"])
        self.assertEqual([t["id"] for t in core.state(self.c)["takeovers"]], [self.h])
        core.undo_takeover(self.c, self.h, "mark")
        it = core._item(self.c, self.h)
        self.assertEqual((it["doer"], it["status"], it["assignee"], it["takeover_by"]), ("human", "open", None, None))
        self.assertEqual(len(core.needs_you(self.c)), 1)

    def test_agent_done_or_drop_needs_a_note(self):
        with self.assertRaises(RiverError):
            core.done(self.c, self.h, None, "ag")
        core.drop(self.c, self.h, "ag", note="not needed on the free tier")
        self.assertEqual(core.takeovers(self.c)[0]["takeover_kind"], "dropped")
        core.takeover_seen(self.c, self.h)
        self.assertEqual(core.takeovers(self.c), [])
        core.undo_takeover(self.c, self.h, "mark")
        self.assertEqual(core._item(self.c, self.h)["status"], "open")
        core.done(self.c, self.h, "did it", "mark")  # a person needs no note


class Prompts(Base):
    def test_prompt_for_item_and_all(self):
        core.project_add(self.c, "a", path=self.dir.name)
        core.register(self.c, "mark", human=True)
        h = core.item_add(self.c, "a", "Approve policy", doer="human", context="docs/policy.md, 5 decisions")["id"]
        self.add("a", "Launch", p=0, after=[h])
        q = core.send(self.c, "question", "Which plan?", to="mark", actor="ag")
        p = core.prompt_for(self.c, h)
        from pathlib import Path
        folder = Path(self.dir.name).resolve()
        for part in (f"cd {folder} && river --as mark show {h}", "docs/policy.md", "It blocks: #",
                     f"river --as mark done {h} --output", "takeover"):
            self.assertIn(part, p)
        allp = core.prompt_for_all(self.c, "mark")
        self.assertIn("1. Item #", allp)
        self.assertIn(f"river --as mark answer {q['id']}", allp)
        core.done(self.c, h, "approved", "mark")
        self.assertIn("Nothing", core.prompt_for_all(self.c, "nobody"))


class OfferGiveSplit(Base):
    def setUp(self):
        super().setUp()
        core.project_add(self.c, "a")
        for n in ("ag", "bo"):
            core.register(self.c, n)
        self.goal = self.add("a", "Stripe activation")
        self.page = core.item_add(self.c, "a", "Privacy page", blocks=self.goal, actor="t")["id"]
        core.claim(self.c, self.page, "bo")

    def test_offer_split_then_helper_takes_a_piece(self):
        o = core.offer(self.c, "I can take the draft", self.page, actor="ag")
        self.assertEqual((o["kind"], o["to_agent"]), ("offer", "bo"))
        res = core.split(self.c, self.page, ["draft text", "address"], "bo")
        self.assertEqual(core._item(self.c, self.page)["status"], "held")
        self.assertEqual(core.message_show(self.c, o["id"])["state"], "accepted")
        got = core.next_item(self.c, unblocks=self.goal, claim=True, actor="ag")
        self.assertIn(got[0]["id"], res["split_into"])
        core.done(self.c, got[0]["id"], "drafted", "ag")
        other = [n for n in res["split_into"] if n != got[0]["id"]][0]
        core.done(self.c, other, "found", "t")
        st = core._item(self.c, self.page)
        self.assertEqual((st["status"], st["assignee"]), ("in_progress", "bo"))

    def test_give_moves_the_lease_and_accepts_the_offer(self):
        o = core.offer(self.c, "let me", self.page, actor="ag")
        with self.assertRaises(RiverError):
            core.give(self.c, self.page, "ag", "ag")
        core.give(self.c, self.page, "ag", "bo")
        it = core._item(self.c, self.page)
        self.assertEqual((it["assignee"], it["status"]), ("ag", "in_progress"))
        self.assertEqual(core.message_show(self.c, o["id"])["state"], "accepted")
        with self.assertRaises(RiverError):
            core.give(self.c, self.page, "ag", "bo")  # not bo's any more

    def test_decline_offer_and_offer_refusals(self):
        o = core.offer(self.c, "let me", self.page, actor="ag")
        with self.assertRaises(RiverError):
            core.decline_message(self.c, o["id"], "no", "ag")  # not for ag
        core.decline_message(self.c, o["id"], "almost done", "bo")
        self.assertEqual(core.message_show(self.c, o["id"])["state"], "declined")
        self.assertTrue(any("almost done" in m["body"] for m in core.inbox(self.c, "ag")))
        with self.assertRaises(RiverError):
            core.offer(self.c, "x", self.goal, actor="ag")  # nobody holds it


if __name__ == "__main__":
    unittest.main()
