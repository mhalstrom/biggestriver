"""Local web page for Biggest River. Binds to 127.0.0.1 only."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import core
from .core import RiverError

STATIC = Path(__file__).resolve().parent / "static"
PKG = Path(__file__).resolve().parent
DEV = {"on": False}
# The desktop app (desktop/) starts the server with RIVER_DESKTOP=1: the app updates itself, so the
# page's git Update button does not apply there.
DESKTOP = os.environ.get("RIVER_DESKTOP") == "1"
BOOT = str(time.time())  # changes when the server restarts; the page waits for a new one after an update


# Types the page's own files use; anything else under static is sent as bytes.
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".map": "application/json", ".json": "application/json", ".svg": "image/svg+xml",
                ".png": "image/png", ".woff2": "font/woff2", ".txt": "text/plain; charset=utf-8",
                ".md": "text/plain; charset=utf-8"}


def static_file(path, root=None):
    """The (bytes, content type) of a file under river/static for a URL path, or None.

    Refuses anything that resolves outside the folder: '..', symlinks out, encoded slashes, hidden files."""
    from urllib.parse import unquote
    root = (root or STATIC).resolve()
    rel = unquote(path.lstrip("/")) if "%2f" not in path.lower() and "%5c" not in path.lower() else None
    if not rel or "\\" in rel or "\0" in rel or any(part.startswith(".") for part in rel.split("/")):
        return None
    f = (root / rel).resolve()
    if root not in f.parents or not f.is_file():
        return None
    name = f.name.lower()
    ctype = "text/plain; charset=utf-8" if name.startswith("license") else STATIC_TYPES.get(f.suffix.lower(), "application/octet-stream")
    return f.read_bytes(), ctype


def _watched():
    """The server's code and every page file, in subfolders too (components/); vendor/ never changes by hand."""
    return sorted(PKG.glob("*.py")) + sorted(
        f for f in STATIC.rglob("*") if f.is_file() and "vendor" not in f.relative_to(STATIC).parts[:1]
        and not any(part.startswith(".") for part in f.relative_to(STATIC).parts))


def _build_id():
    """Changes whenever a watched file changes; the page reloads itself in dev mode."""
    return str(max((f.stat().st_mtime_ns for f in _watched()), default=0))


def _code_id():
    """Changes when the server's Python code changes (git pull, a commit, an edit). The page files are
    read from disk on every request, but the code runs as it was when the server started."""
    return str(max((f.stat().st_mtime_ns for f in PKG.glob("*.py")), default=0))


BOOT_CODE = _code_id()


def code_stale():
    """True when the river code on disk is newer than the code this server runs: it needs a restart."""
    return _code_id() != BOOT_CODE

