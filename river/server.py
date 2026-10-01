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


# Tests (and river launch tests) set this to a fake that receives what would open a terminal.
TERMINAL_RUNNER = None


def _can_open_terminal(runner, hint, launch_in=None):
    if launch_in == "tmux":  # no Terminal app needed: any system with tmux, over SSH too
        import shutil
        if TMUX_RUNNER is None and not shutil.which(TMUX_CMD[0]):
            raise RiverError("launch_in is tmux, and tmux is not installed: brew install tmux (Linux: apt install tmux), "
                             "or use a Terminal tab: river config set launch_in tab")
        return
    runner = runner or TERMINAL_RUNNER
    if core.PLATFORM not in ("darwin", "win32") and runner is None:
        raise RiverError(f"starting an agent from the page works on macOS and Windows only; start one yourself: {hint}")


def launch_agent(conn, project=None, runner=None, agent=None, actor=None, model=None, effort=None, launch_in=None,
                 options=None):
    """Open a Terminal window in the project folder of the most important ready agent item and run
    the command of the chosen launch_agents entry there (default: the Claude Code profile), so one click starts one
    agent session. macOS and Windows."""
    # With no project named, Start spreads sessions: first a project with ready work and no agent yet.
    # A session that waits for work in that project (river wait) gets the item: no new session needed.
    # Else the new session gets a name and the item is pushed to it, so the next Start sees the project covered.
    t = core.launch_target(conn, project, agent, None, model, effort, launch_in, spread=project is None, options=options)
    waiting = core.waiting_agent_for(conn, t["project"], t["item"]["id"])
    if waiting:
        core.push(conn, t["item"]["id"], waiting, "from the Start button: you were waiting for work", actor)
        return {**t, "pushed_to": waiting}
    _can_open_terminal(runner, "cd <project folder> && claude go", t["launch_in"])
    return _start_for_item(conn, t, runner, actor, "Start opened this session for it")


def dispatch_item(conn, item_id, runner=None, agent=None, actor=None, model=None, effort=None, launch_in=None,
                  options=None):
    """Start work on one ready item: a session that waits for work in its project gets it (push);
    else river names a new session, reserves the item for it (push), and opens the chosen agent in the
    project folder with RIVER_AGENT set to that name and RIVER_FOCUS=item:<id>, so its first river go takes it."""
    t = core.launch_target(conn, agent=agent, item=item_id, model=model, effort=effort, launch_in=launch_in,
                           options=options)
    waiting = core.waiting_agent_for(conn, t["project"], t["item"]["id"])
    if waiting:
        core.push(conn, t["item"]["id"], waiting, "from the page: Dispatch; you were waiting for work", actor)
        return {**t, "pushed_to": waiting}
    _can_open_terminal(runner, f"cd <project folder> && RIVER_FOCUS=item:{t['item']['id']} claude go", t["launch_in"])
    return _start_for_item(conn, t, runner, actor, "Dispatch started this session for it")


def _start_for_item(conn, t, runner, actor, why):
    """Name a new session, reserve the item for it (push), and open the agent with RIVER_AGENT and
    RIVER_FOCUS=item:<id>: its first river go claims that item, or says why not. A session that never runs
    a river command is a manager finding (not connected) and does not count as the project's agent."""
    import secrets
    name = f"{t['project']}-{secrets.token_hex(2)}"
    core.register(conn, name, note=f"{core.STARTED_NOTE} #{t['item']['id']}")
    core.push(conn, t["item"]["id"], name, f"from the page: {why}", actor)
    _open_terminal(t, {"RIVER_AGENT": name, "RIVER_FOCUS": f"item:{t['item']['id']}", **t["env"]}, runner)
    return {**t, "session_name": name}


def open_agent_on(conn, item_id, runner=None, agent=None, person=None, actor=None, model=None, effort=None,
                  launch_in=None, options=None):
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
        return dispatch_item(conn, it["id"], runner, agent, actor, model, effort, launch_in, options)
    else:
        focus = f"unblock:{it['id']}"
    p = core._project(conn, it["project"])
    if not p["path"]:
        raise RiverError(f"project {p['name']} has no folder, so river cannot start a session there: "
                         f"river project path {p['name']} <folder>")
    return {**_open_focused(conn, p, focus, runner, agent, model, effort, launch_in, options),
            "item": {"id": it["id"], "title": it["title"]}}


def open_needs_you(conn, runner=None, agent=None, person=None, model=None, effort=None, launch_in=None, options=None):
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
    return _open_focused(conn, p, "needs:" + (f"@{person}" if person else ""), runner, agent, model, effort, launch_in,
                         options)


