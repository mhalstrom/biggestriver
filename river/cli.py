"""`river` command line. Every command takes --json and --as <agent>."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import core
from .core import RiverError

GUIDES = Path(__file__).resolve().parent.parent / "skills"


def _fmt_item(a, show_reason=True):
    flags = []
    if a["status"] != "open":
        flags.append(a["status"] + (f" by {a['assignee']}" if a.get("assignee") else ""))
    elif a["open_blockers"]:
        flags.append("waits on " + ",".join(f"#{b}" for b in a["open_blockers"]))
    elif a["blocked_reason"]:
        flags.append(f"blocked: {a['blocked_reason']}")
    else:
        flags.append("ready")
    if a["doer"] != "any":
        flags.append(a["doer"])
    if "distance" in a:
        flags.append(f"{a['distance']} link(s) away")
    line = f"#{a['id']:<4} [{a['project']}] {a['title']}  ({'; '.join(flags)})"
    if show_reason:
        line += f"\n       {a['reason']}"
    return line


def _print_show(a):
    print(_fmt_item(a))
    if a["notes"]:
        print("  notes:", a["notes"])
    if a["waits_on_detail"]:
        print("  waits on:", ", ".join(f"#{d['id']} {d['title']} ({d['status']})" for d in a["waits_on_detail"]))
    if a["unblocks_detail"]:
        print("  unblocks:", ", ".join(f"#{d['id']} {d['title']} ({d['status']})" for d in a["unblocks_detail"]))
    if a.get("lease_expires_at"):
        print("  lease until:", a["lease_expires_at"])
    if a.get("output"):
        print("  output:", a["output"])
    if a.get("now_ready"):
        print("  now ready:", ", ".join(f"#{i}" for i in a["now_ready"]))
    for e in a.get("events", [])[:8]:
        print(f"  {e['at']} {e['actor']}: {e['change']}")


def _print_tree(n, prefix="", last=True, root=True):
    left = f", {n['lease_seconds_left'] // 60}m left" if n["lease_seconds_left"] is not None else ""
    who = f", {n['assignee']}{left}" if n["assignee"] else ""
    state = "ready" if n["ready"] else n["status"]
    if n["blocked_reason"]:
        state += f', blocked: "{n["blocked_reason"]}"'
    label = f"#{n['id']} {n['title']}  ({state}{who})"
    if root:
        print(label)
    else:
        print(prefix + ("└─ " if last else "├─ ") + label)
    kids = n["children"]
    for i, c in enumerate(kids):
        ext = "" if root else ("   " if last else "│  ")
        _print_tree(c, prefix + ext, i == len(kids) - 1, False)


def _footer(conn, actor):
    if not actor:
        return
    r = conn.execute("SELECT 1 FROM agents WHERE name=?", (actor,)).fetchone()
    if not r:
        print(f"(agent {actor} is not registered: river register {actor} [--human])", file=sys.stderr)
        return
    holds = core.agent_status(conn, actor)["holds"]
    if holds:
        parts = []
        for h in holds:
            if h["lease_expires_at"]:
                mins = int((core.parse_iso(h["lease_expires_at"]) - core.now()).total_seconds() // 60)
                parts.append(f"#{h['id']} {mins}m left")
            else:
                parts.append(f"#{h['id']}")
        print(f"[{actor}: holds {', '.join(parts)}]", file=sys.stderr)


def build_parser():
    p = argparse.ArgumentParser(prog="river", description="Biggest River: a dependency-ordered work queue for agents and people.")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--as", dest="actor", default=os.environ.get("RIVER_AGENT"), help="agent name (default $RIVER_AGENT)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("project", help="add, rank, list, or archive projects")
    prs = pr.add_subparsers(dest="pcmd", required=True)
    x = prs.add_parser("add"); x.add_argument("name"); x.add_argument("--rank", type=int); x.add_argument("--notes", default="")
    x = prs.add_parser("rank"); x.add_argument("name"); x.add_argument("rank", type=int)
    x = prs.add_parser("archive"); x.add_argument("name")
    prs.add_parser("list")

    x = sub.add_parser("add", help="add an item to a project")
    x.add_argument("project"); x.add_argument("title")
    x.add_argument("--priority", "-p", type=int, default=2, help="0 highest .. 4 lowest (default 2)")
    x.add_argument("--after", type=int, nargs="*", default=[], help="items this one waits on")
    x.add_argument("--notes", default="")
    x.add_argument("--doer", default="any", choices=core.DOERS, help="who can do it (default any)")

    x = sub.add_parser("edit", help="change title, notes, doer, or project")
    x.add_argument("id", type=int); x.add_argument("--title"); x.add_argument("--notes")
    x.add_argument("--doer", choices=core.DOERS); x.add_argument("--project")

    x = sub.add_parser("list", help="list items (open by default)")
    x.add_argument("--project"); x.add_argument("--status"); x.add_argument("--all", action="store_true")

    x = sub.add_parser("show", help="one item with its links and history"); x.add_argument("id", type=int)

    x = sub.add_parser("next", help="the next ready item from the area you choose")
    x.add_argument("--project", help="one project, or a comma list")
    x.add_argument("--unblocks", help="prerequisites of this item id or project")
    x.add_argument("--near", help="items linked to these item ids (comma list), closest first")
    x.add_argument("--claim", action="store_true", help="take it")
    x.add_argument("--limit", "-n", type=int, default=1)

    x = sub.add_parser("claim", help="take one ready item by id"); x.add_argument("id", type=int)
    x = sub.add_parser("done", help="finish an item"); x.add_argument("id", type=int); x.add_argument("--output")
    x = sub.add_parser("release", help="give a claimed item back"); x.add_argument("id", type=int); x.add_argument("--note")
    x = sub.add_parser("drop", help="close an item without doing it"); x.add_argument("id", type=int)
    x = sub.add_parser("reopen", help="open a closed item again"); x.add_argument("id", type=int)
    x = sub.add_parser("prio", help="set item priority"); x.add_argument("id", type=int); x.add_argument("priority", type=int)
    x = sub.add_parser("move", help="manual order inside a project")
    x.add_argument("id", type=int); g = x.add_mutually_exclusive_group(required=True)
    g.add_argument("--before", type=int); g.add_argument("--after", type=int)
    x = sub.add_parser("dep", help="make an item wait on others"); x.add_argument("id", type=int); x.add_argument("--on", type=int, nargs="+", required=True)
    x = sub.add_parser("undep", help="remove waits"); x.add_argument("id", type=int); x.add_argument("--on", type=int, nargs="+", required=True)
    x = sub.add_parser("blocked", help="record a blocker outside the queue"); x.add_argument("id", type=int); x.add_argument("--reason", required=True)
    x = sub.add_parser("unblock", help="clear an outside blocker"); x.add_argument("id", type=int)
    x = sub.add_parser("blockers", help="tree of what an item waits on"); x.add_argument("id", type=int)

    x = sub.add_parser("register", help="register this agent or person")
    x.add_argument("name"); x.add_argument("--human", action="store_true"); x.add_argument("--note", default="")
    x = sub.add_parser("unregister", help="remove an agent that holds nothing"); x.add_argument("name")
    x = sub.add_parser("note", help="set your status note"); x.add_argument("text")
    x = sub.add_parser("who", help="who is doing what"); x.add_argument("--item", type=int); x.add_argument("--project")
    sub.add_parser("heartbeat", help="renew your leases")
    sub.add_parser("capacity", help="how many agent sessions the graph can use now")

    c = sub.add_parser("config", help="settings")
    cs = c.add_subparsers(dest="ccmd", required=True)
    for name in ("get", "set", "unset"):
        y = cs.add_parser(name)
        if name != "get":
            y.add_argument("key")
        else:
            y.add_argument("key", nargs="?")
        if name == "set":
            y.add_argument("value")
        y.add_argument("--project"); y.add_argument("--item", type=int); y.add_argument("--agent")

    x = sub.add_parser("serve", help="the web page on 127.0.0.1")
    x.add_argument("--port", type=int); x.add_argument("--open", action="store_true")
    x = sub.add_parser("guide", help="print the skill text"); x.add_argument("which", nargs="?", default="river", choices=["river", "river-planner"])
    return p


def run(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd == "guide":
        print((GUIDES / args.which / "SKILL.md").read_text())
        return 0
    conn = core.connect()
    if args.cmd == "serve":
        from . import server
        port = args.port or int(core.setting(conn, "serve_port"))
        server.serve(port, open_browser=args.open)
        return 0
    actor = args.actor
    core.activity(conn, actor)
    res = dispatch(conn, args, actor)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        render(args, res)
    _footer(conn, actor)
    return 0


def dispatch(conn, a, actor):
    c = a.cmd
    if c == "project":
        if a.pcmd == "add":
            return core.project_add(conn, a.name, a.rank, a.notes, actor)
        if a.pcmd == "rank":
            return core.project_rank(conn, a.name, a.rank, actor)
        if a.pcmd == "archive":
            return core.project_archive(conn, a.name, actor)
        return core.project_list(conn)
    if c == "add":
        return core.item_add(conn, a.project, a.title, a.priority, a.notes, a.doer, a.after, actor)
    if c == "edit":
        return core.item_edit(conn, a.id, a.title, a.notes, a.doer, a.project, actor)
    if c == "list":
        return core.item_list(conn, a.project, a.status, a.all)
    if c == "show":
        return core.item_show(conn, a.id)
    if c == "next":
        return core.next_item(conn, a.project, a.unblocks, a.claim, actor, a.limit, a.near)
    if c == "claim":
        return core.claim(conn, a.id, actor)
    if c == "done":
        return core.done(conn, a.id, a.output, actor)
    if c == "release":
        return core.release(conn, a.id, a.note, actor)
    if c == "drop":
        return core.drop(conn, a.id, actor)
    if c == "reopen":
        return core.reopen(conn, a.id, actor)
    if c == "prio":
        return core.item_prio(conn, a.id, a.priority, actor)
    if c == "move":
        return core.item_move(conn, a.id, a.before, a.after, actor)
    if c == "dep":
        return core.dep_add(conn, a.id, a.on, actor)
    if c == "undep":
        return core.dep_remove(conn, a.id, a.on, actor)
    if c == "blocked":
        return core.block(conn, a.id, a.reason, actor)
    if c == "unblock":
        return core.unblock(conn, a.id, actor)
    if c == "blockers":
        return core.blockers(conn, a.id)
    if c == "register":
        return core.register(conn, a.name, a.human, a.note)
    if c == "unregister":
        return core.unregister(conn, a.name, actor)
    if c == "note":
        if not actor:
            raise RiverError("set RIVER_AGENT or pass --as <name>")
        return core.agent_note(conn, actor, a.text)
    if c == "who":
        return core.who(conn, a.item, a.project)
    if c == "heartbeat":
        return {"ok": True}
    if c == "capacity":
        return core.capacity(conn)
    if c == "config":
        if a.ccmd == "get":
            if a.key:
                return {"key": a.key, "value": core.setting(
                    conn, a.key, item_id=a.item, agent=a.agent,
                    project_id=core._project(conn, a.project)["id"] if a.project else None)}
            return core.config_list(conn)
        if a.ccmd == "set":
            return core.config_set(conn, a.key, a.value, a.project, a.item, a.agent, actor)
        return core.config_unset(conn, a.key, a.project, a.item, a.agent, actor)
    raise RiverError(f"unknown command {c}")


def render(a, res):
    c = a.cmd
    if c == "project":
        rows = res if isinstance(res, list) else [res]
        for p in rows:
            print(f"{p['rank']:>2}. {p['name']}" + (f"  ({p['open_items']} open)" if "open_items" in p else ""))
        return
    if c in ("list",):
        if not res:
            print("(no items)")
        for it in res:
            print(_fmt_item(it, show_reason=it["ready"]))
        return
    if c == "next":
        if not res:
            print("Nothing is ready in that area. Try: river next (all projects), river next --unblocks <id>, or river list")
            return
        if a.claim:
            print("Claimed:")
            _print_show(res[0])
        else:
            for it in res:
                print(_fmt_item(it))
        return
    if c == "blockers":
        _print_tree(res)
        return
    if c == "who":
        if not res:
            print("(nobody)")
        for ag in res:
            holds = ", ".join(f"#{h['id']} {h['title']}" for h in ag["holds"]) or "nothing"
            note = f" — {ag['note']}" if ag["note"] else ""
            print(f"{ag['name']} ({ag['kind']}, {ag['state']}){note}\n    holds: {holds}")
        return
    if c == "capacity":
        print(f"Ready for agents: {len(res['ready_for_agents'])}   ready for humans: {len(res['ready_for_humans'])}   "
              f"in progress: {len(res['in_progress'])}")
        print(f"Active agent sessions: {res['agents_active']} (busy {res['agents_busy']}, idle {len(res['agents_idle'])})")
        print(f"Spare slots: {res['spare_slots']}   excess sessions: {res['excess_sessions']}   "
              f"peak future width: {res['peak_width']}")
        for adv in res["advice"]:
            print(" -", adv["text"])
        return
    if c == "config":
        if "overrides" in res:
            for k, v in res["defaults"].items():
                print(f"{k} = {v} (default)")
            for o in res["overrides"]:
                print(f"{o['key']} = {o['value']} ({o['scope']})")
        else:
            print(json.dumps(res))
        return
    if c == "register" or c == "note":
        print(f"{res['name']} ({res['kind']}) registered. Set RIVER_AGENT={res['name']} in your shell.")
        return
    if c == "heartbeat":
        print("leases renewed")
        return
    if isinstance(res, dict) and "id" in res and "title" in res:
        _print_show(res)
        return
    print(json.dumps(res, indent=2, default=str))


def main():
    try:
        sys.exit(run())
    except RiverError as e:
        print(f"river: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