def _applescript_str(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# The page opens agent sessions in a new Terminal tab (macOS) or console window (Windows). Tests set it.
PLATFORM = sys.platform


def _can_open_terminal(runner, hint):
    if PLATFORM not in ("darwin", "win32") and runner is None:
        raise RiverError(f"starting an agent from the page works on macOS and Windows only; start one yourself: {hint}")


def launch_agent(conn, project=None, runner=None, agent=None, actor=None):
    """Open a Terminal window in the project folder of the most important ready agent item and run
    the command of the chosen launch_agents entry there (default `claude go --remote-control`), so one click starts one
    agent session. macOS and Windows."""
    # A session that waits for work in that project (river wait) gets the item: no new session needed.
    top = core.launch_target(conn, project, agent)
    waiting = core.waiting_agent_for(conn, top["project"])
    if waiting:
        core.push(conn, top["item"]["id"], waiting, "from the Start button: you were waiting for work", actor)
        return {**top, "pushed_to": waiting}
    _can_open_terminal(runner, "cd <project folder> && claude go")
    t = core.launch_target(conn, project, agent)
    _open_terminal(t, {}, runner)
    return t


def dispatch_item(conn, item_id, runner=None, agent=None, actor=None):
    """Start work on one ready item: a session that waits for work in its project gets it (push);
    else river names a new session, reserves the item for it (push), and opens the chosen agent in the
    project folder with RIVER_AGENT set to that name, so its first river go takes this item."""
    import secrets
    t = core.launch_target(conn, agent=agent, item=item_id)
    waiting = core.waiting_agent_for(conn, t["project"])
    if waiting:
        core.push(conn, t["item"]["id"], waiting, "from the page: Dispatch; you were waiting for work", actor)
        return {**t, "pushed_to": waiting}
    _can_open_terminal(runner, f"cd <project folder> && claude go, then river push {t['item']['id']} --to <its name>")
    name = f"{t['project']}-{secrets.token_hex(2)}"
    core.register(conn, name, note=f"started from the page for #{t['item']['id']}")
    core.push(conn, t["item"]["id"], name, "from the page: Dispatch started this session for it", actor)
    _open_terminal(t, {"RIVER_AGENT": name}, runner)
    return {**t, "session_name": name}


def open_agent_on(conn, item_id, runner=None, agent=None, person=None, actor=None):
    """Open an agent session for one item from its drawer. A ready item: Dispatch. A person's item: a
    session that does it together with the person. An item that waits: a session that first takes what
    blocks it. The session learns which from RIVER_FOCUS, which its river go reads."""
    it = core.item_show(conn, item_id)
    if it["status"] not in core.OPEN_STATES:
        raise RiverError(f"#{item_id} is {it['status']}; there is nothing to open an agent on")
    mine = person and it["assignee"] == person  # a person claimed it (Claim next): help them do it
    if it["status"] == "in_progress" and not mine:
        raise RiverError(f"#{item_id} is in progress by {it['assignee']}; message them instead")
    if it["doer"] == "human" or mine:
        focus = f"help:{it['id']}" + (f"@{person}" if person else "")
    elif it["ready"]:
        return dispatch_item(conn, it["id"], runner, agent, actor)
    else:
        focus = f"unblock:{it['id']}"
    p = core._project(conn, it["project"])
    if not p["path"]:
        raise RiverError(f"project {p['name']} has no folder, so river cannot start a session there: "
                         f"river project path {p['name']} <folder>")
    return {**_open_focused(conn, p, focus, runner, agent), "item": {"id": it["id"], "title": it["title"]}}


def open_needs_you(conn, runner=None, agent=None, person=None):
    """Needs you, Open agent: a session that works through everything that waits on the person, with
    them (the Copy prompt text), in the folder of the most important such item's project."""
    ann = core.annotate(conn)
    items = sorted((a for a in ann.values() if a["ready"] and a["doer"] == "human" and not a["project_archived"]),
                   key=lambda a: a["sort_key"])
    projects = [core._project(conn, a["project"]) for a in items] + [
        core._project(conn, p["name"]) for p in core.project_list(conn)]
    p = next((x for x in projects if x["path"]), None)
    if p is None:
        raise RiverError("no project has a folder, so river cannot start a session: river project path <name> <folder>")
    return _open_focused(conn, p, "needs:" + (f"@{person}" if person else ""), runner, agent)


def deploy_now(conn, target, review=False, runner=None, agent=None, actor=None):
    """Targets tab, Deploy now (or Review and deploy): when the deploy item (or its review) is ready and
    the target has no owner to alert, open an agent in a folder of the target's projects that takes it."""
    r = core.deploy_now(conn, target, review, actor)
    if not r["ready"] or r.get("alerted"):
        return r
    p = next((core._project(conn, x["name"]) for x in core.target_show(conn, target)["projects"]
              if core._project(conn, x["name"])["path"]), None)
    if p is None:
        raise RiverError(f"no project of target {target} has a folder, so river cannot start a session there: "
                         f"river project path <name> <folder>")
    return {**r, **_open_focused(conn, p, f"{'review' if review else 'deploy'}:{target}", runner, agent)}


def _open_focused(conn, p, focus, runner, agent):
    """Open the chosen agent in a project folder with RIVER_FOCUS set; its river go reads it."""
    _can_open_terminal(runner, f"cd {p['path']}, set RIVER_FOCUS={focus}, then claude go")
    t = {"project": p["name"], "path": p["path"], "focus": focus,
         **core._launch_agent_cmd(conn, p["id"], agent), "launch_in": core.setting(conn, "launch_in", project_id=p["id"])}
    _open_terminal(t, {"RIVER_FOCUS": focus}, runner)
    return t


def _open_terminal(t, env, runner=None):
    """Run the agent command (t["command"]) in the project folder (t["path"]) with env set: in a new
    Terminal tab or window on macOS (launch_in), in a new console window on Windows. runner (tests) gets
    the AppleScript on macOS, and {"args", "cwd", "env"} on Windows."""
    import os
    import shlex
    import subprocess
    # The agent uses the same queue as this page: a page on a RIVER_DB queue starts agents on it too.
    if os.environ.get("RIVER_DB"):
        env = {"RIVER_DB": str(core.db_path()), **env}
    if PLATFORM == "win32":
        # cmd /k keeps the window open when the agent ends; the command line goes to cmd as written.
        spec = {"args": f"cmd /k {t['command']}", "cwd": t["path"], "env": dict(env)}
        try:
            (runner or (lambda s: subprocess.Popen(s["args"], cwd=s["cwd"], env={**os.environ, **s["env"]},
                                                   creationflags=subprocess.CREATE_NEW_CONSOLE)))(spec)
        except OSError as e:
            raise RiverError(f"could not open a console window in {t['path']}: {e}")
        return
    shell = f"cd {shlex.quote(t['path'])} && " + "".join(f"{k}={shlex.quote(v)} " for k, v in env.items()) + t["command"]
    cmd = _applescript_str(shell)
    if t["launch_in"] == "tab":
        # Terminal has no "new tab" command: press Command-T in it, then run the command in that tab.
        # A key press goes to the app in front, so wait until Terminal is in front (else Command-T opens a
        # browser tab). Then wait until the new tab exists (else the command runs in the old tab, which
        # can hold a busy agent). Terminal's AppleScript lists each tab of a tabbed window as a window of
        # its own, so a new tab shows as one more window, or as one more tab of the front window. Pressing keys needs the Accessibility permission once. Without it, or
        # when no new tab appears, fall back to a new window.
        script = "\n".join([
            'tell application "Terminal"', '  activate', '  set hasWindow to (count of windows) > 0', 'end tell',
            'if hasWindow then', '  try',
            '    tell application "System Events"',
            '      repeat 40 times',
            '        if frontmost of process "Terminal" then exit repeat',
            '        delay 0.05',
            '      end repeat',
            '      if not (frontmost of process "Terminal") then error "Terminal is not in front"',
            '    end tell',
            '    tell application "Terminal" to set {windowsBefore, tabsBefore} to {count of windows, count of tabs of front window}',
            '    tell application "System Events" to tell process "Terminal" to keystroke "t" using command down',
            '    set gotTab to false',
            '    repeat 60 times',
            '      delay 0.05',
            '      tell application "Terminal" to set gotTab to (count of windows) > windowsBefore or (count of tabs of front window) > tabsBefore',
            '      if gotTab then exit repeat',
            '    end repeat',
            '    if not gotTab then error "Terminal opened no new tab"',
            f'    tell application "Terminal" to do script {cmd} in selected tab of front window',
            '  on error', f'    tell application "Terminal" to do script {cmd}', '  end try',
            'else', f'  tell application "Terminal" to do script {cmd}', 'end if'])
    else:
        script = f'tell application "Terminal"\n  activate\n  do script {cmd}\nend tell'
    try:
        (runner or (lambda s: subprocess.run(["osascript", "-e", s], check=True, capture_output=True,
                                             text=True, timeout=20)))(script)
    except (OSError, subprocess.SubprocessError) as e:
        why = (getattr(e, "stderr", "") or str(e)).strip()
        raise RiverError(f"could not open Terminal: {why}. macOS may ask once to let river control Terminal "
                         f"(System Settings, Privacy & Security, Automation)")


REPO = PKG.parent


def _git(repo, *args, timeout=60):
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise RiverError(f"git {args[0]}: {e}")
    if r.returncode:
        raise RiverError(f"git {args[0]}: {(r.stderr or r.stdout).strip()}")
    return r.stdout.strip()


def _no_update_in_app():
    if DESKTOP:
        raise RiverError("the desktop app updates itself; the git Update does not apply here")


def update_status(repo=REPO, fetch=True):
    """Whether this river install (a git clone) is behind its upstream branch: the page's Update button."""
    if not (Path(repo) / ".git").exists():
        return {"git": False}
    out = {"git": True, "fetch_error": None}
    if fetch:
        try:
            _git(repo, "fetch", "--quiet")
        except RiverError as e:
            out["fetch_error"] = str(e)
    out["head"] = _git(repo, "rev-parse", "--short", "HEAD")
    out["branch"] = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    try:
        out["upstream"] = _git(repo, "rev-parse", "--abbrev-ref", "@{u}")
    except RiverError:
        return {**out, "upstream": None, "behind": 0, "ahead": 0, "commits": []}
    out["behind"] = int(_git(repo, "rev-list", "--count", "HEAD..@{u}"))
    out["ahead"] = int(_git(repo, "rev-list", "--count", "@{u}..HEAD"))
    out["commits"] = _git(repo, "log", "--format=%h %s", "-20", "HEAD..@{u}").splitlines() if out["behind"] else []
    return out


def _restart_soon():
    """Start the server process again after the reply goes out, so it runs the new code."""
    def go():
        time.sleep(0.8)
        print("updated; restarting", flush=True)
        os.execv(sys.executable, [sys.executable] + sys.argv)
    threading.Thread(target=go, daemon=True).start()


def update_apply(repo=REPO, restart=_restart_soon):
    """Fast-forward this river install to its upstream branch, then restart the server. Agents and the
    command pick up the new code on their next run, because `river` and the skills link into this folder."""
    st = update_status(repo)
    if not st["git"]:
        raise RiverError("this river is not a git clone; update it the way you installed it")
    if st["fetch_error"]:
        raise RiverError(f"could not check for updates: {st['fetch_error']}")
    if not st["upstream"]:
        raise RiverError(f"branch {st['branch']} has no upstream to update from")
    if not st["behind"]:
        return {**st, "updated": False}
    if st["ahead"]:
        raise RiverError(f"this clone has {st['ahead']} commit(s) that {st['upstream']} does not have, so it "
                         f"cannot fast-forward; push or rebase them in {repo} yourself, then update")
    try:
        _git(repo, "merge", "--ff-only", "@{u}")
    except RiverError as e:
        raise RiverError(f"could not fast-forward {repo}: local changes are in the way; commit or stash them "
                         f"(git status), then update.\n{e}")
    restart()
    return {**update_status(repo, fetch=False), "updated": True, "from": st["head"], "commits": st["commits"]}


# Agent CLIs the setup guide offers for the Start button. Each gets an explicit first prompt, so it works
# even where the agent does not read the project's instruction file. {river_dir} becomes the folder of the
# queue: Codex's sandbox writes only in the project folder unless --add-dir names another one.
KNOWN_AGENTS = [
    ("Claude Code", "claude", "claude go --remote-control"),
    ("Codex", "codex", 'codex --add-dir {river_dir} "run river go in this folder and follow the briefing"'),
    ("Grok", "grok", 'grok "run river go in this folder and follow the briefing"'),
    ("OpenCode", "opencode", 'opencode --prompt "run river go in this folder and follow the briefing"'),
    ("Gemini", "gemini", 'gemini -i "run river go in this folder and follow the briefing"'),
]


def _agent_cmd(cmd):
    """A KNOWN_AGENTS command with {river_dir} filled in for this computer."""
    import shlex
    d = str(core.db_path().expanduser().resolve().parent)
    return cmd.replace("{river_dir}", f'"{d}"' if PLATFORM == "win32" else shlex.quote(d))


def _block_state(path):
    from .cli import AGENT_SNIPPET, _imports_agents
    if not path.exists():
        return "missing"
    text = path.read_text(errors="replace")
    if path.name == "CLAUDE.md" and _imports_agents(text):
        return _block_state(path.with_name("AGENTS.md"))  # Claude Code reads AGENTS.md through the import
    return "current" if AGENT_SNIPPET in text else "old" if "Biggest River" in text else "missing"


def setup_status(conn):
    """What the setup guide shows: each check, and whether it is done."""
    import shutil
    from .cli import instructions_layout
    folders = {}
    for p in core.project_list(conn):
        if p["path"]:
            folders.setdefault(p["path"], []).append(p["name"])
    skills = Path("~/.claude/skills").expanduser()
    agents = core.parse_launch_agents(core.setting(conn, "launch_agents"))
    have = {label for label, _ in agents}
    return {
        "done": core.setting(conn, "setup_done") == "on",
        "folders": [{"path": d, "projects": names, "exists": Path(d).is_dir(),
                     "claude_md": _block_state(Path(d, "CLAUDE.md")), "agents_md": _block_state(Path(d, "AGENTS.md")),
                     "layout": instructions_layout(d) if Path(d).is_dir() else None}
                    for d, names in folders.items()],
        "projects_without_folder": [p["name"] for p in core.project_list(conn) if not p["path"]],
        "people": [r["name"] for r in conn.execute("SELECT name FROM agents WHERE kind='human' ORDER BY name")],
        "claude_home": Path("~/.claude").expanduser().is_dir(),
        "skills": {n: ("installed" if (skills / n / "SKILL.md").is_file() else "missing") for n in ("river", "river-planner")},
        "launch_agents": [label for label, _ in agents],
        "agent_clis": [{"label": label, "found": bool(shutil.which(exe)), "added": label in have, "command": _agent_cmd(cmd)}
                       for label, exe, cmd in KNOWN_AGENTS],
        "notify_channels": core._channels(core.setting(conn, "notify_channels")),
        "ntfy_ready": bool(core.setting(conn, "ntfy_topic")),
    }


def setup_block(conn, path, move=None):
    """Add or update the work queue block for every agent in a registered project folder (river init):
    AGENTS.md holds it and CLAUDE.md imports it; move=True first moves the rules in CLAUDE.md to AGENTS.md."""
    from .cli import setup_instructions
    folder = Path(path).expanduser().resolve()
    if folder not in {Path(p["path"]).resolve() for p in core.project_list(conn) if p["path"]}:
        raise RiverError(f"{path} is not the folder of a river project")
    if not folder.is_dir():
        raise RiverError(f"{path} does not exist")
    return setup_instructions(folder, move)


def setup_skills():
    from .cli import install_skills
    return install_skills(Path("~/.claude/skills").expanduser())


def setup_agent_add(conn, label, actor=None):
    """Add one of KNOWN_AGENTS to launch_agents (the Start button's list)."""
    cmd = next((_agent_cmd(c) for lab, _, c in KNOWN_AGENTS if lab == label), None)
    if cmd is None:
        raise RiverError(f"unknown agent {label!r}")
    agents = core.parse_launch_agents(core.setting(conn, "launch_agents"))
    if label not in {a for a, _ in agents}:
        agents.append((label, cmd))
        core.config_set(conn, "launch_agents", "; ".join(f"{a}={c}" for a, c in agents), actor=actor)
    return [a for a, _ in agents]


def setup_ntfy(conn, actor=None):
    from . import notify
    return notify.setup_ntfy(conn, actor=actor)


# Operations the page may call. Each maps JSON args to one core function.
OPS = {
    "project_add": lambda c, a, who: core.project_add(c, a["name"], a.get("rank"), a.get("notes", ""), who),
    "project_rank": lambda c, a, who: core.project_rank(c, a["name"], a["rank"], who),
    "project_describe": lambda c, a, who: core.project_describe(c, a["name"], a["text"], who),
    "item_add": lambda c, a, who: core.item_add(c, a["project"], a["title"], int(a.get("priority", 2)),
                                                a.get("notes", ""), a.get("doer", "any"),
                                                [int(x) for x in a.get("after", [])], who,
                                                a.get("context", ""), a.get("touches"), a.get("check", ""),
                                                a.get("blocks"), a.get("mode"), a.get("found_during"),
                                                [int(x) for x in a.get("feeds", [])], a.get("due"),
                                                goals=a.get("goals")),
    "item_edit": lambda c, a, who: core.item_edit(c, a["id"], a.get("title"), a.get("notes"), a.get("doer"),
                                                  a.get("project"), who, a.get("context"), a.get("touches"),
                                                  a.get("check"), a.get("due"), goals=a.get("goals"),
                                                  untag=a.get("untag")),
    "goal_add": lambda c, a, who: core.goal_add(c, a["project"], a["name"], a.get("outcome", ""), a.get("done_when", ""),
                                                who),
    "goal_edit": lambda c, a, who: core.goal_edit(c, a["name"], a.get("outcome"), a.get("done_when"), a.get("new_name"), who),
    "goal_rank": lambda c, a, who: core.goal_rank(c, a["name"], a["rank"], who),
    "goal_own": lambda c, a, who: core.goal_own(c, a["name"], who),
    "goal_release": lambda c, a, who: core.goal_release(c, a["name"], who),
    "goal_done": lambda c, a, who: core.goal_done(c, a["name"], a.get("result", ""), who, bool(a.get("drop_open"))),
    "goal_reopen": lambda c, a, who: core.goal_reopen(c, a["name"], who),
    "prio": lambda c, a, who: core.item_prio(c, a["id"], a["priority"], who),
    "move": lambda c, a, who: core.item_move(c, a["id"], a.get("before"), a.get("after"), who),
    "dep_add": lambda c, a, who: core.dep_add(c, a["id"], [int(x) for x in a["on"]], who, a.get("kind", "blocks")),
    "dep_remove": lambda c, a, who: core.dep_remove(c, a["id"], [int(x) for x in a["on"]], who),
    "claim": lambda c, a, who: core.claim(c, a["id"], who),
    "push": lambda c, a, who: core.push(c, a["id"], a["to"], a.get("note"), who),
    "push_cancel": lambda c, a, who: core.cancel_push(c, a["id"], who),
    "undo_takeover": lambda c, a, who: core.undo_takeover(c, a["id"], who),
    "takeover_seen": lambda c, a, who: core.takeover_seen(c, a["id"], who),
    "accept": lambda c, a, who: core.accept(c, a["id"], who),
    "decline": lambda c, a, who: core.decline(c, a["id"], a.get("note"), who),
    "next_claim": lambda c, a, who: core.next_item(c, a.get("project"), a.get("unblocks"), True, who, 1, a.get("near"),
                                                   bool(a.get("mine"))),
    "done": lambda c, a, who: core.done(c, a["id"], a.get("output"), who),
    "release": lambda c, a, who: core.release(c, a["id"], a.get("note"), who),
    "drop": lambda c, a, who: core.drop(c, a["id"], who),
    "reopen": lambda c, a, who: core.reopen(c, a["id"], who),
    "block": lambda c, a, who: core.block(c, a["id"], a.get("reason"), who, a.get("until")),
    "unblock": lambda c, a, who: core.unblock(c, a["id"], who),
    "replanned": lambda c, a, who: core.replanned(c, a["id"], a.get("note"), who),
    "register": lambda c, a, who: core.register(c, a["name"], bool(a.get("human")), a.get("note", "")),
    "config_set": lambda c, a, who: core.config_set(c, a["key"], str(a["value"]), a.get("project"), a.get("item"),
                                                    a.get("agent"), who),
    "config_unset": lambda c, a, who: core.config_unset(c, a["key"], a.get("project"), a.get("item"), a.get("agent"), who),
    "answer": lambda c, a, who: core.answer(c, int(a["msg"]), a["body"], who),
    "message_read": lambda c, a, who: _message_read(c, int(a["msg"])),
    "send": lambda c, a, who: core.send(c, a["kind"], a["body"], a.get("to"), a.get("item"), a.get("reply"), who),
    "offer": lambda c, a, who: core.offer(c, a["body"], int(a["item"]), a.get("to"), who),
    "give": lambda c, a, who: core.give(c, int(a["id"]), a["to"], who),
    "split": lambda c, a, who: core.split(c, int(a["id"]), [t for t in a["titles"] if t.strip()], who),
    "setup_block": lambda c, a, who: setup_block(c, a["path"], a.get("move")),
    "setup_skills": lambda c, a, who: setup_skills(),
    "setup_agent_add": lambda c, a, who: setup_agent_add(c, a["label"], who),
    "setup_ntfy": lambda c, a, who: setup_ntfy(c, who),
    "launch_agent": lambda c, a, who: launch_agent(c, a.get("project"), agent=a.get("agent"), actor=who),
    "dispatch_item": lambda c, a, who: dispatch_item(c, int(a["id"]), agent=a.get("agent"), actor=who),
    "open_agent_on": lambda c, a, who: open_agent_on(c, int(a["id"]), agent=a.get("agent"), person=a.get("person"), actor=who),
    "open_needs_you": lambda c, a, who: open_needs_you(c, agent=a.get("agent"), person=a.get("person")),
    "deploy_now": lambda c, a, who: deploy_now(c, a["target"], bool(a.get("review")), agent=a.get("agent"), actor=who),
    "decline_message": lambda c, a, who: core.decline_message(c, int(a["msg"]), a.get("note"), who),
    "update": lambda c, a, who: _no_update_in_app() or update_apply(),
    "restart": lambda c, a, who: _no_update_in_app() or (_restart_soon(), {"restarting": True})[1],
}


def _message_read(conn, msg_id):
    """Marks one alert or note read, which closes its needs-you event. Questions stay open until answered."""
    core.message_show(conn, msg_id)  # refuses an unknown id
    with core.tx(conn):
        core._mark_read(conn, [msg_id])
    return core.message_show(conn, msg_id)


def needs_you_view(conn, human=None):
    """Open needs-you events with what a person needs to act: the item's context and notes, the message's id."""
    rows = core.needs_you(conn, human or None)
    for r in rows:
        if r["item_id"] is not None:
            it = conn.execute("SELECT context, notes, status FROM items WHERE id=?", (r["item_id"],)).fetchone()
            if it:
                r.update(item_context=it["context"], item_notes=it["notes"], item_status=it["status"])
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path == "/api/state":
            conn = core.connect()
            try:
                core.activity(conn, None)
                st = core.state(conn)
                st["goals"] = core.goal_list(conn, include_complete=True)
                if DEV["on"]:
                    st["dev_build"] = _build_id()
                st["desktop"] = DESKTOP
                return self._send(200, st)
            finally:
                conn.close()
        if path == "/api/log":
            from urllib.parse import parse_qs, urlsplit
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            conn = core.connect()
            try:
                since = q.get("since", "7d")
                return self._send(200, core.completed(conn, q.get("project") or None, None if since == "all" else since))
            except RiverError as e:
                return self._send(400, {"error": str(e)})
            finally:
                conn.close()
        if path == "/api/needs-you":
            from urllib.parse import parse_qs, urlsplit
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            conn = core.connect()
            try:
                return self._send(200, {"events": needs_you_view(conn, q.get("human"))})
            finally:
                conn.close()
        if path == "/api/prompt-all" or (path.startswith("/api/item/") and path.endswith("/prompt")):
            from urllib.parse import parse_qs, urlsplit
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            conn = core.connect()
            try:
                if path == "/api/prompt-all":
                    return self._send(200, {"prompt": core.prompt_for_all(conn, q.get("person") or None)})
                return self._send(200, {"prompt": core.prompt_for(conn, int(path.split("/")[3]), q.get("person") or None)})
            except (RiverError, ValueError) as e:
                return self._send(404, {"error": str(e)})
            finally:
                conn.close()
        if path == "/api/inbox":
            from urllib.parse import parse_qs, urlsplit
            q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            conn = core.connect()
            try:
                # The page only looks: messages turn read when the person acts on them (Mark read, answer, reply).
                return self._send(200, {"messages": core.inbox(conn, q.get("agent"), bool(q.get("all")), mark_read=False)})
            except RiverError as e:
                return self._send(400, {"error": str(e)})
            finally:
                conn.close()
        if path == "/api/update":
            if DESKTOP:  # the page hides the button when git is false
                return self._send(200, {"git": False, "desktop": True, "boot": BOOT})
            try:
                return self._send(200, {**update_status(fetch="fetch=0" not in self.path), "boot": BOOT,
                                        "stale": code_stale()})
            except RiverError as e:
                return self._send(409, {"error": str(e)})
        if path == "/api/setup":
            conn = core.connect()
            try:
                return self._send(200, setup_status(conn))
            finally:
                conn.close()
        if path.startswith("/api/thread/"):
            conn = core.connect()
            try:
                return self._send(200, core.thread(conn, int(path.rsplit("/", 1)[1])))
            except (RiverError, ValueError) as e:
                return self._send(404, {"error": str(e)})
            finally:
                conn.close()
        if path.startswith("/api/item/"):
            conn = core.connect()
            try:
                iid = int(path.rsplit("/", 1)[1])
                res = core.item_show(conn, iid)
                res["tree"] = core.blockers(conn, iid)
                res["messages"] = core.item_messages(conn, iid)
                return self._send(200, res)
            except (RiverError, ValueError) as e:
                return self._send(404, {"error": str(e)})
            finally:
                conn.close()
        if not path.startswith("/api/"):
            got = static_file(path)
            if got:
                return self._send(200, *got)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/action":
            return self._send(404, {"error": "not found"})
        # Same-origin check: the page is the only intended caller.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        if origin and origin not in (f"http://{host}",):
            return self._send(403, {"error": "cross-origin request refused"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            op = OPS.get(body.get("op"))
            if not op:
                return self._send(400, {"error": f"unknown op {body.get('op')!r}"})
            actor = body.get("actor") or None
            conn = core.connect()
            try:
                core.activity(conn, actor)
                result = op(conn, body.get("args", {}), actor)
                with core.tx(conn):
                    core.sync_needs_you(conn)
                return self._send(200, {"ok": True, "result": result})
            finally:
                conn.close()
        except RiverError as e:
            return self._send(409, {"error": str(e)})
        except (KeyError, ValueError, TypeError) as e:
            return self._send(400, {"error": f"bad request: {e}"})


def _restart_on_change(httpd):
    """Dev mode: when a Python file changes, stop serving and start the process again."""
    code = {f: f.stat().st_mtime_ns for f in PKG.glob("*.py")}
    while True:
        time.sleep(1)
        now = {f: f.stat().st_mtime_ns for f in PKG.glob("*.py")}
        if now != code:
            print("code changed; restarting", flush=True)
            # Replace the process in place. Python sockets are not inherited across exec,
            # so the port is free for the new process. Shutting down first would let the
            # main thread exit before this line runs.
            time.sleep(0.3)  # let an editor finish writing
            os.execv(sys.executable, [sys.executable] + sys.argv)


def serve(port: int, open_browser=False, dev=False):
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}/"
    DEV["on"] = dev
    print(f"Biggest River on {url} (database {core.db_path()}){' [dev: restarts on code change]' if dev else ''}",
          flush=True)
    if dev:
        threading.Thread(target=_restart_on_change, args=(httpd,), daemon=True).start()
    from . import notify
    notify.SERVE_PORT["port"] = port
    stop = threading.Event()
    threading.Thread(target=notify.loop, args=(stop,), daemon=True, name="river-notify").start()
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