def deploy_now(conn, target, review=False, runner=None, agent=None, actor=None, model=None, effort=None,
               launch_in=None, options=None):
    """Targets tab, Deploy now (or Review and deploy): when the deploy item (or its review) is ready and
    the target has no owner to alert, open an agent in a folder of the target's projects that takes it."""
    r = core.deploy_now(conn, target, review, actor)
    if not r["ready"] or r.get("alerted"):
        return r
    p = _target_folder(conn, target)
    return {**r, **_open_focused(conn, p, f"{'review' if review else 'deploy'}:{target}", runner, agent,
                                 model, effort, launch_in, options)}


def _target_folder(conn, target):
    """A folder to start a session for a target: the first of its projects that has one."""
    p = next((core._project(conn, x["name"]) for x in core.target_show(conn, target)["projects"]
              if core._project(conn, x["name"])["path"]), None)
    if p is None:
        raise RiverError(f"no project of target {target} has a folder, so river cannot start a session there: "
                         f"river project path <name> <folder>")
    return p


def _agent_for(conn, model):
    """The launch_agents entry that runs this model: the first of its family, else the first of no known family."""
    opts = core.launch_options(conn)
    fam = core._family(core.parse_ladder(core.setting(conn, "model_ladder")), model)[0] if model else None
    pick = (next((o for o in opts if fam and o["family"] == fam), None)
            or next((o for o in opts if o["family"] is None), None) or opts[0])
    return pick["label"], pick


def open_monitors(conn, runner=None, db=None):
    """Open a session for each monitor item nobody holds yet (a deploy just started): in a folder of the
    target's projects, with RIVER_FOCUS=monitor:<id>, the item's model and effort (a monitor defaults to
    sonnet, low). The command asks the running server for this after it claims a deploy item; db must name
    this server's queue, so a test queue never opens sessions from the real one."""
    if db is not None and str(Path(db).expanduser().resolve()) != str(core.db_path().expanduser().resolve()):
        raise RiverError("this river serve uses another queue")
    out = []
    ann = None
    for m in core.pending_monitors(conn):
        ann = ann or core.annotate(conn)
        it = ann[m["id"]]
        try:
            p = _target_folder(conn, m["target"])
            agent, opt = _agent_for(conn, it["model"])
            model = it["model"] if any(x["name"] == it["model"] for x in opt["models"]) else None
            effort = it["effort"] if it["effort"] in opt["efforts"] else None
            t = _open_focused(conn, p, f"monitor:{m['id']}", runner, agent, model, effort)
        except RiverError as e:
            out.append({"id": m["id"], "error": str(e)})
            continue
        with core.tx(conn):
            core._event(conn, m["id"], "river", f"monitor session opened ({t['agent']} in {p['name']})")
        out.append({"id": m["id"], "agent": t["agent"], "project": p["name"], "model": model})
    return out


def _open_focused(conn, p, focus, runner, agent, model=None, effort=None, launch_in=None, options=None):
    """Open the chosen agent in a project folder with RIVER_FOCUS set; its river go reads it."""
    launch_in = core._launch_in(conn, p["id"], launch_in)
    _can_open_terminal(runner, f"cd {p['path']}, set RIVER_FOCUS={focus}, then claude go", launch_in)
    name = core.focus_title(conn, focus)
    t = {"project": p["name"], "path": p["path"], "focus": focus, "session_title": name,
         **core._launch_agent_cmd(conn, p["id"], agent, model, effort, options, name),
         "launch_in": launch_in}
    _open_terminal(t, {"RIVER_FOCUS": focus, **t["env"]}, runner)
    return t


def manage_command(cmd):
    """A launch_agents command that starts a manager instead of a worker: 'river go' (a first prompt) or the
    word go (Claude Code's `claude go`) becomes manage."""
    import re
    if "river go" in cmd:
        return cmd.replace("river go", "river manage")
    out, n = re.subn(r"(?<=\s)go(?=\s|$)", "manage", cmd, count=1)
    if not n:
        raise RiverError(f"cannot make a manager from the command {cmd!r}: it has no 'go' or 'river go' to replace")
    return out


def start_manager(conn, runner=None, agent=None, actor=None, model=None, effort=None, launch_in=None, options=None):
    """Start manager (the page's Manager section): the chosen agent with manage in place of go, in the folder
    of the first project that has one. Refuses while a manager is active."""
    import secrets
    other = core.active_manager(conn)
    if other:
        raise RiverError(f"{other} is the active manager; open its chat instead")
    p = next((core._project(conn, x["name"]) for x in core.project_list(conn) if x.get("path")), None)
    if p is None:
        raise RiverError("no project has a folder, so river cannot start a session: river project path <name> <folder>")
    launch_in = core._launch_in(conn, p["id"], launch_in)
    _can_open_terminal(runner, f"cd {p['path']} && claude manage", launch_in)
    t = {"project": p["name"], "path": p["path"], "session_title": "river manager",
         **core._launch_agent_cmd(conn, p["id"], agent, model, effort, options, "river manager"),
         "launch_in": launch_in}
    t["command"] = manage_command(t["command"])
    name = f"manager-{secrets.token_hex(2)}"
    core.register(conn, name, note="started from the page as the manager")
    core._set_role_note(conn, name, "manager", None)  # the next Start manager sees it at once
    _open_terminal(t, {"RIVER_AGENT": name, **t["env"]}, runner)
    return {**t, "session_name": name}


