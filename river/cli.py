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
briefing. Run it again after each item. To plan work with the user instead,
run `river plan`: an overview, the open questions, and the planner's rules.

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
When the user says "plan", run `river plan` instead and ask the user what
outcome they want before you add items.
"""

# The block before plan mode; river init replaces it with AGENT_SNIPPET.
OLD_SNIPPETS = [AGENT_SNIPPET.split('When the user says "plan"')[0]]

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
    elif a.get("busy_conflicts"):
        flags.append("conflicts with " + ",".join(f"#{b}" for b in a["busy_conflicts"]) + " (in progress)")
    else:
        flags.append("ready")
    if a["doer"] != "any":
        flags.append(a["doer"])
    if a.get("reserved_for") and a["status"] == "open":
        flags.append(f"pushed to {a['reserved_for']}" if a.get("reserved_until") else f"reserved for {a['reserved_for']}")
    if a.get("replan"):
        flags.append("replan")
    if a.get("same_project"):
        flags.append("same project as your earlier work")
    elif a.get("distance") is not None:
        flags.append(f"{a['distance']} link(s) away")
    line = f"#{a['id']:<4} [{a['project']}] {a['title']}  ({'; '.join(flags)})"
    if show_reason:
        line += f"\n       {a['reason']}"
    return line


def _context_lines(a, indent="  "):
    """What a new agent needs to start: why and where (context), the files (touches), how to know it works (check)."""
    out = []
    if a.get("context"):
        first, *rest = a["context"].splitlines() or [""]
        out.append(f"{indent}context: {first}")
        out += [f"{indent}         {line}" for line in rest]
    if a.get("touches"):
        out.append(f"{indent}touches: {', '.join(a['touches'])}")
    if a.get("check"):
        out.append(f"{indent}check:   {a['check']}")
    for f in a.get("fed_by_detail", []):
        if f["output"]:
            out.append(f"{indent}from #{f['id']}: {f['output']}")
    return out


def _cut(text, n=70):
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _print_show(a):
    print(_fmt_item(a))
    if a.get("kind") == "deploy":
        print(f"  deploy item for target {a['target']} (owner: {a.get('target_owner') or 'nobody'}; only the owner takes it)")
    if a["notes"]:
        print("  notes:", a["notes"])
    for line in _context_lines(a):
        print(line)
    if a.get("kind") == "deploy" and a["waits_on_detail"]:
        print("  ships:", ", ".join(f"#{d['id']} {d['title']} ({d['status']})" for d in a["waits_on_detail"]))
    elif a["waits_on_detail"]:
        print("  waits on:", ", ".join(f"#{d['id']} {d['title']} ({d['status']})" for d in a["waits_on_detail"]))
    if a["unblocks_detail"]:
        print("  unblocks:", ", ".join(f"#{d['id']} {d['title']} ({d['status']})" for d in a["unblocks_detail"]))
    if a.get("conflicts_detail"):
        print("  conflicts with (never in progress together):", ", ".join(
            f"#{d['id']} {d['title']} ({d['status']}" + (f" by {d['assignee']}" if d["assignee"] else "") + ")"
            for d in a["conflicts_detail"]))
    if a.get("lease_expires_at"):
        print("  lease until:", a["lease_expires_at"])
    if a.get("hold_expires_at"):
        print("  held until:", a["hold_expires_at"], "(renewed by your commands; river release ends it)")
    if a.get("output"):
        print("  output:", a["output"])
    if a.get("found_during"):
        print(f"  found during: #{a['found_during']}")
    if a.get("found_here"):
        print("  found while doing this:", ", ".join(f"#{d['id']} {d['title']} ({d['status']})" for d in a["found_here"]))
    if a.get("message_count"):
        print(f"  messages: {a['message_count']} (river thread --item {a['id']})")
    if a.get("shipped_in"):
        print(f"  ship requested: joins deploy item #{a['shipped_in']}")
    if a.get("now_ready"):
        print("  now ready:", ", ".join(f"#{i}" for i in a["now_ready"]))
    if a.get("resumed"):
        print("  back in progress for its holder:", ", ".join(f"#{i}" for i in a["resumed"]))
    for e in a.get("events", [])[:8]:
        print(f"  {e['at']} {e['actor']}: {e['change']}")


def _print_tree(n, prefix="", last=True, root=True):
    left = f", {n['lease_seconds_left'] // 60}m left" if n["lease_seconds_left"] is not None else ""
    who = f", {n['assignee']}{left}" if n["assignee"] else ""
    state = "ready" if n["ready"] else n["status"]
    if n["blocked_reason"]:
        state += f', blocked: "{n["blocked_reason"]}"'
    for c in n.get("busy_conflicts", []):
        state += f", conflicts with #{c['id']} held by {c['assignee']}"
    label = f"#{n['id']} {n['title']}  ({state}{who})"
    if root:
        print(label)
    else:
        print(prefix + ("└─ " if last else "├─ ") + label)
    kids = n["children"]
    for i, c in enumerate(kids):
        ext = "" if root else ("   " if last else "│  ")
        _print_tree(c, prefix + ext, i == len(kids) - 1, False)


def _unread_text(u, actor):
    if not u["unread"] and not u["questions"]:
        return None
    parts = [f"{u['unread']} unread"] if u["unread"] else []
    if u["alerts"]:
        parts.append(f"{u['alerts']} alert{'s' if u['alerts'] > 1 else ''}")
    if u["questions"]:
        parts.append(f"{u['questions']} question{'s' if u['questions'] > 1 else ''} to answer")
    return f"inbox: {', '.join(parts)} (river --as {actor} inbox)"


def _footer(conn, actor):
    if not actor:
        return
    r = conn.execute("SELECT 1 FROM agents WHERE name=?", (actor,)).fetchone()
    if not r:
        print(f"(agent {actor} is not registered: river register {actor} [--human])", file=sys.stderr)
        return
    st = core.agent_status(conn, actor)
    holds = st["holds"]
    bits = []
    if holds:
        parts = []
        for h in holds:
            if h["lease_expires_at"]:
                mins = int((core.parse_iso(h["lease_expires_at"]) - core.now()).total_seconds() // 60)
                parts.append(f"#{h['id']} {mins}m left")
            elif h.get("hold_expires_at"):
                parts.append(f"#{h['id']} held {core._short(core.parse_iso(h['hold_expires_at']) - core.now())} left")
            else:
                parts.append(f"#{h['id']}")
        bits.append(f"holds {', '.join(parts)}")
    if st["owns"]:
        bits.append("owns " + ", ".join(
            f"{o['name']} {core._short(core.parse_iso(o['owner_expires_at']) - core.now())} left" for o in st["owns"]))
    msg = _unread_text(core.unread(conn, actor), actor)
    if msg:
        bits.append(msg)
    if bits:
        print(f"[{actor}: {'; '.join(bits)}]", file=sys.stderr)


def _fmt_msg(m, indent=""):
    to = m["to_agent"] or (f"the holder of #{m['item_id']}" if m["item_id"] else "?")
    about = f" about #{m['item_id']}" + (f" {m['item_title']}" if m.get("item_title") else "") if m["item_id"] else ""
    head = f"{indent}#{m['id']} {m['kind']} from {m['from_agent']} to {to}{about}  ({m['created_at']}"
    if m["kind"] in ("question", "offer") or m["state"] not in ("open", "read"):
        head += f", {m['state']}"
    if m["unread"]:
        head += ", new"
    if m["reply_to"]:
        head += f", reply to #{m['reply_to']}"
    lines = [head + ")"]
    lines += [f"{indent}    {line}" for line in m["body"].splitlines() or [""]]
    return "\n".join(lines)


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
    x.add_argument("--target", help="deploy target this project ships to (river target list)")
    x = prs.add_parser("describe", help="set a project's description"); x.add_argument("name"); x.add_argument("text")
    x = prs.add_parser("path", help="link a project to a folder (river go uses it)"); x.add_argument("name"); x.add_argument("path", nargs="?")
    x = prs.add_parser("show", help="a project's description, who works on it, and its ready items"); x.add_argument("name")
    x = prs.add_parser("target", help="put a project in a deploy target (no target clears it)")
    x.add_argument("name"); x.add_argument("target", nargs="?")
    x = prs.add_parser("rank"); x.add_argument("name"); x.add_argument("rank", type=int)
    x = prs.add_parser("archive"); x.add_argument("name")
    prs.add_parser("list")

    tg = sub.add_parser("target", help="deploy targets: where projects ship to")
    tgs = tg.add_subparsers(dest="tcmd", required=True)
    x = tgs.add_parser("add"); x.add_argument("name")
    x.add_argument("--description", default="", help="how and where it deploys")
    x = tgs.add_parser("describe", help="set how a target deploys"); x.add_argument("name"); x.add_argument("text")
    x = tgs.add_parser("show", help="a target, its owner, and its projects"); x.add_argument("name")
    x = tgs.add_parser("own", help="become the one owner of a target (runs its deploys)"); x.add_argument("name")
    x = tgs.add_parser("release", help="stop owning a target"); x.add_argument("name")
    x = tgs.add_parser("give", help="hand a target you own to another agent"); x.add_argument("name")
    x.add_argument("--to", required=True)
    tgs.add_parser("list")

    x = sub.add_parser("add", help="add an item: river add [project] \"title\" (project from --blocks/--found-during or the folder)")
    x.add_argument("words", nargs="+", metavar="[project] title")
    x.add_argument("--priority", "-p", type=int, default=2, help="0 highest .. 4 lowest (default 2)")
    x.add_argument("--after", type=int, nargs="*", default=[], help="items this one waits on")
    x.add_argument("--notes", default="")
    x.add_argument("--doer", default="any", choices=core.DOERS, help="who can do it (default any)")
    x.add_argument("--context", default="", help="what a new agent must know to start: why, where, decisions made")
    x.add_argument("--touches", nargs="*", default=[], help="files or directories it changes")
    x.add_argument("--check", default="", help="command that shows it works (tests, a build)")
    x.add_argument("--blocks", type=int, help="item that must wait on this new one (usually the one you hold)")
    x.add_argument("--found-during", type=int, dest="found_during",
                   help="the item you were working on when you found this (links them; not a dependency)")
    g = x.add_mutually_exclusive_group()
    g.add_argument("--keep", dest="mode", action="store_const", const="keep",
                   help="with --blocks: keep holding that item and do this one yourself now")
    g.add_argument("--release", dest="mode", action="store_const", const="release",
                   help="with --blocks: give that item back; anyone can do this one (the default)")

    x = sub.add_parser("edit", help="change title, notes, doer, or project")
    x.add_argument("id", type=int); x.add_argument("--title"); x.add_argument("--notes")
    x.add_argument("--doer", choices=core.DOERS); x.add_argument("--project")
    x.add_argument("--context"); x.add_argument("--touches", nargs="*", help="replaces the list; give none to clear it")
    x.add_argument("--check")

    x = sub.add_parser("list", help="list items (open by default)")
    x.add_argument("--project"); x.add_argument("--status"); x.add_argument("--all", action="store_true")

    x = sub.add_parser("show", help="one item with its links and history"); x.add_argument("id", type=int)
    nt = sub.add_parser("notify", help="send needs-you notifications: run, test a channel, status")
    nts = nt.add_subparsers(dest="ncmd", required=True)
    x = nts.add_parser("run", help="send what is due (loops every notify_interval unless --once)")
    x.add_argument("--once", action="store_true", help="one pass, for launchd or cron")
    x.add_argument("--now", action="store_true", help="do not wait for the batch window")
    x = nts.add_parser("test", help="send a test message on one channel"); x.add_argument("channel")
    nts.add_parser("status", help="per channel: configured, pending, last send, last error")
    x = nts.add_parser("setup", help="set up a channel (ntfy: makes a secret topic and prints the phone steps)")
    x.add_argument("channel", choices=["ntfy"]); x.add_argument("--url", help="ntfy server (default https://ntfy.sh)")
    x.add_argument("--token", help="access token for a protected or self-hosted ntfy server")
    x = sub.add_parser("prompt", help="a paste-ready prompt for an agent that helps a person with a human item")
    x.add_argument("id", type=int, nargs="?"); x.add_argument("--all", action="store_true", help="every item and question that waits on the person")
    x.add_argument("--for", dest="person", help="the person (default: you if you are a person, else the first registered person)")
    x = sub.add_parser("needs-you", help="what waits on a person: ready human items, questions and alerts to people")
    x.add_argument("--human", help="only this person's (and those for anyone)"); x.add_argument("--all", action="store_true", help="closed ones too")
    x = sub.add_parser("status", help="overview: every project's counts, recent completions, who is working, open slots")
    x.add_argument("--recent", type=int, default=10, help="how many recent completions (default 10)")
    x = sub.add_parser("log", help="completed work: done items with output, who, and when, by day")
    x.add_argument("--project"); x.add_argument("--since", default="7d", help="how far back (default 7d; all for everything)")

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

    x = sub.add_parser("plan", help="start a planner session: overview, open questions, and the planner's rules")
    x.add_argument("--project", help="project name(s) to focus on (default: this folder's, else all)")

    x = sub.add_parser("claim", help="take one ready item by id"); x.add_argument("id", type=int)
    x = sub.add_parser("done", help="finish an item"); x.add_argument("id", type=int); x.add_argument("--output")
    x.add_argument("--ship", action="store_true", help="also ask for it to be deployed (river ship)")
    x.add_argument("--note", help="why an agent may close a person's item (required then; the user is told)")
    x = sub.add_parser("ship", help="ask for an item to be deployed: it joins its target's next deploy item")
    x.add_argument("id", type=int)
    x = sub.add_parser("release", help="give a claimed item back"); x.add_argument("id", type=int); x.add_argument("--note")
    x = sub.add_parser("drop", help="close an item without doing it"); x.add_argument("id", type=int)
    x.add_argument("--note", help="why (required when an agent drops a person's item)")
    x = sub.add_parser("takeover", help="an agent does a person's item itself (the user is told, and can undo)")
    x.add_argument("id", type=int); x.add_argument("--note", required=True, help="how you will do it without the user")
    x = sub.add_parser("undo-takeover", help="give a taken-over item back to the people"); x.add_argument("id", type=int)
    x = sub.add_parser("reopen", help="open a closed item again"); x.add_argument("id", type=int)
    x = sub.add_parser("prio", help="set item priority"); x.add_argument("id", type=int); x.add_argument("priority", type=int)
    x = sub.add_parser("move", help="manual order inside a project")
    x.add_argument("id", type=int); g = x.add_mutually_exclusive_group(required=True)
    g.add_argument("--before", type=int); g.add_argument("--after", type=int)
    x = sub.add_parser("dep", help="make an item wait on others, or mark items that must not run together")
    x.add_argument("id", type=int); x.add_argument("--on", type=int, nargs="+", required=True)
    x.add_argument("--kind", default="blocks", choices=core.DEP_KINDS,
                   help="blocks: wait for it (default); feeds: wait, then read its output; "
                        "conflicts: no order, never in progress together")
    g = x.add_mutually_exclusive_group()
    g.add_argument("--keep", dest="mode", action="store_const", const="keep",
                   help="you hold <id>: keep it and do the prerequisites yourself")
    g.add_argument("--release", dest="mode", action="store_const", const="release",
                   help="you hold <id>: give it back while the prerequisites wait")
    x = sub.add_parser("push", help="reserve an open item for one agent and alert it")
    x.add_argument("id", type=int); x.add_argument("--to", required=True); x.add_argument("--note")
    x = sub.add_parser("accept", help="take an item pushed to you"); x.add_argument("id", type=int)
    x = sub.add_parser("decline", help="hand a pushed item back"); x.add_argument("id", type=int); x.add_argument("--note")
    x = sub.add_parser("keep", help="hold an item again while you do its open prerequisites"); x.add_argument("id", type=int)
    x = sub.add_parser("undep", help="remove waits or conflict links"); x.add_argument("id", type=int); x.add_argument("--on", type=int, nargs="+", required=True)
    x = sub.add_parser("blocked", help="record a blocker outside the queue"); x.add_argument("id", type=int); x.add_argument("--reason", required=True)
    x = sub.add_parser("unblock", help="clear an outside blocker"); x.add_argument("id", type=int)
    x = sub.add_parser("blockers", help="tree of what an item waits on"); x.add_argument("id", type=int)

    x = sub.add_parser("send", help="send an alert, question, or note to an agent or to the holder of an item")
    x.add_argument("kind", choices=core.SEND_KINDS); x.add_argument("text")
    x.add_argument("--to", help="agent name"); x.add_argument("--item", type=int, help="the item it is about; without --to it goes to the holder")
    x.add_argument("--reply", type=int, help="message id this replies to (goes to its sender)")
    x = sub.add_parser("answer", help="answer a question"); x.add_argument("id", type=int); x.add_argument("text")
    x = sub.add_parser("inbox", help="your unread messages and questions waiting for your answer")
    x.add_argument("--all", action="store_true", help="read messages too")
    x.add_argument("--peek", action="store_true", help="do not mark them read")
    x = sub.add_parser("thread", help="a message and its replies")
    x.add_argument("id", type=int, nargs="?"); x.add_argument("--item", type=int, help="every message about this item")

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
    if args.cmd not in ("go", "plan"):
        core.activity(conn, actor)
    res = dispatch(conn, args, actor)
    with core.tx(conn):
        core.sync_needs_you(conn)  # the command may have made a human item ready, or sent a question to a person
    if args.json:
        print(json.dumps(res, indent=2, default=str))
    else:
        render(args, res)
    sys.stdout.flush()
    _footer(conn, res["agent"] if args.cmd in ("go", "plan") else actor)
    if not args.quiet and not args.json:
        h = _hint(args, res, actor)
        if h:
            print(h, file=sys.stderr)
    return 0


def _hint(a, res, actor):
    c = a.cmd
    if c in ("go", "plan"):
        return None
    if actor and c == "done":
        return f"next: river --as {actor} go"
    if actor and c == "release":
        return f"next: river --as {actor} go"
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
    if c == "inbox" and res:
        me = f"river --as {actor}"
        return (f"answer a question: {me} answer <id> \"...\"   reply: {me} send note --reply <id> \"...\"   "
                f"whole conversation: {me} thread <id>")
    if not actor and c in ("list", "show", "who", "capacity", "blockers"):
        return HINTS["no_actor"]
    return None


def _append_block(f):
    old = f.read_text() if f.exists() else ""
    if "Biggest River" in old:
        for prev in OLD_SNIPPETS:
            if prev in old and AGENT_SNIPPET not in old:
                f.write_text(old.replace(prev, AGENT_SNIPPET, 1))
                return f"{f.name}: updated the work queue block (adds: say plan, run river plan)"
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
            return core.project_add(conn, a.name, a.rank, a.notes, actor, a.path, a.target)
        if a.pcmd == "rank":
            return core.project_rank(conn, a.name, a.rank, actor)
        if a.pcmd == "path":
            return core.project_path(conn, a.name, a.path, actor)
        if a.pcmd == "describe":
            return core.project_describe(conn, a.name, a.text, actor)
        if a.pcmd == "show":
            return core.project_show(conn, a.name)
        if a.pcmd == "target":
            return core.project_target(conn, a.name, a.target, actor)
        if a.pcmd == "archive":
            return core.project_archive(conn, a.name, actor)
        return core.project_list(conn)
    if c == "target":
        if a.tcmd == "add":
            return core.target_add(conn, a.name, a.description, actor)
        if a.tcmd == "describe":
            return core.target_describe(conn, a.name, a.text, actor)
        if a.tcmd == "show":
            return core.target_show(conn, a.name)
        if a.tcmd == "own":
            return core.target_own(conn, a.name, actor)
        if a.tcmd == "release":
            return core.target_release(conn, a.name, actor)
        if a.tcmd == "give":
            return core.target_give(conn, a.name, a.to, actor)
        return core.target_list(conn)
    if c == "add":
        if a.mode and a.blocks is None:
            raise RiverError("--keep and --release go with --blocks <id>")
        if len(a.words) > 2:
            raise RiverError("put the title in quotes: river add [project] \"title\"")
        if len(a.words) == 2:
            project, title = a.words
        else:
            title = a.words[0]
            project = core.project_for_add(conn, os.getcwd(), a.blocks if a.blocks is not None else a.found_during)
        return core.item_add(conn, project, title, a.priority, a.notes, a.doer, a.after, actor,
                             a.context, a.touches, a.check, a.blocks, a.mode, a.found_during)
    if c == "edit":
        return core.item_edit(conn, a.id, a.title, a.notes, a.doer, a.project, actor, a.context, a.touches, a.check)
    if c == "list":
        return core.item_list(conn, a.project, a.status, a.all)
    if c == "show":
        return core.item_show(conn, a.id)
    if c == "status":
        return core.status(conn, a.recent)
    if c == "needs-you":
        return core.needs_you(conn, a.human, a.all)
    if c == "prompt":
        person = a.person or (actor if actor and conn.execute(
            "SELECT 1 FROM agents WHERE name=? AND kind='human'", (actor,)).fetchone() else None)
        if a.all:
            return {"prompt": core.prompt_for_all(conn, person)}
        if a.id is None:
            raise RiverError("give an item id, or --all")
        return {"prompt": core.prompt_for(conn, a.id, person)}
    if c == "notify":
        from . import notify
        if a.ncmd == "test":
            return notify.test(conn, a.channel)
        if a.ncmd == "status":
            return notify.status(conn)
        if a.ncmd == "setup":
            return notify.setup_ntfy(conn, a.url, a.token, actor)
        if a.once or a.now:
            return {"results": notify.run(conn, now_=a.now)}
        import threading
        print(f"river notify: sending every {core.setting(conn, 'notify_interval')} (ctrl-c stops)", flush=True)
        try:
            notify.loop(threading.Event())
        except KeyboardInterrupt:
            pass
        return {"results": []}
    if c == "log":
        return core.completed(conn, a.project, None if a.since == "all" else a.since)
    if c == "go":
        return core.go(conn, os.getcwd(), actor, a.project, a.role)
    if c == "plan":
        return core.plan(conn, os.getcwd(), actor, a.project)
    if c == "next":
        return core.next_item(conn, a.project, a.unblocks, a.claim, actor, a.limit, a.near, a.mine)
    if c == "claim":
        return core.claim(conn, a.id, actor)
    if c == "done":
        return core.done(conn, a.id, a.output, actor, a.ship, a.note)
    if c == "ship":
        return core.ship(conn, a.id, actor)
    if c == "release":
        return core.release(conn, a.id, a.note, actor)
    if c == "drop":
        return core.drop(conn, a.id, actor, a.note)
    if c == "takeover":
        return core.takeover(conn, a.id, a.note, actor)
    if c == "undo-takeover":
        return core.undo_takeover(conn, a.id, actor)
    if c == "reopen":
        return core.reopen(conn, a.id, actor)
    if c == "prio":
        return core.item_prio(conn, a.id, a.priority, actor)
    if c == "move":
        return core.item_move(conn, a.id, a.before, a.after, actor)
    if c == "dep":
        return core.dep_add(conn, a.id, a.on, actor, a.kind, a.mode)
    if c == "keep":
        return core.keep(conn, a.id, actor)
    if c == "push":
        return core.push(conn, a.id, a.to, a.note, actor)
    if c == "accept":
        return core.accept(conn, a.id, actor)
    if c == "decline":
        return core.decline(conn, a.id, a.note, actor)
    if c == "undep":
        return core.dep_remove(conn, a.id, a.on, actor)
    if c == "blocked":
        return core.block(conn, a.id, a.reason, actor)
    if c == "unblock":
        return core.unblock(conn, a.id, actor)
    if c == "blockers":
        return core.blockers(conn, a.id)
    if c == "send":
        return core.send(conn, a.kind, a.text, a.to, a.item, a.reply, actor)
    if c == "answer":
        return core.answer(conn, a.id, a.text, actor)
    if c == "inbox":
        return core.inbox(conn, actor, a.all, not a.peek)
    if c == "thread":
        if (a.id is None) == (a.item is None):
            raise RiverError("give a message id or --item <id>")
        if a.item is not None:
            return {"thread_id": None, "messages": core.item_messages(conn, a.item)}
        return core.thread(conn, a.id, actor)
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
                return {"key": a.key, "value": core.mask(a.key, core.setting(
                    conn, a.key, item_id=a.item, agent=a.agent,
                    project_id=core._project(conn, a.project)["id"] if a.project else None))}
            return core.config_list(conn)
        if a.ccmd == "set":
            return core.config_set(conn, a.key, a.value, a.project, a.item, a.agent, actor)
        return core.config_unset(conn, a.key, a.project, a.item, a.agent, actor)
    raise RiverError(f"unknown command {c}")


def render_status(res):
    rows = res["projects"]
    if not rows:
        print("(no projects: river project add <name>)")
    else:
        w = max(4, *(len(p["project"]) for p in rows))
        print(f"{'project':<{w}}  {'done':>4} {'open':>4} {'ready':>5} {'working':>7} {'human':>5} {'blocked':>7}")
        for p in rows:
            print(f"{p['project']:<{w}}  {p['done']:>4} {p['open']:>4} {p['ready']:>5} {p['in_progress']:>7} "
                  f"{p['human_waiting']:>5} {p['blocked']:>7}" + (f"  [{p['target']}]" if p["target"] else ""))
    print()
    print(f"Recently done ({len(res['recent'])}):" if res["recent"] else "Recently done: nothing yet")
    for it in res["recent"]:
        by = f" by {it['by_agent']}" if it["by_agent"] not in (None, "?") else ""
        print(f"  {it['closed_at'][:16].replace('T', ' ')}  #{it['id']:<4} [{it['project']}] {_cut(it['title'])}{by}")
    print()
    print("Working now:" if res["agents"] else "Working now: nobody registered")
    for ag in res["agents"]:
        holds = ", ".join(f"#{h['id']} {_cut(h['title'], 60)}" for h in ag["holds"]) or "nothing"
        owns = f"; owns {', '.join(ag['owns'])}" if ag["owns"] else ""
        print(f"  {ag['name']} ({ag['kind']}, {ag['state']}): {holds}{owns}")
    if res["human_waiting"]:
        print()
        print("Waiting on a human:")
        for h in res["human_waiting"]:
            print(f"  #{h['id']:<4} [{h['project']}] {_cut(h['title'])}")
    print()
    print(f"Open slots: {res['spare_slots']}   sessions with nothing to do: {res['excess_sessions']}")
    for adv in res["advice"]:
        print(" -", adv["text"])


PLAN_RULES = """You are a PLANNER. Talk with the user about what they want done, then write it into the queue.
Change the plan only; do not take or do the work (claims refuse for this session).
  Projects:      {r} project add <name> --description "..." [--path <dir>] [--target <t>]   (describe, rank, target)
  Items:         {r} add <project> "<title>" --doer ai|human --context "..." --touches <files> --check "<cmd>"
  Order:         {r} dep <id> --on <id> [--kind feeds|conflicts]   Importance: {r} prio <id> 0 (on the outcome only)
  Fix:           {r} edit <id> ...   {r} move <id> --before <id>   {r} drop <id>   {r} blocked <id> --reason "..."
  Progress:      river status   river log --since 7d   river blockers <id>
