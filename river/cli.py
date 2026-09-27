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

QUICKSTART = """Biggest River: a shared work queue for people and agent sessions.

Items live in projects and can wait on other items. `river next` gives the
most important ready item in the area you choose; `--claim` takes it.

Agent sessions: run `river go` in the project folder. It names the session,
picks a role (worker, unblocker, planner, idle), claims an item, and prints a
briefing. Run it again after each item.

By hand:
  river register <your-name> [--human] --note "what you work on"
  export RIVER_AGENT=<your-name>
  river project list                      what each project covers; pick the one you know

Work loop:
  river next --project <name> --claim     take the next item where you have context
  river next --mine --claim               or: next to what you did before (after your first item)
  river show <id>                         read it
  river done <id> --output "what changed" finish it   (or: river release <id>)

More:
  river guide          how an agent works from the queue (the full loop)
  river guide planner  how to split work into items and dependencies
  river guide setup    how to set up your agents to use river
  river --help         every command
"""

AGENT_SNIPPET = """## Work queue

This project uses Biggest River (`river`) to track work and who is doing it.
When the user says "go" (or asks you to take work from the queue), run
`river go` in this folder and follow the briefing it prints: it names you,
gives you a role and an item, and says what to run when you finish.
"""

SETUP = """Setting up agents to use river

1. Put the command on PATH:
     ln -s <repo>/bin/river ~/.local/bin/river

2. Tell your agents about it. Add this block to the instructions file your
   agent reads (CLAUDE.md for Claude Code, AGENTS.md for Codex and others):

""" + "\n".join("     " + line for line in AGENT_SNIPPET.splitlines()) + """

   Or let river add it:  river setup-agent --append CLAUDE.md

3. Claude Code only, optional: install the skills so they load when needed:
     ln -s <repo>/skills/river ~/.claude/skills/river
     ln -s <repo>/skills/river-planner ~/.claude/skills/river-planner

4. Give each agent session its own name (river register <name>). A person
   registers with --human and usually wants longer claims:
     river config set lease_ttl 7d --agent <person>

5. Watch it:  river serve --open
"""

HINTS = {
    "register": "next: export RIVER_AGENT={name}, read river project list, then river next --project <name> --claim  (river guide for the full loop)",
    "claim": "when finished: river done {id} --output \"what changed\"   cannot finish: river release {id} --note \"why\"   work found: river add <project> \"title\"",
    "done": "next: river next --mine --claim (work next to what you just did)  or  river next --project <name> --claim",
    "release": "next: river next --claim",
    "empty": "nothing ready here. Try: river next (all projects), river next --unblocks <id>, river blockers <id>, river list",
    "no_actor": "tip: river register <name> and export RIVER_AGENT=<name> so claims and history carry your name",
}


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
    if a.get("same_project"):
        flags.append("same project as your earlier work")
    elif a.get("distance") is not None:
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
    p.add_argument("--quiet", "-q", action="store_true", default=bool(os.environ.get("RIVER_QUIET")),
                   help="no hint lines")
    sub = p.add_subparsers(dest="cmd")

    pr = sub.add_parser("project", help="add, rank, list, or archive projects")
    prs = pr.add_subparsers(dest="pcmd", required=True)
    x = prs.add_parser("add"); x.add_argument("name"); x.add_argument("--rank", type=int)
    x.add_argument("--description", "--notes", dest="notes", default="",
                   help="what the project covers and what context helps (agents read this to pick an area)")
    x.add_argument("--path", help="folder this project lives in; river go run there finds it")
    x = prs.add_parser("describe", help="set a project's description"); x.add_argument("name"); x.add_argument("text")
    x = prs.add_parser("path", help="link a project to a folder (river go uses it)"); x.add_argument("name"); x.add_argument("path", nargs="?")
    x = prs.add_parser("show", help="a project's description, who works on it, and its ready items"); x.add_argument("name")
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
    x.add_argument("--mine", action="store_true", help="items linked to what you claimed or finished before, then your projects")
    x.add_argument("--claim", action="store_true", help="take it")
    x.add_argument("--limit", "-n", type=int, default=1)

    x = sub.add_parser("init", help="set up the current folder: link or create its project, add the agent block")
    x.add_argument("--project", help="project name (default: the folder name)")
    x.add_argument("--description", default="", help="what the project covers, for agents")
    x.add_argument("--file", action="append", help="instructions file to add the block to (default CLAUDE.md, plus AGENTS.md if present)")

    x = sub.add_parser("go", help="start or continue an agent session: name, role, item, briefing")
    x.add_argument("--project", help="project name(s) when this folder is not linked")
    x.add_argument("--role", choices=core.ROLES, help="ask for a role instead of letting river pick")

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
    x.add_argument("--dev", action="store_true", help="restart on code change; the page reloads itself")
    x = sub.add_parser("guide", help="how to use river: worker loop, planner, or agent setup")
    x.add_argument("which", nargs="?", default="river", choices=["river", "planner", "river-planner", "setup"])
    x = sub.add_parser("setup-agent", help="print (or append) the instructions block for CLAUDE.md / AGENTS.md")
    x.add_argument("--append", metavar="FILE", help="append the block to this file if it is not there yet")
    return p


