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


if __name__ == "__main__":
    unittest.main()