def open_chat(conn, agent, runner=None):
    """Bring the person into an agent's chat: its web link when the session has one (Claude Code with
    --remote-control); else, on macOS, the Terminal tab that runs its process (by the tty of the PID river
    recorded); else a hint that says why and what to do."""
    import subprocess
    a = core.agent_status(conn, agent)
    if a.get("session_url"):
        return {"agent": agent, "url": a["session_url"]}
    why = ("it has no web link: Remote Control is off for Claude Code (river config set claude_remote_control on)"
           if core.setting(conn, "claude_remote_control") == "off"
           else "it has no web link (start Claude Code with --remote-control for one)")
    if a.get("pid") and a.get("host") == core.this_host() and core.pid_alive(a["pid"]):
        try:
            tty = subprocess.run(["ps", "-o", "tty=", "-p", str(a["pid"])], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            tty = ""
        if tty and tty not in ("??", "?") and (core.PLATFORM == "darwin" or runner or TERMINAL_RUNNER):
            dev = tty if tty.startswith("/dev/") else "/dev/" + tty
            pane = _tmux_pane_of(dev)
            if pane:
                return {"agent": agent, "tmux_pane": pane["pane"],
                        "hint": f"{agent} runs in tmux ({pane['name'] or pane['pane']}): `river view` in a terminal shows it."}
            script = "\n".join([
                'tell application "Terminal"',
                '  repeat with w in windows',
                '    repeat with t in tabs of w',
                f'      if tty of t is {_applescript_str(dev)} then',
                '        set selected of t to true', '        set index of w to 1', '        activate',
                '        return "ok"', '      end if', '    end repeat', '  end repeat', 'end tell', 'return "none"'])
            run = runner or TERMINAL_RUNNER or (lambda sc: subprocess.run(
                ["osascript", "-e", sc], capture_output=True, text=True, timeout=10).stdout)
            try:
                got = run(script)
            except (OSError, subprocess.SubprocessError) as e:
                got = str(e)
            if (got or "").strip() == "ok":
                return {"agent": agent, "focused": True, "tty": dev}
            why = f"no Terminal tab runs {dev} (it may run in another terminal app)"
        else:
            why = "its process has no terminal (a background session)"
    elif a.get("pid"):
        why = f"its process runs on {a.get('host')}, not here" if a.get("host") != core.this_host() else "its process has ended"
    return {"agent": agent, "hint": f"No chat to open for {agent}: {why}."
            + (f" Its Claude Code session: {a['session']}." if a.get("session") else "")}


def _open_terminal(t, env, runner=None):
    """Run the agent command (t["command"]) in the project folder (t["path"]) with env set: in a new
    Terminal tab or window on macOS (launch_in), in a new console window on Windows, or with launch_in
    tmux in a pane of the river tmux session on any system. runner (tests) gets the AppleScript on macOS,
    and {"args", "cwd", "env"} on Windows; TMUX_RUNNER gets the tmux commands."""
    import os
    import shlex
    import subprocess
    runner = runner or TERMINAL_RUNNER
    # The agent uses the same queue as this page: a page on a RIVER_DB queue starts agents on it too.
    if os.environ.get("RIVER_DB"):
        env = {"RIVER_DB": str(core.db_path()), **env}
    if t["launch_in"] == "tmux":
        t["tmux_pane"] = _tmux_open(t, env, f"cd {shlex.quote(t['path'])} && "
                                    + "".join(f"{k}={shlex.quote(v)} " for k, v in env.items()) + t["command"])
        return
    if core.PLATFORM == "win32":
        # cmd /k keeps the window open when the agent ends; the command line goes to cmd as written.
        title = f"title {t['session_title']} & " if t.get("session_title") else ""
        spec = {"args": f"cmd /k {title}{t['command']}", "cwd": t["path"], "env": dict(env)}
        try:
            (runner or (lambda s: subprocess.Popen(s["args"], cwd=s["cwd"], env={**os.environ, **s["env"]},
                                                   creationflags=subprocess.CREATE_NEW_CONSOLE)))(spec)
        except OSError as e:
            raise RiverError(f"could not open a console window in {t['path']}: {e}")
        return
    # The tab or window gets the session's name as its title until the agent CLI sets its own (Claude Code
    # shows the --name it got; other CLIs may keep this one).
    title = f"printf '\\033]0;%s\\007' {shlex.quote(t['session_title'])}; " if t.get("session_title") else ""
    shell = (title + f"cd {shlex.quote(t['path'])} && " + "".join(f"{k}={shlex.quote(v)} " for k, v in env.items())
             + t["command"])
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


# launch_in tmux: each agent river starts is a pane of one tmux session, so one terminal shows them all
# (river view), over SSH too, and nothing needs AppleScript. TMUX_CMD is the tmux program (the tests give
# it a server of their own); TMUX_RUNNER (tests) gets each argument list in its place and returns the
# output, or None for a command that fails.
TMUX_CMD = ["tmux"]
TMUX_SESSION = "river"
TMUX_RUNNER = None
TMUX_SHELLS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "csh", "tcsh", "nu"}
# One line per pane: the free text (the session's name) comes last.
_TMUX_PANE = "#{pane_id}|#{window_id}|#{@river_tile}|#{window_panes}|#{pane_current_command}|#{pane_tty}|#{@river_agent}|#{@river_name}"