def run(argv=None):
    args = build_parser().parse_args(argv)
    if args.cmd is None:
        print(QUICKSTART)
        return 0
    if args.cmd == "guide":
        if args.which == "setup":
            print(SETUP)
        else:
            name = "river-planner" if args.which in ("planner", "river-planner") else "river"
            text = (GUIDES / name / "SKILL.md").read_text()
            print(text.split("---", 2)[2].strip() if text.startswith("---") else text)
        return 0
    if args.cmd == "init":
        return init_folder(args)
    if args.cmd == "setup-agent":
        if not args.append:
            print(AGENT_SNIPPET)
            return 0
        f = Path(args.append)
        old = f.read_text() if f.exists() else ""
        if "Biggest River" in old:
            print(f"{f} already mentions Biggest River; nothing added")
            return 0
        f.write_text(old + ("\n" if old and not old.endswith("\n") else "") + ("\n" if old else "") + AGENT_SNIPPET)
        print(f"added the work queue block to {f}")
        return 0
    conn = core.connect()
    if args.cmd == "serve":
        from . import server
        port = args.port or int(core.setting(conn, "serve_port"))
        server.serve(port, open_browser=args.open, dev=args.dev)
        return 0
    actor = args.actor
    if args.cmd != "go":
        core.activity(conn, actor)
    res = dispatch(conn, args, actor)
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        render(args, res)
    sys.stdout.flush()
    _footer(conn, res["agent"] if args.cmd == "go" else actor)
    if not args.quiet and not args.json:
        h = _hint(args, res, actor)
        if h:
            print(h, file=sys.stderr)
    return 0


def _hint(a, res, actor):
    c = a.cmd
    if c == "go":
        return None
    if c == "register":
        return HINTS["register"].format(name=res["name"])
    if c == "next":
        if not res:
            return None  # render already says what to try
        if a.claim:
            return HINTS["claim"].format(id=res[0]["id"])
        return f"take it: river claim {res[0]['id']}   (or add --claim to river next)" + ("" if actor else "\n" + HINTS["no_actor"])
    if c == "claim":
        return HINTS["claim"].format(id=res["id"])
    if c == "done":
        return HINTS["done"].format(id=res["id"])
    if c == "release":
        return HINTS["release"]
    if not actor and c in ("list", "show", "who", "capacity", "blockers"):
        return HINTS["no_actor"]
    return None


def _append_block(f):
    old = f.read_text() if f.exists() else ""
    if "Biggest River" in old:
        return f"{f.name}: already has the work queue block"
    f.write_text(old + ("\n" if old and not old.endswith("\n") else "") + ("\n" if old else "") + AGENT_SNIPPET)
    return f"{f.name}: added the work queue block"


def init_folder(args):
    import re
    conn = core.connect()
    here = Path.cwd()
    lines = []
    linked = core.projects_for_dir(conn, here)
    linked_here = [n for n in linked if Path(core._project(conn, n)["path"]) == here.resolve()]
    if args.project:
        name = args.project
    elif linked_here:
        name = None
        lines.append(f"projects already linked to this folder: {', '.join(linked_here)}")
    else:
        name = re.sub(r"[^a-z0-9._-]+", "-", here.name.lower()).strip("-") or "project"
    if name:
        exists = conn.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone()
        if exists:
            core.project_path(conn, name, str(here), args.actor)
            lines.append(f"project {name}: linked to {here}")
        else:
            core.project_add(conn, name, notes=args.description, actor=args.actor, path=str(here))
            lines.append(f"project {name}: created and linked to {here}")
        if args.description and exists:
            core.project_describe(conn, name, args.description, args.actor)
    files = [Path(f) for f in (args.file or [])] or [here / "CLAUDE.md"] + ([here / "AGENTS.md"] if (here / "AGENTS.md").exists() else [])
    for f in files:
        lines.append(_append_block(f))
    shown = name or linked_here[0]
    if not core._project(conn, shown)["notes"]:
        lines.append(f'next: describe it for agents: river project describe {shown} "what it covers, where, what helps"')
    lines.append(f"next: add work (river add {shown} \"...\") or open an agent here and say go")
    print("\n".join(lines))
    return 0


def dispatch(conn, a, actor):
    c = a.cmd
    if c == "project":
        if a.pcmd == "add":
            return core.project_add(conn, a.name, a.rank, a.notes, actor, a.path)
        if a.pcmd == "rank":
            return core.project_rank(conn, a.name, a.rank, actor)
        if a.pcmd == "path":
            return core.project_path(conn, a.name, a.path, actor)
        if a.pcmd == "describe":
            return core.project_describe(conn, a.name, a.text, actor)
        if a.pcmd == "show":
            return core.project_show(conn, a.name)
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
    if c == "go":
        return core.go(conn, os.getcwd(), actor, a.project, a.role)
    if c == "next":
        return core.next_item(conn, a.project, a.unblocks, a.claim, actor, a.limit, a.near, a.mine)
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


