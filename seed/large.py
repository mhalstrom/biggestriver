#!/usr/bin/env python3
"""A large queue for a hand check of the page: 700 items in 17 projects, 630 of them finished, about 700 links.
Mermaid's own limits (50,000 characters, 500 edges) stopped the Graph tab on a queue of this size.
Run on an empty database, then open the Graph tab, choose All projects and tick "include done":

    RIVER_DB=/tmp/large.db python3 seed/large.py
    RIVER_DB=/tmp/large.db bin/river serve --open

Other sizes: python3 seed/large.py <items> <open items>   (above 1,500 items the graph leaves out finished items)
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from river import core  # noqa: E402

WORDS = ("orders", "checkout", "pricing page", "search", "invoices", "the importer", "email", "the footer",
         "accounts", "reports", "the API", "webhooks", "the cache", "sign-in", "exports", "the dashboard")
VERBS = ("Build", "Fix", "Review", "Test", "Document", "Speed up", "Clean up", "Add a check for")


def load(conn, items=700, open_items=70, projects=17, seed=696):
    """Add the items; the first ones are finished, the last open_items stay open. Returns (items, links)."""
    rnd = random.Random(seed)
    core.register(conn, "alex", human=True)
    core.config_set(conn, "setup_done", "on")  # the setup guide would cover the page
    names = [f"project-{n:02d}" for n in range(1, projects + 1)]
    for n in names:
        core.project_add(conn, n)
    ids, links, own = [], 0, {n: [] for n in names}
    for k in range(items):
        name = rnd.choice(names)
        # As in a real queue: most items wait on recent work of their own project, some on another project.
        near = own[name][-12:] if rnd.random() < 0.9 else ids[-40:]
        after = rnd.sample(near, min(len(near), rnd.choice((0, 0, 1, 1, 1, 2, 2))))
        title = f"{rnd.choice(VERBS)} {rnd.choice(WORDS)} for {rnd.choice(WORDS)}, part {k + 1}"
        ids.append(core.item_add(conn, name, title, after=after, actor="alex",
                                 doer="human" if k % 23 == 0 else "any")["id"])
        own[name].append(ids[-1])
        links += len(after)
    # In the order they were added, so each prerequisite is finished first.
    for i in ids[:items - open_items]:
        core.done(conn, i, output="done", actor="alex")
    return len(ids), links


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 700
    left = int(sys.argv[2]) if len(sys.argv) > 2 else n // 10
    conn = core.connect()
    if core.state(conn)["items"]:
        sys.exit("this database has items already: set RIVER_DB to a new file")
    made, links = load(conn, n, left)
    print(f"Loaded {made} items ({made - left} finished) and {links} links into {core.db_path()}.")