def _tmux(*args, check=True):
    """Run one tmux command and return its output. When it fails: RiverError with tmux's own words, or
    None with check off (has-session: no such session; split-window: no space)."""
    import subprocess
    if TMUX_RUNNER is not None:
        out = TMUX_RUNNER(list(args))
        if out is None and check:
            raise RiverError(f"tmux {args[0]} failed")
        return out
    try:
        r = subprocess.run([*TMUX_CMD, *args], capture_output=True, text=True, timeout=15, env=_tmux_env())
    except (OSError, subprocess.SubprocessError) as e:
        raise RiverError(f"tmux {args[0]}: {e}")
    if r.returncode:
        why = (r.stderr or r.stdout).strip()
        # A sandboxed session (Claude Code's sandbox, Codex's) cannot reach the tmux socket at all.
        if "Operation not permitted" in why or "Permission denied" in why:
            raise RiverError(f"tmux {args[0]}: {why}. A sandbox around this session blocks the tmux socket: run "
                             f"this command outside the sandbox, or start the session from the river page")
        if check:
            raise RiverError(f"tmux {args[0]}: {why}")
        return None
    return r.stdout.rstrip("\n")


def _tmux_env():
    """The environment for a tmux command. The first command starts the tmux server, and every pane gets
    the server's environment: when an agent session runs river launch, its own name, focus, and session
    ids (RIVER_*, CLAUDE*, CODEX_*) must stay out, or each new agent would start as a copy of that session."""
    import os
    keep = ("CLAUDE_CONFIG_DIR", "CODEX_HOME")
    return {k: v for k, v in os.environ.items() if k in keep or not k.startswith(("RIVER_", "CLAUDE", "CODEX_"))}


def _tmux_panes(everywhere=False):
    """The panes of the river tmux session (everywhere: of every session); [] when there is none."""
    out = _tmux("list-panes", *(["-a"] if everywhere else ["-s", "-t", "=" + TMUX_SESSION]), "-F", _TMUX_PANE,
                check=False)
    rows = []
    for line in (out or "").splitlines():
        f = line.split("|", 7)
        if len(f) == 8:
            rows.append({"pane": f[0], "window": f[1], "tile": f[2] == "1", "window_panes": int(f[3] or 1),
                         "running": f[4].lstrip("-"), "ended": f[4].lstrip("-") in TMUX_SHELLS, "tty": f[5],
                         "agent": f[6] or None, "name": f[7]})
    return rows


def _tmux_pane_of(tty):
    """The tmux pane on this terminal device, or None (also when tmux is not installed or not running)."""
    import shutil
    if TMUX_RUNNER is None and not shutil.which(TMUX_CMD[0]):
        return None
    try:
        return next((p for p in _tmux_panes(everywhere=True) if p["tty"] == tty), None)
    except RiverError:
        return None


def _tmux_open(t, env, line):
    """Start an agent in a new pane of the river tmux session and return the pane id: a window of its own,
    or one more pane of the side-by-side window when river view made one. tmux starts the pane's own login
    shell and river types the command line into it, as Terminal's do script does: the shell's PATH finds
    the agent CLI, and the pane stays (with what the agent printed) after the agent ends. Nothing takes
    the keyboard: a person who answers a prompt in another pane keeps typing there."""
    name = t.get("session_title") or t["project"]
    new = ["-c", t["path"], "-P", "-F", "#{pane_id}"]
    pane = None
    if _tmux("has-session", "-t", "=" + TMUX_SESSION, check=False) is None:
        # With no terminal attached yet, a window gets this size; it follows the terminal that attaches.
        pane = _tmux("new-session", "-d", "-s", TMUX_SESSION, "-n", name, "-x", "200", "-y", "50", *new)
        _tmux("set-option", "-t", f"={TMUX_SESSION}:", "default-size", "200x50")
    else:
        tile = next((p["window"] for p in _tmux_panes() if p["tile"]), None)
        if tile:  # no space left there for one more pane: a window of its own
            pane = _tmux("split-window", "-d", "-t", tile, *new, check=False)
            if pane:
                _tmux("select-layout", "-t", tile, "tiled")
        pane = pane or _tmux("new-window", "-d", "-t", f"={TMUX_SESSION}:", "-n", name, *new)
    _tmux("set-option", "-p", "-t", pane, "@river_name", name)
    if env.get("RIVER_AGENT"):
        _tmux("set-option", "-p", "-t", pane, "@river_agent", env["RIVER_AGENT"])
    _tmux("send-keys", "-t", pane, "-l", line)
    _tmux("send-keys", "-t", pane, "Enter")
    return pane