def render_go(b):
    me = b["agent"]
    r = f"river --as {me}"
    out = []
    out.append(f"You are river agent {me}. Role: {b['role'].upper()}. ({b['why']})")
    if b["new_name"]:
        out.append(f"Your shell may not keep environment variables, so pass --as {me} on every river command.")
    for n in b["projects"]:
        d = b["descriptions"].get(n)
        out.append(f"Project {n}: {d}" if d else f"Project {n} (no description: {r} project describe {n} \"...\")")
    out.append("")
    it = b.get("item")
    if it:
        out.append(f"YOUR ITEM #{it['id']}: {it['title']}")
        if it["notes"]:
            out.append(f"  notes: {it['notes']}")
        if it["waits_on_detail"]:
            out.append("  waited on (all done): " + ", ".join(f"#{d['id']} {d['title']}" for d in it["waits_on_detail"]))
        if it["unblocks_detail"]:
            out.append("  unblocks: " + ", ".join(f"#{d['id']} {d['title']}" for d in it["unblocks_detail"]))
        if it.get("output"):
            out.append(f"  earlier output: {it['output']}")
        out += [
            "",
            "Rules:",
            f"  - Do this item only. Read `{r} show {it['id']}` again if you need the links.",
            f"  - It needs something first: {r} add <project> \"<title>\" --doer ai|human, then {r} dep {it['id']} --on <new-id>;",
            f"    if you will not do that yourself now: {r} release {it['id']} --note \"<why>\" and run go again.",
            f"  - You find other work: {r} add <project> \"<title>\" --notes \"found during #{it['id']}\". Do not do it now.",
            f"  - Waiting on something outside the queue: {r} blocked {it['id']} --reason \"<what>\", release, run go again.",
            f"  - The user must do a step: add it with --doer human and tell the user.",
            "",
            f"When finished:  {r} done {it['id']} --output \"<what changed, commit id>\"",
            f"Then continue:  {r} go",
        ]
    elif b["role"] == "planner":
        out.append("NO READY WORK. Your job: plan the project into items that agents and people can take.")
        if b.get("open_items"):
            out.append("Open items that cannot move:")
            for o in b["open_items"]:
                why = f"blocked: {o['blocked_reason']}" if o["blocked_reason"] else (
                    "waits on " + ",".join(f"#{x}" for x in o["open_blockers"]) if o["open_blockers"] else "held")
                out.append(f"  #{o['id']} {o['title']}  ({why})")
        out += [
            "",
            "Steps:",
            "  1. Read the project description and the code or documents it names. Ask the user when the goal is unclear.",
            f"  2. Add items, one checkable outcome each: {r} add <project> \"<title>\" --doer ai|human --notes \"<files, commands, how to know it is done>\"",
            f"  3. Link what must come first: {r} dep <id> --on <id> ...   Set importance on the outcome only: {r} prio <id> 0",
            f"  4. Then run {r} go to take the first item, or stop and let other sessions take them.",
            f"  (Full planning guide: river guide planner)",
        ]
    else:
        out.append("NOTHING FOR YOU NOW.")
        for h in b.get("held_by_others", []):
            out.append(f"  #{h['id']} {h['title']}  (held by {h['assignee']})")
        out += [
            "",
            "Tell the user this session has no work here. Options:",
            f"  - stop this session (it frees nothing, it holds nothing), or",
            f"  - work elsewhere: river project list, then {r} go --project <name>, or",
            f"  - check again later: {r} go",
        ]
    if b.get("human_waiting"):
        out.append("")
        out.append("Waiting on the user (tell them):")
        for h in b["human_waiting"]:
            out.append(f"  #{h['id']} {h['title']}")
    print("\n".join(out))


def render(a, res):
    c = a.cmd
    if c == "go":
        return render_go(res)
    if c == "project":
        if isinstance(res, dict) and "ready_count" in res:
            print(f"{res['name']} (rank {res['rank']})")
            print("  " + (res["description"] or "(no description: river project describe " + res["name"] + " \"...\")"))
            print("  items: " + ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in res["counts"].items() if v))
            print("  working now: " + (", ".join(res["working_now"]) or "nobody"))
            if res["worked_recently"]:
                print("  worked here recently: " + ", ".join(res["worked_recently"]))
            print(f"  ready ({res['ready_count']}):")
            for it in res["ready"]:
                print("    " + _fmt_item(it, show_reason=False))
            return
        rows = res if isinstance(res, list) else [res]
        for p in rows:
            print(f"{p['rank']:>2}. {p['name']}" + (f"  ({p['open_items']} open)" if "open_items" in p else ""))
            if p.get("notes"):
                print(f"    {p['notes']}")
        return
    if c in ("list",):
        if not res:
            print("(no items)")
        for it in res:
            print(_fmt_item(it, show_reason=it["ready"]))
        return
    if c == "next":
        if not res:
            print(HINTS["empty"])
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