Ask the user about each open question below that matters to what they want. The full guide: river guide planner
When the user wants work done in this session instead: {r} go"""


def render_plan(b):
    me = b["agent"]
    r = f"river --as {me}"
    out = [f"You are river agent {me}. Role: PLANNER."
           + (f" Focus: {', '.join(b['projects'])}." if b["projects"] else " Focus: every project.")]
    if b["new_name"]:
        out.append(f"Your shell may not keep environment variables, so pass --as {me} on every river command.")
    out += ["", PLAN_RULES.format(r=r), "", "OVERVIEW"]
    print("\n".join(out))
    render_status(b["status"])
    q = b["questions"]
    out = ["", "OPEN QUESTIONS"]
    def section(title, rows, fmt, limit=10):
        if not rows:
            return
        out.append(f"{title} ({len(rows)}):")
        out.extend("  " + fmt(x) for x in rows[:limit])
        if len(rows) > limit:
            out.append(f"  ... {len(rows) - limit} more")
    section("Projects with no description", q["projects_without_description"],
            lambda n: f"{n}   ({r} project describe {n} \"...\")")
    section("Stuck on something outside the queue", q["stuck"],
            lambda x: f"#{x['id']:<4} [{x['project']}] {_cut(x['title'], 60)}: {x['reason']}"
                      + (f" (holds up {x['holds_up']})" if x["holds_up"] else ""))
    section("Waiting on a human", q["human_waiting"], lambda x: f"#{x['id']:<4} [{x['project']}] {_cut(x['title'])}")
    section("Items with no notes or context", q["items_without_notes"],
            lambda x: f"#{x['id']:<4} [{x['project']}] {_cut(x['title'])}")
    if len(out) == 2:
        out.append("(none)")
    print("\n".join(out))


def render_go(b):
    me = b["agent"]
    r = f"river --as {me}"
    out = []
    out.append(f"You are river agent {me}. Role: {b['role'].upper()}. ({b['why']})")
    if b["new_name"]:
        out.append(f"Your shell may not keep environment variables, so pass --as {me} on every river command.")
    for n in b["projects"]:
        d = b["descriptions"].get(n)
        out.append(f"Project {n}: {d}" if d else f"Project {n}.")
    out.append("")
    it = b.get("item")
    if it:
        out.append(f"YOUR ITEM #{it['id']}: {it['title']}")
        if it.get("found_during"):
            out.append(f"  (found during #{it['found_during']}; now it is the most important ready item)")
        if it["notes"]:
            out.append(f"  notes: {it['notes']}")
        out += _context_lines(it)
        if it["waits_on_detail"]:
            out.append("  waited on (all done): " + ", ".join(f"#{d['id']} {d['title']}" for d in it["waits_on_detail"]))
        if it["unblocks_detail"]:
            out.append("  unblocks: " + ", ".join(f"#{d['id']} {d['title']}" for d in it["unblocks_detail"]))
        if it.get("output"):
            out.append(f"  earlier output: {it['output']}")
        if b["role"] == "deployer":
            t = b["target"]
            if not t["description"]:
                out.append(f"  target {t['name']}: (no description: {r} target describe {t['name']} \"how it deploys\")")
            elif t["description"] != it.get("context"):
                out.append(f"  target {t['name']}: {t['description']}")
            out.append("  ships:")
            for d in b["ships"]:
                out.append(f"    #{d['id']} {d['title']} ({d['status']})")
            for n in b.get("next_deploy", []):
                out.append(f"  next deploy #{n['id']} is collecting: " + (", ".join(f"#{d['id']}" for d in n["waits_on_detail"]) or "nothing yet"))
            out += ["",
                    "Deploy as the target description says, run its checks, then put the release id or",
                    f"deployed commit in the output: {r} done {it['id']} --output \"<release id, checks passed>\""]
        if b.get("has_history") and not b.get("new_name"):
            out += [
                "",
                "Rules as before. Short form:",
                f"  needs something first: {r} add \"<title>\" --blocks {it['id']} --keep|--release",
                f"  other work found:      {r} add \"<title>\" --found-during {it['id']}",
                f"  a step for the user:   {r} add \"...\" --doer human --context \"...\" --blocks {it['id']}",
                f"  vague item: make a reasonable choice and say what you chose in --output.",
                "",
            ]
        else:
            out += [
                "",
                "Rules:",
                f"  - Do this item only. Read `{r} show {it['id']}` again if you need the links.",
                f"  - The item is vague: make a reasonable choice and say what you chose in --output. Ask the user",
                f"    (a human item, below) only when a wrong guess would be costly.",
                f"  - It needs something first: {r} add \"<title>\" --blocks {it['id']} --keep   (small; you do it now)",
                f"    or ... --blocks {it['id']} --release   (large or better for someone else; then run go again).",
                f"  - You find other work: {r} add \"<title>\" --found-during {it['id']}. Do not do it now.",
                f"  - Waiting on something outside the queue: {r} blocked {it['id']} --reason \"<what>\", release, run go again.",
                f"  - You need the user (a decision, an approval, an account or payment step): put it in the queue, not only in chat:",
                f"    {r} add \"<what to decide or do>\" --doer human --context \"<exactly what, where the material is>\" --blocks {it['id']}",
                f"    That is what notifies them. A quick question instead: {r} send question --to "
                + ("|".join(b.get("humans") or []) or "<person>") + " \"...\" --item " + str(it["id"]),
                "",
            ]
        out += [
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
    msg = _unread_text(b.get("messages") or {"unread": 0, "questions": 0}, me)
    if msg:
        out.append("")
        out.append(f"Messages for you: {msg}. Read them before you start.")
    if b.get("human_waiting"):
        out.append("")
        out.append(f"Waiting on the user (tell them; if you can do one or work around it: {r} takeover <id> --note \"how\"):")
        for h in b["human_waiting"]:
            blocks = "; ".join(f"blocks #{d['id']} {d['title']} (P{d['priority']})" for d in h.get("blocks", [])[:2])
            out.append(f"  #{h['id']} {h['title']}" + (f"  ({blocks})" if blocks else ""))
    print("\n".join(out))


def render(a, res):
    c = a.cmd
    if c == "go":
        return render_go(res)
    if c == "plan":
        return render_plan(res)
    if c == "project":
        if isinstance(res, dict) and "ready_count" in res:
            print(f"{res['name']} (rank {res['rank']})")
            print("  " + (res["description"] or "(no description: river project describe " + res["name"] + " \"...\")"))
            print("  target: " + (res.get("target") or "none (river project target " + res["name"] + " <target>)"))
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
            print(f"{p['rank']:>2}. {p['name']}" + (f"  ({p['open_items']} open)" if "open_items" in p else "")
                  + (f"  [target {p['target']}]" if p.get("target") else ""))
            if p.get("notes"):
                print(f"    {p['notes']}")
        return
    if c == "target":
        if isinstance(res, list):
            if not res:
                print("(no targets: river target add <name> --description \"how it deploys\")")
            for t in res:
                print(f"{t['name']}  ({t['projects']} project{'' if t['projects'] == 1 else 's'})"
                      + (f"  owner {t['owner']}" if t["owner"] else ""))
                if t["description"]:
                    print(f"    {t['description']}")
            return
        print(res["name"])
        print("  " + (res["description"] or f"(no description: river target describe {res['name']} \"how it deploys\")"))
        if res["owner"]:
            left = core._short(core.parse_iso(res["owner_expires_at"]) - core.now())
            print(f"  owner: {res['owner']} ({left} left; any command by {res['owner']} renews it)")
        else:
            print(f"  owner: nobody (take it: river target own {res['name']})")
        print(f"  projects ({len(res['projects'])}):")
        for p in res["projects"]:
            print(f"    {p['name']}  ({p['open_items']} open)")
        if not res["projects"]:
            print(f"    (none: river project target <project> {res['name']})")
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
                for line in _context_lines(it, "       "):
                    print(line)
        return
    if c == "blockers":
        _print_tree(res)
        return
    if c == "status":
        return render_status(res)
    if c == "notify":
        if "subscribe" in res:
            print("\n".join(res["subscribe"]))
            return
        if "results" in res:
            if not res["results"]:
                print("(nothing to send)")
            for r in res["results"]:
                if r["sent"]:
                    print(f"{r['channel']}: sent {r['rows']} in one message ({r['title']})")
                elif r.get("error"):
                    print(f"{r['channel']}: failed for {r['rows']}: {r['error']} (retries on the next run)")
                else:
                    print(f"{r['channel']}: {r['rows']} waiting for the batch window ({r['waiting']} left; --now sends them)")
        elif "channels" in res:
            print(f"interval {res['interval']}, batch window {res['batch_window']}")
            if not res["channels"]:
                print("(no channels: river config set notify_channels log)")
            for ch in res["channels"]:
                notes = [] if ch["adapter"] else ["no adapter"]
                if not ch["configured"]:
                    notes.append("not in notify_channels")
                print(f"{ch['channel']}: {ch['pending']} pending, last sent {ch['last_sent'] or 'never'}"
                      + (f", last error: {ch['last_error']}" if ch["last_error"] else "")
                      + (f" ({'; '.join(notes)})" if notes else ""))
        else:
            print(f"{res['channel']}: " + ("test sent" if res["ok"] else f"failed: {res['error']}"))
        return
    if c == "prompt":
        print(res["prompt"])
        return
    if c == "needs-you":
        if not res:
            print("(nothing needs a person now)")
        for e in res:
            who = e["human"] or "any person"
            state = f"closed {e['closed_at']} ({e['close_reason']})" if e["closed_at"] else f"open since {e['opened_at']}"
            print(f"[{e['id']}] for {who}: {e['summary']}  ({state})")
            for n in e["notifications"]:
                print(f"      {n['channel']}: {n['state']}" + (f" after {n['attempts']} tries: {n['last_error']}" if n["last_error"] else ""))
        return
    if c == "log":
        window = f"last {res['since']}" if res["since"] else "all time"
        if not res["items"]:
            print(f"(nothing done in the {window})" if res["since"] else "(nothing done yet)")
        for d in res["by_day"]:
            print(f"{d['day']}  ({len(d['items'])} done)")
            for it in d["items"]:
                by = f" by {it['by_agent']}" if it["by_agent"] not in (None, "?") else ""
                print(f"  #{it['id']:<4} [{it['project']}] {it['title']}  ({it['closed_at'][11:16]}{by})")
                if it["output"]:
                    print(f"        {it['output']}")
        if res["progress"]:
            print("\nProgress:")
            w = max(len(p["project"]) for p in res["progress"])
            for p in res["progress"]:
                pct = round(100 * p["done"] / p["total"]) if p["total"] else 0
                bar = "#" * round(pct / 5) + "." * (20 - round(pct / 5))
                recent = f", +{p['done_in_window']} in the {window}" if res["since"] else ""
                print(f"  {p['project']:<{w}}  {bar} {pct:>3}%  {p['done']}/{p['total']} done{recent}")
        return
    if c == "send":
        to = res["to_agent"] or f"the next holder of #{res['item_id']} (nobody holds it now)"
        print(f"sent #{res['id']} {res['kind']} to {to}")
        return
    if c == "answer":
        print(f"answered: sent #{res['id']} to {res['to_agent']}; question #{res['reply_to']} is closed")
        return
    if c == "inbox":
        if not res:
            print("(inbox empty)")
        for m in res:
            print(_fmt_msg(m))
        return
    if c == "thread":
        if not res["messages"]:
            print("(no messages)")
        for m in res["messages"]:
            print(_fmt_msg(m, "  " if m["reply_to"] else ""))
        return
    if c == "who":
        if not res:
            print("(nobody)")
        for ag in res:
            holds = ", ".join(f"#{h['id']} {h['title']}" for h in ag["holds"]) or "nothing"
            note = f" — {ag['note']}" if ag["note"] else ""
            print(f"{ag['name']} ({ag['kind']}, {ag['state']}){note}\n    holds: {holds}"
                  + (f"\n    owns: {', '.join(o['name'] for o in ag['owns'])}" if ag.get("owns") else ""))
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
            print(json.dumps(res, ensure_ascii=False))
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