def tmux_view(layout=None, tidy=False):
    """The agents river started in tmux (launch_in tmux), for river view. layout "tile" puts every agent
    pane side by side in one window, and new agents then join it; "windows" gives each agent a window of
    its own again; None changes nothing. tidy first closes the panes whose agent has ended (only a shell
    runs there). Panes that a person made are left alone. Returns the panes and the tmux command that
    shows the session: attach, or switch-client inside tmux."""
    import os
    target = "=" + TMUX_SESSION
    if _tmux("has-session", "-t", target, check=False) is None:
        raise RiverError(f"no agent runs in tmux (there is no tmux session {TMUX_SESSION!r}). Start agents there: "
                         f"river launch --tmux, or for every start: river config set launch_in tmux")
    mine = lambda: [p for p in _tmux_panes() if p["name"]]
    closed, left = [], []
    if tidy:
        for p in mine():
            if p["ended"]:
                _tmux("kill-pane", "-t", p["pane"])
                closed.append(p["name"])
        if closed and _tmux("has-session", "-t", target, check=False) is None:
            return {"session": TMUX_SESSION, "panes": [], "closed": closed, "left": [], "layout": layout, "show": None}
    panes = mine()
    tile = next((p["window"] for p in panes if p["tile"]), None)
    if layout == "tile" and panes:
        if tile is None:
            tile = panes[0]["window"]
            _tmux("set-option", "-w", "-t", tile, "@river_tile", "1")
            _tmux("rename-window", "-t", tile, "agents")
            # Each pane shows its session's name on its top border.
            _tmux("set-option", "-w", "-t", tile, "pane-border-status", "top")
            _tmux("set-option", "-w", "-t", tile, "pane-border-format", " #{@river_name} ")
        for p in panes:
            if p["window"] != tile:
                if _tmux("join-pane", "-d", "-s", p["pane"], "-t", tile, check=False) is None:
                    left.append(p["name"])  # no space for one more pane: it keeps its window
                _tmux("select-layout", "-t", tile, "tiled")
        _tmux("select-layout", "-t", tile, "tiled")
        _tmux("select-window", "-t", tile)
    elif layout == "windows" and tile:
        inside = [p for p in panes if p["window"] == tile]
        for p in inside[1:]:
            _tmux("break-pane", "-d", "-s", p["pane"], "-n", p["name"], "-t", f"{target}:")
        _tmux("set-option", "-w", "-u", "-t", tile, "@river_tile")
        _tmux("set-option", "-w", "-u", "-t", tile, "pane-border-status")
        _tmux("set-option", "-w", "-u", "-t", tile, "pane-border-format")
        if inside:
            _tmux("rename-window", "-t", tile, inside[0]["name"])
    # The keys a person needs, in the status line for a few seconds after the session shows.
    prefix = (_tmux("show-options", "-gv", "prefix", check=False) or "C-b").replace("C-", "Ctrl-")
    hint = f"{prefix} then: an arrow = the next pane, z = one pane large (and back), n = the next window, d = leave"
    show = ["switch-client", "-t", target] if os.environ.get("TMUX") else ["attach-session", "-t", target]
    return {"session": TMUX_SESSION, "panes": mine(), "closed": closed, "left": left, "layout": layout,
            "show": [*TMUX_CMD, *show, ";", "display-message", "-d", "6000", hint]}


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


# Agent CLIs the setup guide offers for the Start button. Claude Code and Codex are launch profiles: river
# builds their commands from their options (core.LAUNCH_PLATFORMS). The others get an explicit first prompt,
# so they work even where the agent does not read the project's instruction file.
KNOWN_AGENTS = [
    ("Claude Code", "claude", "@claude-code"),
    ("Codex", "codex", "@codex"),
    ("Grok", "grok", 'grok "run river go in this folder and follow the briefing"'),
    ("OpenCode", "opencode", 'opencode --prompt "run river go in this folder and follow the briefing"'),
    ("Gemini", "gemini", 'gemini -i "run river go in this folder and follow the briefing"'),
]


def _agent_cmd(cmd):
    """A KNOWN_AGENTS command with {river_dir} filled in for this computer."""
    import shlex
    d = core.river_dir()
    return cmd.replace("{river_dir}", f'"{d}"' if core.PLATFORM == "win32" else shlex.quote(d))


def _shown_cmd(conn, cmd):
    """What an entry runs, as the setup guide shows it: a profile's command with its current options."""
    prof = core.parse_profile(cmd)
    return core.build_command(prof[0], core.profile_options(conn, *prof)) if prof else _agent_cmd(cmd)


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
    # Agent programs as a new terminal finds them (the app itself has only Finder's short PATH).
    # Agents start in a new Terminal window, so that is where their programs must be found (on Windows: PATH).
    exes = sorted({exe for _, exe, _ in KNOWN_AGENTS} | {core.entry_exe(c) for _, c in agents})
    term = _login_shell_which(exes) if core.PLATFORM != "win32" else {e: shutil.which(e) for e in exes}
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
        # Each Start button agent, and whether its program is on this computer (the first word of its command).
        "start_agents": [{"label": label, "found": bool(term.get(core.entry_exe(cmd)))} for label, cmd in agents],
        "agent_clis": [{"label": label, "found": bool(term.get(exe)), "added": label in have,
                        "command": _shown_cmd(conn, cmd)} for label, exe, cmd in KNOWN_AGENTS],
        # The options of each launch profile the Start button uses, for toggles (Settings > Setup).
        "launch_profiles": core.launch_profiles(conn),
        "notify_channels": core._channels(core.setting(conn, "notify_channels")),
        "ntfy_ready": bool(core.setting(conn, "ntfy_topic")),
        "river_cmd": river_command_status(),
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


def folder_add(conn, path, name=None, description="", move=None, actor=None):
    """Add a project folder from the page: what river init does in that folder, without a terminal. The
    project is created (default name: the folder's) or linked; a project linked to another folder that
    still exists is refused (only the user moves it: river project path --move). Then the agent block
    goes in AGENTS.md, and CLAUDE.md imports it; move is the CLAUDE.md rules choice (None: ask)."""
    from .cli import link_folder, setup_instructions, instructions_layout, folder_project_name
    if not (path or "").strip():
        raise RiverError("choose a folder")
    folder = Path(path.strip()).expanduser()
    if not folder.is_absolute():
        raise RiverError(f"{path} is not a full path; start it with / or ~")
    if not folder.is_dir():
        raise RiverError(f"{folder} is not a folder on this computer")
    folder = folder.resolve()
    name = (name or "").strip() or None
    if name:
        row = conn.execute("SELECT path FROM projects WHERE name=?", (name,)).fetchone()
        if row and row["path"] and Path(row["path"]) != folder and Path(row["path"]).is_dir():
            raise RiverError(f"project {name} is linked to {row['path']}. Pick another name for this folder, "
                             f"or move the project in a terminal: river project path {name} {folder} --move")
    name, linked_here, lines = link_folder(conn, folder, name, description.strip(), actor)
    lines += setup_instructions(folder, move)
    return {"project": name or linked_here[0], "path": str(folder), "lines": lines,
            "layout": instructions_layout(folder), "default_name": folder_project_name(folder)}


# The river command for agents: a small launcher in ~/.local/bin that runs this river with this Python, so an
# agent started from the app (or any terminal) can run `river go` on a Mac that has only the app.
LAUNCHER_MARK = "# Biggest River launcher"


def _launcher_path():
    return Path("~/.local/bin/river").expanduser()


def _login_shell_which(names):
    """Where a new terminal finds each command, {name: path or None}. The app runs with the short PATH
    that Finder gives, so ask a login shell started as Terminal starts one: the system PATH, then the
    profile files (not this process's PATH)."""
    import re
    import subprocess
    names = [n for n in names if re.fullmatch(r"[A-Za-z0-9._-]+", n)]
    shell = os.environ.get("SHELL") or "/bin/zsh"
    env = {k: os.environ[k] for k in ("HOME", "USER", "LOGNAME", "SHELL", "LANG", "TMPDIR") if k in os.environ}
    env.update(PATH="/usr/bin:/bin:/usr/sbin:/sbin", TERM="dumb")
    script = "; ".join(f'printf "@@{n}=%s\\n" "$(command -v {n})"' for n in names)
    try:
        r = subprocess.run([shell, "-ilc", script], capture_output=True, text=True, timeout=8,
                           stdin=subprocess.DEVNULL, env=env)
    except (OSError, subprocess.SubprocessError):
        return {n: None for n in names}
    found = dict(x[2:].split("=", 1) for x in r.stdout.splitlines() if x.startswith("@@") and "=" in x)
    return {n: (found.get(n) or "").strip() if (found.get(n) or "").strip().startswith("/") else None for n in names}


def _login_shell_river():
    """What `river` is in a new terminal, or None."""
    return _login_shell_which(["river"])["river"]


def river_command_status():
    """ok: `river` works in a new terminal. ours: it is this launcher. launcher: the launcher's state
    (current / old / missing). shell_path: what a new terminal finds. where: where the launcher goes."""
    if core.PLATFORM == "win32":
        return {"ok": True, "unsupported": True}
    lp = _launcher_path()
    text = lp.read_text(errors="replace") if lp.is_file() else ""
    launcher = "missing" if not text else "current" if text == _launcher_text() else "old" if LAUNCHER_MARK in text else "other"
    found = _login_shell_river()
    # A river command that the setup guide did not write (a link to a clone, pip) is the person's own: fine.
    ours = bool(found) and Path(found).expanduser() == lp and launcher in ("current", "old")
    return {"ok": bool(found) and (not ours or launcher == "current"), "shell_path": found, "ours": ours,
            "launcher": launcher, "where": str(lp), "in_app_image": "/Volumes/" in str(Path(__file__).resolve())}


def _launcher_text():
    import shlex
    root = Path(__file__).resolve().parent.parent
    py, script = shlex.quote(sys.executable), shlex.quote(str(root / "bin" / "river"))
    lines = [f"#!/bin/sh", f"{LAUNCHER_MARK}: runs the river that the setup guide found ({root}).",
             "# The setup guide rewrites it (Install the river command); delete it to remove it."]
    if DESKTOP:
        # An app opened from Downloads and then moved to Applications: use the copy in Applications.
        app = "/Applications/Biggest River.app/Contents/Resources/river-app"
        lines += [f"if [ ! -f {script} ] && [ -x '{app}/python/bin/python3' ]; then",
                  f"  exec '{app}/python/bin/python3' '{app}/bin/river' \"$@\"", "fi"]
    lines += [f"if [ ! -x {py} ] || [ ! -f {script} ]; then",
              f"  echo \"river: {root} is gone (the app moved or was removed). Open Biggest River, then Settings,\" >&2",
              "  echo \"the setup guide, and Install the river command again.\" >&2", "  exit 1", "fi",
              f"exec {py} {script} \"$@\""]
    return "\n".join(lines) + "\n"


def install_river_command():
    """Write the launcher, and put ~/.local/bin on PATH in the login profile when a new terminal would not
    find it. A river command that is not ours (a clone or pip) is left alone. Returns what changed."""
    if core.PLATFORM == "win32":
        raise RiverError("the river command installer works on macOS for now; on Windows, add the river "
                         "folder's bin to PATH")
    st = river_command_status()
    if st["in_app_image"]:
        raise RiverError("the app runs from its download image: drag Biggest River to Applications, open it "
                         "from there, then install the river command")
    if st["shell_path"] and not st["ours"]:
        return {"changed": [], "note": f"river is already installed at {st['shell_path']}; left as it is"}
    lp, changed = _launcher_path(), []
    if lp.exists() and st["launcher"] == "other":
        raise RiverError(f"{lp} exists and is not river's launcher; move it away first")
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(_launcher_text())
    lp.chmod(0o755)
    changed.append(f"{lp}: runs river from {Path(__file__).resolve().parent.parent}")
    if not _login_shell_river():
        shell = Path(os.environ.get("SHELL") or "/bin/zsh").name
        prof = Path("~/.zprofile" if shell == "zsh" else "~/.bash_profile" if shell == "bash" else "~/.profile").expanduser()
        old = prof.read_text() if prof.exists() else ""
        line = 'export PATH="$HOME/.local/bin:$PATH"  # Biggest River: the river command'
        if line not in old:
            prof.write_text(old + ("\n" if old and not old.endswith("\n") else "") + line + "\n")
            changed.append(f"{prof}: adds ~/.local/bin to PATH for new terminals")
    return {"changed": changed, "status": river_command_status()}


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
        # The first agent is the Start button's default: when its program is not on this computer (the
        # default Claude Code on a Mac that has only Codex), the agent added now becomes the default.
        first = core.entry_exe(agents[0][1]) if agents else None
        missing = first and core.PLATFORM != "win32" and not _login_shell_which([first])[first]
        agents = [(label, cmd)] + agents if missing else agents + [(label, cmd)]
        core.config_set(conn, "launch_agents", "; ".join(f"{a}={c}" for a, c in agents), actor=actor)
    return [a for a, _ in agents]


def setup_ntfy(conn, actor=None):
    from . import notify
    return notify.setup_ntfy(conn, actor=actor)


# Operations the page may call. Each maps JSON args to one core function.
def _launch_args(a):
    """The launch dialog's choices: model, effort, tab, window, or tmux, and profile options (each optional)."""
    return {**{k: a.get(k) or None for k in ("model", "effort", "launch_in")},
            "options": {k: str(v) for k, v in (a.get("options") or {}).items()} or None}


def _page_reason(a, who):
    """The reason of a stop from the page: the person's words, else who stopped it."""
    return (a.get("reason") or "").strip() or f"stopped from the page by {who or 'a person'}"


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
                                                goals=a.get("goals"), models=a.get("models")),
    "item_edit": lambda c, a, who: core.item_edit(c, a["id"], a.get("title"), a.get("notes"), a.get("doer"),
                                                  a.get("project"), who, a.get("context"), a.get("touches"),
                                                  a.get("check"), a.get("due"), goals=a.get("goals"),
                                                  untag=a.get("untag"), models=a.get("models")),
    "goal_add": lambda c, a, who: core.goal_add(c, a["project"], a["name"], a.get("outcome", ""), a.get("done_when", ""),
                                                who, shared=bool(a.get("shared"))),
    "goal_edit": lambda c, a, who: core.goal_edit(c, a["name"], a.get("outcome"), a.get("done_when"), a.get("new_name"), who,
                                                  a.get("shared")),
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
    "setup_river_cmd": lambda c, a, who: install_river_command(),
    "folder_add": lambda c, a, who: folder_add(c, a.get("path"), a.get("name"), a.get("description") or "", a.get("move"), who),
    "setup_skills": lambda c, a, who: setup_skills(),
    "setup_agent_add": lambda c, a, who: setup_agent_add(c, a["label"], who),
    "setup_ntfy": lambda c, a, who: setup_ntfy(c, who),
    "launch_agent": lambda c, a, who: launch_agent(c, a.get("project"), agent=a.get("agent"), actor=who, **_launch_args(a)),
    "dispatch_item": lambda c, a, who: dispatch_item(c, int(a["id"]), agent=a.get("agent"), actor=who, **_launch_args(a)),
    "open_agent_on": lambda c, a, who: open_agent_on(c, int(a["id"]), agent=a.get("agent"), person=a.get("person"), actor=who,
                                                     **_launch_args(a)),
    "open_needs_you": lambda c, a, who: open_needs_you(c, agent=a.get("agent"), person=a.get("person"), **_launch_args(a)),
    "deploy_now": lambda c, a, who: deploy_now(c, a["target"], bool(a.get("review")), agent=a.get("agent"), actor=who,
                                               **_launch_args(a)),
    "open_monitors": lambda c, a, who: open_monitors(c, db=a.get("db")),
    "queue_add": lambda c, a, who: core.queue_add(c, a["agent"], a.get("id"), a.get("message"), bool(a.get("first")),
                                                  a.get("before"), who),
    "queue_remove": lambda c, a, who: core.queue_remove(c, a["agent"], str(a["ref"]), who),
    # A person stops from the page without a reason; the agent then reads who stopped it.
    "stop_agent": lambda c, a, who: core.stop_agent(c, a["agent"], _page_reason(a, who), who),
    "kill_agent": lambda c, a, who: core.kill_agent(c, a["agent"], _page_reason(a, who), who),
    "start_manager": lambda c, a, who: start_manager(c, agent=a.get("agent"), actor=who, **_launch_args(a)),
    "open_chat": lambda c, a, who: open_chat(c, a["agent"]),
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
        if path == "/mcp":
            return self._mcp("GET")
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

    def _mcp(self, method):
        """/mcp: river's MCP tools over Streamable HTTP, for this computer only (mcp.local_refusal)."""
        from . import mcp
        why = mcp.local_refusal(self.client_address[0], dict(self.headers.items()))
        if why:
            return self._send(403, {"error": why})
        sid = self.headers.get("Mcp-Session-Id")
        if method == "GET":  # no stream of server messages: river sends none
            self.send_response(405)
            self.send_header("Allow", "POST, DELETE")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if method == "DELETE":
            return self._send(200 if mcp.http_delete(sid) else 404, {})
        code, reply, headers = mcp.http_post(self.rfile.read(int(self.headers.get("Content-Length", 0))), sid)
        data = b"" if reply is None else json.dumps(reply).encode()
        self.send_response(code)
        for k, v in headers.items():
            self.send_header(k, v)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_DELETE(self):
        if self.path.split("?")[0] == "/mcp":
            return self._mcp("DELETE")
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] == "/mcp":
            return self._mcp("POST")
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
                if body.get("op") in ("claim", "next_claim") and core.pending_monitors(conn):
                    try:  # a person claimed a deploy item on the page: its monitor starts too
                        open_monitors(conn)
                    except RiverError as e:
                        print(f"monitor: {e}", flush=True)
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


class _Server(ThreadingHTTPServer):
    """HTTPServer.server_bind asks DNS for the host's full name (socket.getfqdn), which can hang for
    a long time on a Mac with a slow or missing network. The page needs no name: skip the lookup."""
    def server_bind(self):
        import socketserver
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]


def serve(port: int, open_browser=False, dev=False):
    httpd = _Server(("127.0.0.1", port), Handler)
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
