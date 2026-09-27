"""Biggest River core: storage, graph ordering, claims, registry, capacity.

Every public function takes an open connection from `connect()` and returns
plain dicts and lists, so the CLI and the web server share one code path.
"""

from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "river.db"

OPEN_STATES = ("open", "in_progress", "held")
CLOSED_STATES = ("done", "dropped")
DOERS = ("any", "ai", "human")
# blocks: wait for it. feeds: wait for it, then read its output. conflicts: no
# order, but never in progress at the same time (both edit the same files).
DEP_KINDS = ("blocks", "feeds", "conflicts")

# Messages. An alert asks for attention now, a question waits for an answer,
# a note is for information, a notice comes from river itself, and an offer
# (help on a blocked item) is accepted or declined.
MESSAGE_KINDS = ("alert", "question", "answer", "note", "notice", "offer")
SEND_KINDS = ("alert", "question", "note")
MESSAGE_STATES = ("open", "accepted", "declined", "answered", "read")

DEFAULT_SETTINGS = {
    "lease_ttl": "30m",
    "hold_ttl": "2h",
    "owner_ttl": "8h",
    "keep_prereq_limit": "3",
    "replan_threshold": "3",
    "default_prerequisite_mode": "release",
    "max_leases": "1",
    "away_after": "1h",
    "gone_after": "24h",
    "question_nudge_after": "30m",
    "serve_port": "8765",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
  id                INTEGER PRIMARY KEY,
  name              TEXT NOT NULL UNIQUE,
  description       TEXT NOT NULL DEFAULT '',
  owner             TEXT,
  owner_expires_at  TEXT,
  created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,
  rank        INTEGER NOT NULL,
  notes       TEXT NOT NULL DEFAULT '',
  path        TEXT,
  target      TEXT,
  archived    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
  id                INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES projects(id),
  title             TEXT NOT NULL,
  notes             TEXT NOT NULL DEFAULT '',
  priority          INTEGER NOT NULL DEFAULT 2 CHECK (priority BETWEEN 0 AND 4),
  rank              REAL NOT NULL,
  doer              TEXT NOT NULL DEFAULT 'any' CHECK (doer IN ('any','ai','human')),
  status            TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','in_progress','held','done','dropped')),
  blocked_reason    TEXT,
  assignee          TEXT,
  claimed_at        TEXT,
  lease_expires_at  TEXT,
  output            TEXT NOT NULL DEFAULT '',
  context           TEXT NOT NULL DEFAULT '',
  touches           TEXT NOT NULL DEFAULT '',
  "check"           TEXT NOT NULL DEFAULT '',
  created_at        TEXT NOT NULL,
  closed_at         TEXT
);

CREATE TABLE IF NOT EXISTS deps (
  item_id     INTEGER NOT NULL REFERENCES items(id),
  blocked_by  INTEGER NOT NULL REFERENCES items(id),
  kind        TEXT NOT NULL DEFAULT 'blocks',
  auto        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (item_id, blocked_by),
  CHECK (item_id <> blocked_by)
);

CREATE TABLE IF NOT EXISTS events (
  id       INTEGER PRIMARY KEY,
  item_id  INTEGER REFERENCES items(id),
  at       TEXT NOT NULL,
  actor    TEXT NOT NULL,
  change   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  name           TEXT PRIMARY KEY,
  kind           TEXT NOT NULL DEFAULT 'ai' CHECK (kind IN ('ai','human')),
  note           TEXT NOT NULL DEFAULT '',
  registered_at  TEXT NOT NULL,
  last_seen      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
  scope  TEXT NOT NULL,
  key    TEXT NOT NULL,
  value  TEXT NOT NULL,
  PRIMARY KEY (scope, key)
);

CREATE TABLE IF NOT EXISTS messages (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL
              CHECK (kind IN ('alert','question','answer','note','notice','offer')),
  from_agent  TEXT NOT NULL,
  to_agent    TEXT,
  item_id     INTEGER REFERENCES items(id),
  reply_to    INTEGER REFERENCES messages(id),
  thread_id   INTEGER NOT NULL,
  body        TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'open'
              CHECK (state IN ('open','accepted','declined','answered','read')),
  created_at  TEXT NOT NULL,
  read_at     TEXT,
  closed_at   TEXT
);

CREATE INDEX IF NOT EXISTS items_status ON items(status);
CREATE INDEX IF NOT EXISTS deps_blocked_by ON deps(blocked_by);
CREATE INDEX IF NOT EXISTS events_item ON events(item_id);
CREATE INDEX IF NOT EXISTS messages_to ON messages(to_agent, read_at);
CREATE INDEX IF NOT EXISTS messages_item ON messages(item_id);
CREATE INDEX IF NOT EXISTS messages_thread ON messages(thread_id);
"""


class RiverError(Exception):
    """A refusal. The message names the rule and, where possible, the next command."""


# ---------------------------------------------------------------- time

def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


_DURATION = re.compile(r"^\s*(\d+)\s*([smhd])\s*$")


def parse_duration(s: str) -> timedelta:
    m = _DURATION.match(s)
    if not m:
        raise RiverError(f"bad duration {s!r}: use a number and s, m, h, or d (for example 30m, 2h, 7d)")
    n, unit = int(m.group(1)), m.group(2)
    return {"s": timedelta(seconds=n), "m": timedelta(minutes=n),
            "h": timedelta(hours=n), "d": timedelta(days=n)}[unit]


# ---------------------------------------------------------------- connection

def db_path() -> Path:
    return Path(os.environ.get("RIVER_DB", DEFAULT_DB)).expanduser()


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _migrate(conn):
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(projects)")}
    if "path" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN path TEXT")
    if "target" not in cols:
        conn.execute("ALTER TABLE projects ADD COLUMN target TEXT")
    if "auto" not in {r["name"] for r in conn.execute("PRAGMA table_info(deps)")}:
        conn.execute("ALTER TABLE deps ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")
    icols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    for col in ("context", "touches", "check"):
        if col not in icols:
            conn.execute(f"ALTER TABLE items ADD COLUMN \"{col}\" TEXT NOT NULL DEFAULT ''")


class tx:
    """BEGIN IMMEDIATE ... COMMIT, so two writers never interleave a claim."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def __enter__(self):
        self.conn.execute("BEGIN IMMEDIATE")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        self.conn.execute("ROLLBACK" if exc_type else "COMMIT")
        return False


def _event(conn, item_id, actor, change):
    conn.execute("INSERT INTO events(item_id, at, actor, change) VALUES (?,?,?,?)",
                 (item_id, iso(now()), actor or "?", change))


# ---------------------------------------------------------------- settings

def setting(conn, key: str, item_id: int | None = None, agent: str | None = None,
            project_id: int | None = None) -> str:
    """Most specific value wins: item, agent, project, global, built-in."""
    if key not in DEFAULT_SETTINGS:
        raise RiverError(f"unknown setting {key!r}; known: {', '.join(sorted(DEFAULT_SETTINGS))}")
    if item_id is not None and project_id is None:
        r = conn.execute("SELECT project_id FROM items WHERE id=?", (item_id,)).fetchone()
        project_id = r["project_id"] if r else None
    scopes = []
    if item_id is not None:
        scopes.append(f"item:{item_id}")
    if agent:
        scopes.append(f"agent:{agent}")
    if project_id is not None:
        r = conn.execute("SELECT name FROM projects WHERE id=?", (project_id,)).fetchone()
        if r:
            scopes.append(f"project:{r['name']}")
    scopes.append("global")
    for sc in scopes:
        r = conn.execute("SELECT value FROM settings WHERE scope=? AND key=?", (sc, key)).fetchone()
        if r:
            return r["value"]
    return DEFAULT_SETTINGS[key]


def _scope(conn, project=None, item=None, agent=None) -> str:
    given = [x for x in (project, item, agent) if x is not None]
    if len(given) > 1:
        raise RiverError("give at most one of --project, --item, --agent")
    if project is not None:
        _project(conn, project)
        return f"project:{project}"
    if item is not None:
        _item(conn, item)
        return f"item:{item}"
    if agent is not None:
        return f"agent:{agent}"
    return "global"


def config_set(conn, key, value, project=None, item=None, agent=None, actor=None):
    if key not in DEFAULT_SETTINGS:
        raise RiverError(f"unknown setting {key!r}; known: {', '.join(sorted(DEFAULT_SETTINGS))}")
    if key.endswith(("_ttl", "_after")):
        parse_duration(value)
    elif key in ("keep_prereq_limit", "replan_threshold", "max_leases", "serve_port"):
        if not value.isdigit():
            raise RiverError(f"{key} takes a whole number")
    elif key == "default_prerequisite_mode" and value not in ("keep", "release"):
        raise RiverError("default_prerequisite_mode is keep or release")
    sc = _scope(conn, project, item, agent)
    with tx(conn):
        conn.execute("INSERT INTO settings(scope,key,value) VALUES (?,?,?) "
                     "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value", (sc, key, value))
        _event(conn, None, actor, f"setting {sc} {key}={value}")
    return {"scope": sc, "key": key, "value": value}


def config_unset(conn, key, project=None, item=None, agent=None, actor=None):
    sc = _scope(conn, project, item, agent)
    with tx(conn):
        conn.execute("DELETE FROM settings WHERE scope=? AND key=?", (sc, key))
        _event(conn, None, actor, f"setting {sc} {key} unset")
    return {"scope": sc, "key": key}


def config_list(conn):
    rows = [dict(r) for r in conn.execute("SELECT scope,key,value FROM settings ORDER BY scope,key")]
    return {"defaults": DEFAULT_SETTINGS, "overrides": rows}


# ---------------------------------------------------------------- projects

def _project(conn, name):
    r = conn.execute("SELECT * FROM projects WHERE name=?", (name,)).fetchone()
    if not r:
        names = [x["name"] for x in conn.execute("SELECT name FROM projects WHERE archived=0 ORDER BY rank")]
        raise RiverError(f"no project {name!r}; projects: {', '.join(names) or '(none; river project add <name>)'}")
    return r


def project_add(conn, name, rank=None, notes="", actor=None, path=None, target=None):
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", name):
        raise RiverError("project names use lower-case letters, digits, '.', '_', '-'")
    with tx(conn):
        if conn.execute("SELECT 1 FROM projects WHERE name=?", (name,)).fetchone():
            raise RiverError(f"project {name!r} exists")
        top = conn.execute("SELECT COALESCE(MAX(rank),0) m FROM projects").fetchone()["m"]
        conn.execute("INSERT INTO projects(name,rank,notes,created_at) VALUES (?,?,?,?)",
                     (name, top + 1, notes, iso(now())))
        _event(conn, None, actor, f"project {name} added")
    if rank is not None:
        project_rank(conn, name, rank, actor)
    if path:
        project_path(conn, name, path, actor)
    if target:
        project_target(conn, name, target, actor)
    return dict(_project(conn, name))


def project_rank(conn, name, rank, actor=None):
    """Move a project to position `rank` (1 = most important) and renumber the rest."""
    with tx(conn):
        _project(conn, name)
        names = [r["name"] for r in conn.execute(
            "SELECT name FROM projects WHERE archived=0 ORDER BY rank, id") if r["name"] != name]
        rank = max(1, min(int(rank), len(names) + 1))
        names.insert(rank - 1, name)
        for i, n in enumerate(names, 1):
            conn.execute("UPDATE projects SET rank=? WHERE name=?", (i, n))
        _event(conn, None, actor, f"project {name} rank {rank}")
    return project_list(conn)


def project_archive(conn, name, actor=None):
    with tx(conn):
        _project(conn, name)
        conn.execute("UPDATE projects SET archived=1, rank=100000 WHERE name=?", (name,))
        _event(conn, None, actor, f"project {name} archived")
    return project_list(conn)


def project_path(conn, name, path, actor=None):
    """Link a project to a folder, so `river go` run inside that folder finds it."""
    full = str(Path(path).expanduser().resolve()) if path else None
    with tx(conn):
        _project(conn, name)
        conn.execute("UPDATE projects SET path=? WHERE name=?", (full, name))
        _event(conn, None, actor, f"project {name} path {full or 'cleared'}")
    return dict(_project(conn, name))


def project_target(conn, name, target, actor=None):
    """Put a project in the deploy target it ships to; no target clears it."""
    with tx(conn):
        _project(conn, name)
        if target is not None:
            _target(conn, target)
        conn.execute("UPDATE projects SET target=? WHERE name=?", (target, name))
        _event(conn, None, actor, f"project {name} target {target or 'cleared'}")
    return dict(_project(conn, name))


def projects_for_dir(conn, cwd):
    """Projects whose folder contains cwd; the deepest folder wins, ties keep every match."""
    here = Path(cwd).expanduser().resolve()
    best, depth = [], -1
    for r in conn.execute("SELECT name, path FROM projects WHERE archived=0 AND path IS NOT NULL ORDER BY rank"):
        root = Path(r["path"])
        if here == root or root in here.parents:
            d = len(root.parts)
            if d > depth:
                best, depth = [r["name"]], d
            elif d == depth:
                best.append(r["name"])
    return best


def project_describe(conn, name, text, actor=None):
    """The project description tells an agent what the project covers and what context helps."""
    with tx(conn):
        _project(conn, name)
        conn.execute("UPDATE projects SET notes=? WHERE name=?", (text, name))
        _event(conn, None, actor, f"project {name} description changed")
    return project_show(conn, name)


def project_show(conn, name):
    p = dict(_project(conn, name))
    ann = annotate(conn)
    mine = [a for a in ann.values() if a["project"] == name]
    ready = sorted((a for a in mine if a["ready"]), key=lambda a: a["sort_key"])
    holders = sorted({a["assignee"] for a in mine if a["status"] in ("in_progress", "held") and a["assignee"]})
    recent = sorted({r["actor"] for r in conn.execute(
        "SELECT DISTINCT e.actor FROM events e JOIN items i ON i.id=e.item_id "
        "WHERE i.project_id=? AND (e.change LIKE 'claimed%' OR e.change LIKE 'done%') "
        "ORDER BY e.id DESC LIMIT 20", (p["id"],))})
    p.update(
        description=p.pop("notes"),
        counts={s: sum(1 for a in mine if a["status"] == s) for s in ("open", "in_progress", "held", "done", "dropped")},
        ready=[{k: v for k, v in a.items() if k != "sort_key"} for a in ready[:5]],
        ready_count=len(ready),
        working_now=holders,
        worked_recently=recent,
    )
    return p


def project_list(conn):
    return [dict(r) for r in conn.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM items i WHERE i.project_id=p.id AND i.status IN ('open','in_progress','held')) open_items "
        "FROM projects p WHERE archived=0 ORDER BY rank, id")]


# ---------------------------------------------------------------- deploy targets

def _target(conn, name):
    r = conn.execute("SELECT * FROM targets WHERE name=?", (name,)).fetchone()
    if not r:
        names = [x["name"] for x in conn.execute("SELECT name FROM targets ORDER BY name")]
        raise RiverError(f"no target {name!r}; targets: {', '.join(names) or '(none; river target add <name> --description ...)'}")
    return r


def target_add(conn, name, description="", actor=None):
    """A deploy target is where projects ship to (a server, an app store, a package index)."""
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", name):
        raise RiverError("target names use lower-case letters, digits, '.', '_', '-'")
    with tx(conn):
        if conn.execute("SELECT 1 FROM targets WHERE name=?", (name,)).fetchone():
            raise RiverError(f"target {name!r} exists")
        conn.execute("INSERT INTO targets(name,description,created_at) VALUES (?,?,?)", (name, description, iso(now())))
        _event(conn, None, actor, f"target {name} added")
    return target_show(conn, name)


def target_describe(conn, name, text, actor=None):
    with tx(conn):
        _target(conn, name)
        conn.execute("UPDATE targets SET description=? WHERE name=?", (text, name))
        _event(conn, None, actor, f"target {name} description changed")
    return target_show(conn, name)


def target_own(conn, name, actor=None):
    """Become the one owner of a target. Refused while another agent owns it."""
    if not actor:
        raise RiverError("owning a target needs an agent name: set RIVER_AGENT or pass --as <name>")
    with tx(conn):
        _sweep(conn)
        _agent(conn, actor)
        t = _target(conn, name)
        if t["owner"] and t["owner"] != actor:
            left = parse_iso(t["owner_expires_at"]) - now()
            raise RiverError(f"refused: target {name} is owned by {t['owner']} "
                             f"(until {t['owner_expires_at']}, {_short(left)} left unless they renew). "
                             f"Ask them: river send question --to {t['owner']} \"...\", "
                             f"or they can hand it over: river target give {name} --to {actor}")
        ttl = parse_duration(setting(conn, "owner_ttl", agent=actor))
        cur = conn.execute("UPDATE targets SET owner=?, owner_expires_at=? WHERE name=? AND (owner IS NULL OR owner=?)",
                           (actor, iso(now() + ttl), name, actor))
        if cur.rowcount != 1:
            raise RiverError(f"target {name} changed owner while you asked; see: river target show {name}")
        if t["owner"] != actor:
            _event(conn, None, actor, f"target {name} owned by {actor}")
    return target_show(conn, name)


def target_release(conn, name, actor=None):
    with tx(conn):
        t = _target(conn, name)
        if t["owner"] != actor:
            raise RiverError(f"target {name} is owned by {t['owner'] or 'nobody'}, not {actor}")
        conn.execute("UPDATE targets SET owner=NULL, owner_expires_at=NULL WHERE name=?", (name,))
        _event(conn, None, actor, f"target {name} released")
    return target_show(conn, name)


def target_give(conn, name, to, actor=None):
    """Hand a target to another agent. Only the owner can give it."""
    with tx(conn):
        _sweep(conn)
        t = _target(conn, name)
        if t["owner"] != actor:
            raise RiverError(f"only the owner can give target {name}; it is owned by {t['owner'] or 'nobody'}"
                             + ("" if t["owner"] else f" (take it: river target own {name})"))
        _agent(conn, to)
        ttl = parse_duration(setting(conn, "owner_ttl", agent=to))
        conn.execute("UPDATE targets SET owner=?, owner_expires_at=? WHERE name=?", (to, iso(now() + ttl), name))
        _event(conn, None, actor, f"target {name} given to {to}")
        _send(conn, "notice", actor, f"{actor} gave you target {name}: you now run its deploys. "
              f"See: river target show {name}", to=to)
    return target_show(conn, name)


def _short(td):
    m = max(0, int(td.total_seconds() // 60))
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def target_list(conn):
    return [dict(r) for r in conn.execute(
        "SELECT t.*, (SELECT COUNT(*) FROM projects p WHERE p.target=t.name AND p.archived=0) projects "
        "FROM targets t ORDER BY t.name")]


def target_show(conn, name):
    t = dict(_target(conn, name))
    t["projects"] = [dict(r) for r in conn.execute(
        "SELECT p.name, p.rank, p.notes, (SELECT COUNT(*) FROM items i WHERE i.project_id=p.id "
        "AND i.status IN ('open','in_progress','held')) open_items "
        "FROM projects p WHERE p.target=? AND p.archived=0 ORDER BY p.rank, p.id", (name,))]
    return t


# ---------------------------------------------------------------- items

def _item(conn, item_id):
    r = conn.execute("SELECT * FROM items WHERE id=?", (int(item_id),)).fetchone()
    if not r:
        raise RiverError(f"no item {item_id}")
    return r


def _touches(value):
    """Files an item changes, as one path per line; accepts a list or a comma or newline separated string."""
    if value is None:
        return None
    parts = value if isinstance(value, (list, tuple)) else re.split(r"[,\n]", value)
    seen = []
    for x in (str(v).strip() for v in parts):
        if x and x not in seen:
            seen.append(x)
    return "\n".join(seen)


def touches_list(text):
    return [x for x in (text or "").split("\n") if x]


def item_add(conn, project, title, priority=2, notes="", doer="any", after=(), actor=None,
             context="", touches=None, check=""):
    if doer not in DOERS:
        raise RiverError(f"doer is one of {', '.join(DOERS)}")
    if not (0 <= int(priority) <= 4):
        raise RiverError("priority is 0 (highest) to 4 (lowest)")
    with tx(conn):
        p = _project(conn, project)
        top = conn.execute("SELECT COALESCE(MAX(rank),0) m FROM items WHERE project_id=?", (p["id"],)).fetchone()["m"]
        cur = conn.execute(
            'INSERT INTO items(project_id,title,notes,priority,rank,doer,context,touches,"check",created_at) '
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (p["id"], title, notes, int(priority), top + 1, doer, context or "", _touches(touches) or "",
             check or "", iso(now())))
        iid = cur.lastrowid
        _event(conn, iid, actor, f"added to {project} at P{priority}")
        for b in after:
            _dep_add(conn, iid, int(b), actor)
        _sync_conflicts(conn, iid, actor)
    return item_show(conn, iid)


def item_edit(conn, item_id, title=None, notes=None, doer=None, project=None, actor=None,
              context=None, touches=None, check=None):
    with tx(conn):
        it = _item(conn, item_id)
        if title is not None:
            conn.execute("UPDATE items SET title=? WHERE id=?", (title, it["id"]))
            _event(conn, it["id"], actor, "title changed")
        if notes is not None:
            conn.execute("UPDATE items SET notes=? WHERE id=?", (notes, it["id"]))
            _event(conn, it["id"], actor, "notes changed")
        if doer is not None:
            if doer not in DOERS:
                raise RiverError(f"doer is one of {', '.join(DOERS)}")
            conn.execute("UPDATE items SET doer=? WHERE id=?", (doer, it["id"]))
            _event(conn, it["id"], actor, f"doer {it['doer']} -> {doer}")
        if project is not None:
            p = _project(conn, project)
            conn.execute("UPDATE items SET project_id=? WHERE id=?", (p["id"], it["id"]))
            _event(conn, it["id"], actor, f"moved to project {project}")
        for col, val in (("context", context), ("touches", _touches(touches)), ("check", check)):
            if val is not None and val != it[col]:
                conn.execute(f'UPDATE items SET "{col}"=? WHERE id=?', (val, it["id"]))
                _event(conn, it["id"], actor, f"{col} changed")
                if col == "touches":
                    _sync_conflicts(conn, it["id"], actor)
    return item_show(conn, item_id)


def item_prio(conn, item_id, priority, actor=None):
    if not (0 <= int(priority) <= 4):
        raise RiverError("priority is 0 (highest) to 4 (lowest)")
    with tx(conn):
        it = _item(conn, item_id)
        conn.execute("UPDATE items SET priority=? WHERE id=?", (int(priority), it["id"]))
        _event(conn, it["id"], actor, f"priority P{it['priority']} -> P{priority}")
    return item_show(conn, item_id)


def item_move(conn, item_id, before=None, after=None, actor=None):
    """Change the manual order inside a project: place the item just before or after another."""
    if (before is None) == (after is None):
        raise RiverError("give exactly one of --before or --after")
    with tx(conn):
        it = _item(conn, item_id)
        ref = _item(conn, before if before is not None else after)
        if ref["project_id"] != it["project_id"]:
            raise RiverError("manual order applies inside one project; the two items are in different projects")
        ranks = [r["rank"] for r in conn.execute(
            "SELECT rank FROM items WHERE project_id=? AND id<>? ORDER BY rank", (it["project_id"], it["id"]))]
        i = ranks.index(ref["rank"])
        if before is not None:
            lo = ranks[i - 1] if i > 0 else ref["rank"] - 1
            new = (lo + ref["rank"]) / 2
        else:
            hi = ranks[i + 1] if i + 1 < len(ranks) else ref["rank"] + 1
            new = (ref["rank"] + hi) / 2
        conn.execute("UPDATE items SET rank=? WHERE id=?", (new, it["id"]))
        _event(conn, it["id"], actor, f"moved {'before' if before is not None else 'after'} {ref['id']}")
    return item_show(conn, item_id)


def _reachable(conn, start, edges_sql):
    seen, stack = set(), [start]
    while stack:
        x = stack.pop()
        for r in conn.execute(edges_sql, (x,)):
            y = r[0]
            if y not in seen:
                seen.add(y)
                stack.append(y)
    return seen


def _link(conn, a, b):
    """Any dependency row between two items, in either direction."""
    return conn.execute("SELECT * FROM deps WHERE (item_id=? AND blocked_by=?) OR (item_id=? AND blocked_by=?)",
                        (a, b, b, a)).fetchone()


def _dep_add(conn, item_id, blocked_by, actor, kind="blocks", auto=False):
    if kind not in DEP_KINDS:
        raise RiverError(f"dependency kind is one of {', '.join(DEP_KINDS)}")
    _item(conn, item_id)
    _item(conn, blocked_by)
    if item_id == blocked_by:
        raise RiverError("an item cannot wait on itself")
    if kind == "conflicts":
        # Symmetric and without order. An order between the two already keeps them apart.
        lo, hi = sorted((item_id, blocked_by))
        link = _link(conn, lo, hi)
        if link and link["kind"] != "conflicts":
            if auto:
                return
            raise RiverError(f"#{link['item_id']} already waits on #{link['blocked_by']} ({link['kind']}), "
                             f"so they never run at the same time; no conflict link needed")
        if link:
            return
        conn.execute("INSERT INTO deps(item_id,blocked_by,kind,auto) VALUES (?,?,?,?)", (lo, hi, kind, int(auto)))
        _event(conn, lo, actor, f"conflicts with {hi}" + (" (touches overlap)" if auto else ""))
        _event(conn, hi, actor, f"conflicts with {lo}" + (" (touches overlap)" if auto else ""))
        return
    # Adding item -> blocked_by makes a cycle when item is already a prerequisite of blocked_by.
    upstream_of_blocker = _reachable(conn, blocked_by, "SELECT blocked_by FROM deps WHERE item_id=? AND kind<>'conflicts'")
    if item_id in upstream_of_blocker:
        raise RiverError(f"refused: item {blocked_by} already waits on item {item_id} (directly or through other items); "
                         f"this dependency would make a loop")
    # An order replaces a conflict link between the same two items.
    conn.execute("DELETE FROM deps WHERE kind='conflicts' AND ((item_id=? AND blocked_by=?) OR (item_id=? AND blocked_by=?))",
                 (item_id, blocked_by, blocked_by, item_id))
    conn.execute("INSERT INTO deps(item_id,blocked_by,kind) VALUES (?,?,?) "
                 "ON CONFLICT(item_id,blocked_by) DO UPDATE SET kind=excluded.kind, auto=0", (item_id, blocked_by, kind))
    _event(conn, item_id, actor, f"waits on {blocked_by}" + (" (feeds)" if kind == "feeds" else ""))


def dep_add(conn, item_id, on, actor=None, kind="blocks"):
    with tx(conn):
        for b in on:
            _dep_add(conn, int(item_id), int(b), actor, kind)
    return item_show(conn, item_id)


def dep_remove(conn, item_id, on, actor=None):
    with tx(conn):
        for b in on:
            i, b = int(item_id), int(b)
            conn.execute("DELETE FROM deps WHERE (item_id=? AND blocked_by=?) "
                         "OR (kind='conflicts' AND item_id=? AND blocked_by=?)", (i, b, b, i))
            _event(conn, i, actor, f"no longer linked to {b}")
    return item_show(conn, item_id)


def _paths_overlap(a, b):
    """Same file, or one path is a directory that holds the other."""
    a, b = a.rstrip("/"), b.rstrip("/")
    return a == b or b.startswith(a + "/") or a.startswith(b + "/")


def _sync_conflicts(conn, item_id, actor):
    """Keep automatic conflict links equal to the open items whose touches overlap this item's."""
    it = _item(conn, item_id)
    mine = touches_list(it["touches"])
    want = set()
    if it["status"] in OPEN_STATES and mine:
        for r in conn.execute(f"SELECT id, touches FROM items WHERE id<>? AND status IN {OPEN_STATES} AND touches<>''",
                              (it["id"],)):
            if any(_paths_overlap(x, y) for x in mine for y in touches_list(r["touches"])):
                want.add(r["id"])
    have = {r["item_id"] if r["blocked_by"] == it["id"] else r["blocked_by"] for r in conn.execute(
        "SELECT item_id, blocked_by FROM deps WHERE kind='conflicts' AND auto=1 AND (item_id=? OR blocked_by=?)",
        (it["id"], it["id"]))}
    for other in have - want:
        lo, hi = sorted((it["id"], other))
        conn.execute("DELETE FROM deps WHERE item_id=? AND blocked_by=? AND kind='conflicts'", (lo, hi))
        _event(conn, it["id"], actor, f"no longer conflicts with {other} (touches no longer overlap)")
    for other in sorted(want - have):
        _dep_add(conn, it["id"], other, actor, "conflicts", auto=True)


def block(conn, item_id, reason, actor=None):
    """Record a blocker that is not an item in the queue ("waiting on Stripe review")."""
    with tx(conn):
        it = _item(conn, item_id)
        conn.execute("UPDATE items SET blocked_reason=? WHERE id=?", (reason, it["id"]))
        _event(conn, it["id"], actor, f"blocked: {reason}")
    return item_show(conn, item_id)


def unblock(conn, item_id, actor=None):
    with tx(conn):
        it = _item(conn, item_id)
        conn.execute("UPDATE items SET blocked_reason=NULL WHERE id=?", (it["id"],))
        _event(conn, it["id"], actor, "outside blocker cleared")
    return item_show(conn, item_id)


# ---------------------------------------------------------------- graph

def _load_graph(conn):
    projects = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM projects")}
    items = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM items")}
    waits_on = {i: [] for i in items}     # item -> prerequisites
    waited_by = {i: [] for i in items}    # item -> dependents
    conflicts = {i: [] for i in items}   # item -> items it must not run beside
    feeds = {i: [] for i in items}       # item -> prerequisites whose output it reads
    for r in conn.execute("SELECT item_id, blocked_by, kind FROM deps"):
        if r["kind"] == "conflicts":
            conflicts[r["item_id"]].append(r["blocked_by"])
            conflicts[r["blocked_by"]].append(r["item_id"])
            continue
        waits_on[r["item_id"]].append(r["blocked_by"])
        waited_by[r["blocked_by"]].append(r["item_id"])
        if r["kind"] == "feeds":
            feeds[r["item_id"]].append(r["blocked_by"])
    return projects, items, waits_on, waited_by, conflicts, feeds


def annotate(conn):
    """Compute readiness, effective priority, unblock counts, and sort keys for every item."""
    projects, items, waits_on, waited_by, conflicts, feeds = _load_graph(conn)
    is_open = {i: items[i]["status"] in OPEN_STATES for i in items}

    # Open dependents, transitive, of each open item.
    memo: dict[int, frozenset] = {}

    def dependents(i, trail=()):
        if i in memo:
            return memo[i]
        acc = set()
        for d in waited_by[i]:
            if is_open[d] and d not in trail:
                acc.add(d)
                acc |= dependents(d, trail + (i,))
        memo[i] = frozenset(acc)
        return memo[i]

    # Depth: 0 when no open prerequisite, else 1 + max depth of open prerequisites.
    depth_memo: dict[int, int] = {}

    def depth(i, trail=()):
        if i in depth_memo:
            return depth_memo[i]
        ds = [depth(b, trail + (i,)) + 1 for b in waits_on[i] if is_open[b] and b not in trail]
        depth_memo[i] = max(ds) if ds else 0
        return depth_memo[i]

    out = {}
    for i, it in items.items():
        p = projects[it["project_id"]]
        open_blockers = [b for b in waits_on[i] if is_open[b]]
        deps_i = dependents(i) if is_open[i] else frozenset()
        eff = it["priority"]
        source = None
        for d in deps_i:
            if items[d]["priority"] < eff:
                eff, source = items[d]["priority"], d
        busy = [c for c in conflicts[i] if items[c]["status"] in ("in_progress", "held")]
        ready = it["status"] == "open" and not open_blockers and not it["blocked_reason"] and not busy
        a = dict(it)
        a.update(
            touches=touches_list(it["touches"]),
            project=p["name"],
            project_rank=p["rank"],
            project_archived=bool(p["archived"]),
            waits_on=sorted(waits_on[i]),
            open_blockers=sorted(open_blockers),
            unblocks=sorted(waited_by[i]),
            fed_by=sorted(feeds[i]),
            conflicts=sorted(c for c in conflicts[i] if is_open[c]),
            busy_conflicts=sorted(busy),
            unblocks_count=len(deps_i),
            effective_priority=eff,
            priority_from=source,
            ready=ready,
            depth=depth(i) if is_open[i] else None,
        )
        a["reason"] = _reason(a)
        a["sort_key"] = (eff, p["rank"], -len(deps_i), it["rank"], it["created_at"], i)
        out[i] = a
    return out


def _reason(a):
    parts = [f"P{a['effective_priority']}"]
    if a["priority_from"] is not None:
        parts[0] += f" from #{a['priority_from']} (own P{a['priority']})"
    parts.append(f"project {a['project']} (rank {a['project_rank']})")
    if a["unblocks_count"]:
        parts.append(f"unblocks {a['unblocks_count']}")
    return ", ".join(parts)


def _prereq_closure(ann, root):
    seen, stack = set(), [root]
    while stack:
        x = stack.pop()
        for b in ann[x]["waits_on"]:
            if b not in seen and ann[b]["status"] in OPEN_STATES:
                seen.add(b)
                stack.append(b)
    return seen


def _graph_distances(ann, starts):
    """Distance from the start items over dependency links in both directions."""
    dist = {s: 0 for s in starts}
    frontier = list(starts)
    while frontier:
        nxt = []
        for x in frontier:
            for y in ann[x]["waits_on"] + ann[x]["unblocks"]:
                if y not in dist:
                    dist[y] = dist[x] + 1
                    nxt.append(y)
        frontier = nxt
    return dist


def history(conn, actor, limit=20):
    """Items the agent claimed or finished, most recent first."""
    seen, out = set(), []
    for r in conn.execute(
            "SELECT item_id FROM events WHERE actor=? AND item_id IS NOT NULL "
            "AND (change LIKE 'claimed%' OR change LIKE 'done%') ORDER BY id DESC", (actor,)):
        if r["item_id"] not in seen:
            seen.add(r["item_id"])
            out.append(r["item_id"])
            if len(out) >= limit:
                break
    return out


def ready_list(conn, project=None, unblocks=None, doer_for=None, ann=None, near=None, mine=None):
    """Ready items drawn from the area the agent chooses.

    The agent picks the area where it holds context: one or more projects
    (`project`, a name or comma list), the prerequisites of an item or project
    (`unblocks`), or the items linked to items it knows (`near`, closest
    first). Inside the area the graph order applies. With no area, the pool
    is every project.
    """
    ann = ann or annotate(conn)
    pool = [a for a in ann.values() if a["ready"] and not a["project_archived"]]
    if project:
        names = [n.strip() for n in str(project).split(",") if n.strip()]
        for n in names:
            _project(conn, n)
        pool = [a for a in pool if a["project"] in names]
    if unblocks is not None:
        if str(unblocks).isdigit():
            _item(conn, unblocks)
            roots = [int(unblocks)]
        else:
            _project(conn, unblocks)
            roots = [a["id"] for a in ann.values() if a["project"] == unblocks and a["status"] in OPEN_STATES]
        closure = set()
        for r in roots:
            closure |= _prereq_closure(ann, r)
        pool = [a for a in pool if a["id"] in closure]
    if doer_for is not None:
        pool = [a for a in pool if a["doer"] in ("any", doer_for)]
    if mine:
        hist = [i for i in history(conn, mine) if i in ann]
        if not hist:
            raise RiverError(f"{mine} has no claimed or finished items yet, so there is no history to work near. "
                             f"Pick an area instead: river project list, then river next --project <name>")
        dist = _graph_distances(ann, hist)
        hist_projects = {ann[i]["project"] for i in hist}
        far = 10 ** 6
        pool = [a for a in pool if a["id"] in dist or a["project"] in hist_projects]
        for a in pool:
            a["distance"] = dist.get(a["id"])
            if a["id"] not in dist:
                a["same_project"] = True
        pool.sort(key=lambda a: (dist.get(a["id"], far), a["sort_key"]))
        return pool
    if near:
        starts = [int(x) for x in (near if isinstance(near, (list, tuple)) else str(near).split(","))]
        for x in starts:
            _item(conn, x)
        dist = _graph_distances(ann, starts)
        pool = [a for a in pool if a["id"] in dist]
        for a in pool:
            a["distance"] = dist[a["id"]]
        pool.sort(key=lambda a: (dist[a["id"]], a["sort_key"]))
        return pool
    pool.sort(key=lambda a: a["sort_key"])
    return pool


# ---------------------------------------------------------------- registry and leases

def _touch_agent(conn, actor):
    """Renew every lease and target ownership the actor holds, and record that it was seen."""
    if not actor:
        return
    t = now()
    conn.execute("UPDATE agents SET last_seen=? WHERE name=?", (iso(t), actor))
    owner_ttl = parse_duration(setting(conn, "owner_ttl", agent=actor))
    conn.execute("UPDATE targets SET owner_expires_at=? WHERE owner=?", (iso(t + owner_ttl), actor))
    for r in conn.execute("SELECT id FROM items WHERE assignee=? AND status='in_progress'", (actor,)).fetchall():
        ttl = parse_duration(setting(conn, "lease_ttl", item_id=r["id"], agent=actor))
        conn.execute("UPDATE items SET lease_expires_at=? WHERE id=?", (iso(t + ttl), r["id"]))


def _sweep(conn):
    t = iso(now())
    expired = conn.execute(
        "SELECT id, assignee FROM items WHERE status='in_progress' AND lease_expires_at < ?", (t,)).fetchall()
    for r in expired:
        conn.execute("UPDATE items SET status='open', assignee=NULL, claimed_at=NULL, lease_expires_at=NULL WHERE id=?",
                     (r["id"],))
        _event(conn, r["id"], "river", f"lease expired (was {r['assignee']}); back to open")
        _send(conn, "notice", "river", f"your lease on #{r['id']} expired; the item is open again. "
              f"Claim it again if you still work on it: river claim {r['id']}", to=r["assignee"], item_id=r["id"])
    for r in conn.execute("SELECT name, owner FROM targets WHERE owner IS NOT NULL AND owner_expires_at < ?",
                          (t,)).fetchall():
        conn.execute("UPDATE targets SET owner=NULL, owner_expires_at=NULL WHERE name=?", (r["name"],))
        _event(conn, None, "river", f"target {r['name']} ownership expired (was {r['owner']})")
        _send(conn, "notice", "river", f"your ownership of target {r['name']} expired; nobody owns it now. "
              f"Take it again if you still deploy there: river target own {r['name']}", to=r["owner"])
    return [r["id"] for r in expired]


def activity(conn, actor):
    """Run at the start of every command: expire old leases, then renew the actor's own."""
    with tx(conn):
        expired = _sweep(conn)
        _touch_agent(conn, actor)
    return expired


def register(conn, name, human=False, note=""):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", name):
        raise RiverError("agent names use letters, digits, '.', '_', '-' (up to 64)")
    t = iso(now())
    with tx(conn):
        conn.execute(
            "INSERT INTO agents(name,kind,note,registered_at,last_seen) VALUES (?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, note=excluded.note, last_seen=excluded.last_seen",
            (name, "human" if human else "ai", note, t, t))
        _event(conn, None, name, f"registered as {'human' if human else 'ai'}")
    return agent_status(conn, name)


def unregister(conn, name, actor=None):
    with tx(conn):
        _agent(conn, name)
        held = conn.execute("SELECT id FROM items WHERE assignee=? AND status IN ('in_progress','held')", (name,)).fetchall()
        if held:
            raise RiverError(f"{name} holds {', '.join('#' + str(r['id']) for r in held)}; release those first")
        owned = [r["name"] for r in conn.execute("SELECT name FROM targets WHERE owner=?", (name,))]
        if owned:
            raise RiverError(f"{name} owns target {', '.join(owned)}; release or give it first "
                             f"(river target release <t>, river target give <t> --to <agent>)")
        conn.execute("DELETE FROM agents WHERE name=?", (name,))
        conn.execute("DELETE FROM settings WHERE scope=?", (f"agent:{name}",))
        _event(conn, None, actor or name, f"agent {name} unregistered")
    return {"unregistered": name}


def agent_note(conn, name, note):
    with tx(conn):
        _agent(conn, name)
        conn.execute("UPDATE agents SET note=? WHERE name=?", (note, name))
    return agent_status(conn, name)


def _agent(conn, name):
    r = conn.execute("SELECT * FROM agents WHERE name=?", (name,)).fetchone()
    if not r:
        raise RiverError(f"agent {name!r} is not registered; run: river register {name} [--human] [--note ...]")
    return r


def _agent_state(conn, a):
    age = now() - parse_iso(a["last_seen"])
    if age > parse_duration(setting(conn, "gone_after", agent=a["name"])):
        return "gone"
    if age > parse_duration(setting(conn, "away_after", agent=a["name"])):
        return "away"
    return "active"


def agent_status(conn, name):
    a = dict(_agent(conn, name))
    a["state"] = _agent_state(conn, a)
    a["holds"] = [dict(r) for r in conn.execute(
        "SELECT id, title, status, lease_expires_at FROM items WHERE assignee=? AND status IN ('in_progress','held') ORDER BY id",
        (name,))]
    a["owns"] = [dict(r) for r in conn.execute(
        "SELECT name, owner_expires_at FROM targets WHERE owner=? ORDER BY name", (name,))]
    return a


def who(conn, item=None, project=None):
    agents = [agent_status(conn, r["name"]) for r in conn.execute("SELECT name FROM agents ORDER BY name")]
    if item is not None:
        it = _item(conn, item)
        return [a for a in agents if a["name"] == it["assignee"]]
    if project is not None:
        p = _project(conn, project)
        ids = {r["id"] for r in conn.execute("SELECT id FROM items WHERE project_id=?", (p["id"],))}
        return [a for a in agents if any(h["id"] in ids for h in a["holds"])]
    return agents


def _claim_row(conn, item_id, actor):
    if not actor:
        raise RiverError("claiming needs an agent name: set RIVER_AGENT or pass --as <name>")
    ag = _agent(conn, actor)
    limit = int(setting(conn, "max_leases", agent=actor))
    held = conn.execute("SELECT COUNT(*) c FROM items WHERE assignee=? AND status IN ('in_progress','held')",
                        (actor,)).fetchone()["c"]
    if held >= limit:
        raise RiverError(f"refused: {actor} already holds {held} item(s) (max_leases {limit}). "
                         f"Finish one (river done <id>), release one (river release <id>), "
                         f"or raise the limit (river config set max_leases <n> --agent {actor})")
    ttl = parse_duration(setting(conn, "lease_ttl", item_id=item_id, agent=actor))
    t = now()
    cur = conn.execute(
        "UPDATE items SET status='in_progress', assignee=?, claimed_at=?, lease_expires_at=? "
        "WHERE id=? AND status='open'", (actor, iso(t), iso(t + ttl), item_id))
    if cur.rowcount != 1:
        raise RiverError(f"item {item_id} is no longer open")
    _event(conn, item_id, actor, f"claimed (lease {setting(conn, 'lease_ttl', item_id=item_id, agent=actor)})")
    return ag


def next_item(conn, project=None, unblocks=None, claim=False, actor=None, limit=1, near=None, mine=False):
    """Show, or claim, the first ready item from the area the agent chose."""
    doer_for = None
    if actor:
        r = conn.execute("SELECT kind FROM agents WHERE name=?", (actor,)).fetchone()
        doer_for = r["kind"] if r else None
    if mine and not actor:
        raise RiverError("--mine needs an agent name: set RIVER_AGENT or pass --as <name>")
    who_mine = actor if mine else None
    if not claim:
        return [{k: v for k, v in a.items() if k != 'sort_key'}
                for a in ready_list(conn, project, unblocks, doer_for, near=near, mine=who_mine)[:limit]]
    with tx(conn):
        _sweep(conn)
        pool = ready_list(conn, project, unblocks, doer_for, near=near, mine=who_mine)
        if not pool:
            return []
        _claim_row(conn, pool[0]["id"], actor)
        return [item_show(conn, pool[0]["id"])]


def claim(conn, item_id, actor=None):
    with tx(conn):
        _sweep(conn)
        a = annotate(conn)[_item(conn, item_id)["id"]]
        if not a["ready"]:
            why = (f"waits on {', '.join('#' + str(b) for b in a['open_blockers'])}" if a["open_blockers"]
                   else f"blocked: {a['blocked_reason']}" if a["blocked_reason"]
                   else f"conflicts with {', '.join('#' + str(b) for b in a['busy_conflicts'])}, which is in progress "
                        f"(both edit the same files)" if a["busy_conflicts"] and a["status"] == "open"
                   else f"status is {a['status']}" + (f" (held by {a['assignee']})" if a["assignee"] else ""))
            raise RiverError(f"item {item_id} is not ready: {why}. See: river blockers {item_id}")
        _claim_row(conn, a["id"], actor)
    return item_show(conn, item_id)


def _close(conn, item_id, status, actor, output=None):
    with tx(conn):
        it = _item(conn, item_id)
        if it["status"] in CLOSED_STATES:
            raise RiverError(f"item {item_id} is already {it['status']}")
        if it["assignee"] and actor and it["assignee"] != actor:
            raise RiverError(f"item {item_id} is held by {it['assignee']}; ask them, or release it first")
        conn.execute("UPDATE items SET status=?, closed_at=?, lease_expires_at=NULL, output=COALESCE(?, output) WHERE id=?",
                     (status, iso(now()), output, it["id"]))
        _event(conn, it["id"], actor, status + (f": {output}" if output else ""))
        newly = []
        if status in CLOSED_STATES:
            ann = annotate(conn)
            newly = [d for d in ann[it["id"]]["unblocks"] if ann[d]["ready"]]
    res = item_show(conn, item_id)
    res["now_ready"] = newly
    return res


def done(conn, item_id, output=None, actor=None):
    return _close(conn, item_id, "done", actor, output)


def drop(conn, item_id, actor=None):
    return _close(conn, item_id, "dropped", actor)


def release(conn, item_id, note=None, actor=None):
    with tx(conn):
        it = _item(conn, item_id)
        if it["status"] not in ("in_progress", "held"):
            raise RiverError(f"item {item_id} is {it['status']}; only a claimed item can be released")
        if actor and it["assignee"] != actor:
            raise RiverError(f"item {item_id} is held by {it['assignee']}, not {actor}")
        conn.execute("UPDATE items SET status='open', assignee=NULL, claimed_at=NULL, lease_expires_at=NULL WHERE id=?",
                     (it["id"],))
        _event(conn, it["id"], actor, "released" + (f": {note}" if note else ""))
    return item_show(conn, item_id)


def reopen(conn, item_id, actor=None):
    with tx(conn):
        it = _item(conn, item_id)
        if it["status"] not in CLOSED_STATES:
            raise RiverError(f"item {item_id} is {it['status']}, not closed")
        conn.execute("UPDATE items SET status='open', closed_at=NULL, assignee=NULL WHERE id=?", (it["id"],))
        _event(conn, it["id"], actor, "reopened")
    return item_show(conn, item_id)


# ---------------------------------------------------------------- reads

def item_show(conn, item_id, ann=None):
    ann = ann or annotate(conn)
    iid = int(item_id)
    if iid not in ann:
        raise RiverError(f"no item {item_id}")
    a = {k: v for k, v in ann[iid].items() if k != "sort_key"}
    a["waits_on_detail"] = [{"id": b, "title": ann[b]["title"], "status": ann[b]["status"]} for b in a["waits_on"]]
    a["unblocks_detail"] = [{"id": d, "title": ann[d]["title"], "status": ann[d]["status"]} for d in a["unblocks"]]
    a["fed_by_detail"] = [{"id": d, "title": ann[d]["title"], "status": ann[d]["status"], "output": ann[d]["output"]}
                          for d in a["fed_by"]]
    a["conflicts_detail"] = [{"id": d, "title": ann[d]["title"], "status": ann[d]["status"],
                              "assignee": ann[d]["assignee"]} for d in a["conflicts"]]
    a["events"] = [dict(r) for r in conn.execute(
        "SELECT at, actor, change FROM events WHERE item_id=? ORDER BY id DESC LIMIT 50", (iid,))]
    a["message_count"] = conn.execute("SELECT COUNT(*) FROM messages WHERE item_id=?", (iid,)).fetchone()[0]
    return a


def item_list(conn, project=None, status=None, include_closed=False):
    ann = annotate(conn)
    rows = [a for a in ann.values() if not a["project_archived"]]
    if project is not None:
        _project(conn, project)
        rows = [a for a in rows if a["project"] == project]
    if status is not None:
        rows = [a for a in rows if a["status"] == status]
    elif not include_closed:
        rows = [a for a in rows if a["status"] in OPEN_STATES]
    rows.sort(key=lambda a: (a["status"] in CLOSED_STATES, not a["ready"], a["sort_key"]))
    return [{k: v for k, v in a.items() if k != "sort_key"} for a in rows]


def blockers(conn, item_id):
    """Tree of open prerequisites with status and holder."""
    ann = annotate(conn)
    root = _item(conn, item_id)["id"]

    def node(i, trail):
        a = ann[i]
        left = None
        if a["lease_expires_at"]:
            left = int((parse_iso(a["lease_expires_at"]) - now()).total_seconds())
        return {
            "id": i, "title": a["title"], "status": a["status"], "assignee": a["assignee"],
            "ready": a["ready"], "blocked_reason": a["blocked_reason"], "lease_seconds_left": left,
            "busy_conflicts": [{"id": c, "title": ann[c]["title"], "assignee": ann[c]["assignee"]}
                               for c in a["busy_conflicts"]],
            "children": [node(b, trail | {i}) for b in a["waits_on"]
                         if ann[b]["status"] in OPEN_STATES and b not in trail],
        }

    return node(root, frozenset())


# ---------------------------------------------------------------- messages

def _message(conn, msg_id):
    r = conn.execute("SELECT * FROM messages WHERE id=?", (int(msg_id),)).fetchone()
    if not r:
        raise RiverError(f"no message {msg_id}")
    return r


def _send(conn, kind, sender, body, to=None, item_id=None, reply_to=None):
    """Insert one message inside the caller's transaction and return its id."""
    t = iso(now())
    cur = conn.execute(
        "INSERT INTO messages(kind,from_agent,to_agent,item_id,reply_to,thread_id,body,created_at) "
        "VALUES (?,?,?,?,?,0,?,?)", (kind, sender, to, item_id, reply_to, body, t))
    mid = cur.lastrowid
    thread = _message(conn, reply_to)["thread_id"] if reply_to else mid
    conn.execute("UPDATE messages SET thread_id=? WHERE id=?", (thread, mid))
    return mid


# A message reaches its to_agent. A message with no to_agent but an item
# reaches whoever holds that item when they read, so a question on an
# unclaimed item waits for the next holder.
_TO_ME = ("(m.to_agent=? OR (m.to_agent IS NULL AND m.item_id IN "
          "(SELECT id FROM items WHERE assignee=? AND status IN ('in_progress','held'))))")


def _msg_dict(r):
    m = dict(r)
    m["unread"] = m["read_at"] is None
    return m


def send(conn, kind, body, to=None, item=None, reply_to=None, actor=None):
    """Send an alert, question, or note to an agent, to the holder of an item, or as a reply."""
    if not actor:
        raise RiverError("sending needs an agent name: set RIVER_AGENT or pass --as <name>")
    if kind not in SEND_KINDS:
        raise RiverError(f"send kind is one of {', '.join(SEND_KINDS)}; to answer a question: river answer <message-id> \"...\"")
    if not body or not body.strip():
        raise RiverError("a message needs text")
    with tx(conn):
        if reply_to is not None:
            parent = _message(conn, reply_to)
            if to is None:
                to = parent["from_agent"] if parent["from_agent"] != actor else parent["to_agent"]
            if item is None:
                item = parent["item_id"]
        if item is not None:
            it = _item(conn, item)
            item = it["id"]
            if to is None and it["status"] in ("in_progress", "held"):
                to = it["assignee"]
        if to is None and item is None:
            raise RiverError("say who gets it: --to <agent>, --item <id> (its holder), or --reply <message-id>")
        if to is not None:
            _agent(conn, to)
        mid = _send(conn, kind, actor, body.strip(), to=to, item_id=item, reply_to=reply_to)
        if reply_to is not None:
            # Replying to a message means the sender read it.
            if conn.execute(f"SELECT 1 FROM messages m WHERE m.id=? AND {_TO_ME}", (reply_to, actor, actor)).fetchone():
                _mark_read(conn, [reply_to])
        if item is not None:
            _event(conn, item, actor, f"{kind} #{mid} to {to or 'the next holder'}")
    return message_show(conn, mid)


def answer(conn, msg_id, body, actor=None):
    """Answer a question: the answer goes to the asker and the question closes."""
    if not actor:
        raise RiverError("answering needs an agent name: set RIVER_AGENT or pass --as <name>")
    if not body or not body.strip():
        raise RiverError("an answer needs text")
    with tx(conn):
        q = _message(conn, msg_id)
        if q["kind"] != "question":
            raise RiverError(f"message {msg_id} is {'an' if q['kind'][0] in 'aeiou' else 'a'} {q['kind']}, not a question; "
                             f"reply with: river send note --reply {msg_id} \"...\"")
        if q["state"] != "open":
            raise RiverError(f"question {msg_id} is already {q['state']}; see: river thread {msg_id}")
        if q["from_agent"] == actor:
            raise RiverError(f"you asked question {msg_id}; add to it with: river send note --reply {msg_id} \"...\"")
        t = iso(now())
        mid = _send(conn, "answer", actor, body.strip(), to=q["from_agent"], item_id=q["item_id"], reply_to=q["id"])
        conn.execute("UPDATE messages SET state='answered', closed_at=?, read_at=COALESCE(read_at, ?), "
                     "to_agent=COALESCE(to_agent, ?) WHERE id=?", (t, t, actor, q["id"]))
        if q["item_id"] is not None:
            _event(conn, q["item_id"], actor, f"answered question #{q['id']}")
    return message_show(conn, mid)


def _mark_read(conn, ids):
    t = iso(now())
    for i in ids:
        # Questions and offers stay open until someone answers, accepts, or declines them.
        conn.execute("UPDATE messages SET read_at=COALESCE(read_at, ?), "
                     "state=CASE WHEN kind IN ('question','offer') THEN state ELSE 'read' END WHERE id=?", (t, i))


def inbox(conn, actor, include_read=False, mark_read=True):
    """Messages for the actor: unread ones, and questions still waiting for an answer."""
    if not actor:
        raise RiverError("the inbox needs an agent name: set RIVER_AGENT or pass --as <name>")
    _agent(conn, actor)
    where = f"{_TO_ME} AND m.from_agent<>?"
    if not include_read:
        where += " AND (m.read_at IS NULL OR (m.kind IN ('question','offer') AND m.state='open'))"
    with tx(conn):
        rows = [_msg_dict(r) for r in conn.execute(
            f"SELECT m.*, i.title item_title FROM messages m LEFT JOIN items i ON i.id=m.item_id "
            f"WHERE {where} ORDER BY m.id", (actor, actor, actor))]
        if mark_read:
            _mark_read(conn, [m["id"] for m in rows if m["unread"]])
    return rows


def unread(conn, actor):
    """Counts for the line every command prints: unread messages and open questions to the actor."""
    if not actor:
        return {"unread": 0, "alerts": 0, "questions": 0}
    r = conn.execute(
        f"SELECT SUM(m.read_at IS NULL) unread, SUM(m.read_at IS NULL AND m.kind='alert') alerts, "
        f"SUM(m.kind='question' AND m.state='open') questions FROM messages m WHERE {_TO_ME} AND m.from_agent<>?",
        (actor, actor, actor)).fetchone()
    return {"unread": r["unread"] or 0, "alerts": r["alerts"] or 0, "questions": r["questions"] or 0}


def message_show(conn, msg_id):
    r = conn.execute("SELECT m.*, i.title item_title FROM messages m LEFT JOIN items i ON i.id=m.item_id "
                     "WHERE m.id=?", (int(msg_id),)).fetchone()
    if not r:
        raise RiverError(f"no message {msg_id}")
    return _msg_dict(r)


def thread(conn, msg_id, actor=None):
    """A message with everything before and after it in the same conversation."""
    root = _message(conn, msg_id)["thread_id"]
    with tx(conn):
        rows = [_msg_dict(r) for r in conn.execute(
            "SELECT m.*, i.title item_title FROM messages m LEFT JOIN items i ON i.id=m.item_id "
            "WHERE m.thread_id=? ORDER BY m.id", (root,))]
        if actor:
            mine = {r["id"] for r in conn.execute(
                f"SELECT m.id FROM messages m WHERE m.thread_id=? AND m.read_at IS NULL AND m.from_agent<>? AND {_TO_ME}",
                (root, actor, actor, actor))}
            _mark_read(conn, mine)
    return {"thread_id": root, "messages": rows}


def item_messages(conn, item_id):
    _item(conn, item_id)
    return [_msg_dict(r) for r in conn.execute(
        "SELECT m.*, NULL item_title FROM messages m WHERE m.item_id=? ORDER BY m.id", (int(item_id),))]


# ---------------------------------------------------------------- capacity

def capacity(conn, ann=None):
    """How many agent sessions the graph can use now, and whether too many are running.

    Ready items never wait on each other (a ready item has no open prerequisite),
    so every ready item nobody holds is a slot for one session, except that of
    two ready items that conflict (same files) only one can run.
    """
    ann = ann or annotate(conn)
    live = [a for a in ann.values() if not a["project_archived"]]
    ready = [a for a in live if a["ready"]]
    ready_ai = [a for a in ready if a["doer"] in ("any", "ai")]
    runnable, taken = 0, set()
    for a in sorted(ready_ai, key=lambda a: a["sort_key"]):
        if not taken & set(a["conflicts"]):
            runnable += 1
            taken.add(a["id"])
    ready_human = [a for a in ready if a["doer"] == "human"]
    agents = [agent_status(conn, r["name"]) for r in conn.execute("SELECT name FROM agents")]
    ai_active = [a for a in agents if a["kind"] == "ai" and a["state"] == "active"]
    ai_busy = [a for a in ai_active if a["holds"]]
    ai_idle = [a for a in ai_active if not a["holds"]]
    in_progress = [a for a in live if a["status"] in ("in_progress", "held")]

    # Width by depth: how many open items could run at each step if every earlier step finished.
    layers: dict[int, dict] = {}
    for a in live:
        if a["status"] in OPEN_STATES and a["depth"] is not None:
            layer = layers.setdefault(a["depth"], {"depth": a["depth"], "ai": 0, "human": 0})
            layer["human" if a["doer"] == "human" else "ai"] += 1
    layer_list = [layers[k] for k in sorted(layers)]

    spare = runnable - len(ai_idle)
    advice = []
    if spare > 0:
        advice.append({"kind": "launch", "text": f"{spare} ready item(s) for agents can run and have nobody on them: "
                       f"you can start up to {spare} more agent session(s)."})
    if spare < 0:
        advice.append({"kind": "too_many", "text": f"{len(ai_idle)} active agent session(s) hold nothing, "
                       f"but only {runnable} ready item(s) can run for them now. "
                       f"{-spare} session(s) have no work; stop them or give them other work."})
    if ready_human:
        advice.append({"kind": "human", "text": f"{len(ready_human)} ready item(s) wait on a human."})
    if not ready and any(a["status"] == "open" for a in live) and not in_progress:
        advice.append({"kind": "stuck", "text": "Nothing is ready and nothing is in progress: "
                       "every open item is blocked. Check outside blockers (river list)."})
    return {
        "ready_for_agents": [a["id"] for a in sorted(ready_ai, key=lambda a: a["sort_key"])],
        "ready_for_humans": [a["id"] for a in sorted(ready_human, key=lambda a: a["sort_key"])],
        "in_progress": [a["id"] for a in in_progress],
        "agents_active": len(ai_active),
        "agents_busy": len(ai_busy),
        "agents_idle": [a["name"] for a in ai_idle],
        "spare_slots": max(spare, 0),
        "excess_sessions": max(-spare, 0),
        "layers": layer_list,
        "peak_width": max((l["ai"] for l in layer_list), default=0),
        "advice": advice,
    }


def completed(conn, project=None, since="7d"):
    """Done items with their output, who finished them, and when; grouped by day, with progress per project.

    `since` is a duration (7d, 12h) or None for all time. Progress counts every
    item in the project, so a short window still shows how far the project is.
    """
    cutoff = iso(now() - parse_duration(since)) if since else None
    if project is not None:
        _project(conn, project)
    sql = ("SELECT i.id, i.title, i.output, i.closed_at, p.name project, "
           "(SELECT e.actor FROM events e WHERE e.item_id=i.id AND e.change LIKE 'done%' ORDER BY e.id DESC LIMIT 1) by_agent "
           "FROM items i JOIN projects p ON p.id=i.project_id WHERE i.status='done' AND p.archived=0")
    args = []
    if cutoff:
        sql += " AND i.closed_at >= ?"
        args.append(cutoff)
    if project is not None:
        sql += " AND p.name = ?"
        args.append(project)
    items = [dict(r) for r in conn.execute(sql + " ORDER BY i.closed_at DESC, i.id DESC", args)]
    days = {}
    for it in items:
        days.setdefault(it["closed_at"][:10], []).append(it)
    progress = []
    for p in project_list(conn):
        if project is not None and p["name"] != project:
            continue
        counts = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM items WHERE project_id=? GROUP BY status", (p["id"],))}
        done_n = counts.get("done", 0)
        total = sum(n for st, n in counts.items() if st != "dropped")
        recent = sum(1 for it in items if it["project"] == p["name"])
        if total or recent:
            progress.append({"project": p["name"], "done": done_n, "total": total,
                             "open": total - done_n, "done_in_window": recent})
    return {"since": since, "cutoff": cutoff, "items": items,
            "by_day": [{"day": d, "items": days[d]} for d in sorted(days, reverse=True)],
            "progress": progress}


def status(conn, recent=10):
    """One overview: every project's counts, the latest completions, who is working, and open slots."""
    ann = annotate(conn)
    projects = []
    for p in project_list(conn):
        mine = [a for a in ann.values() if a["project"] == p["name"]]
        projects.append({
            "project": p["name"], "rank": p["rank"], "target": p.get("target"),
            "done": sum(a["status"] == "done" for a in mine),
            "open": sum(a["status"] in OPEN_STATES for a in mine),
            "ready": sum(a["ready"] for a in mine),
            "in_progress": sum(a["status"] in ("in_progress", "held") for a in mine),
            "human_waiting": sum(a["ready"] and a["doer"] == "human" for a in mine),
            "blocked": sum(a["status"] == "open" and not a["ready"] for a in mine),
        })
    agents = [agent_status(conn, r["name"]) for r in conn.execute("SELECT name FROM agents ORDER BY name")]
    cap = capacity(conn, ann)
    return {
        "now": iso(now()),
        "projects": projects,
        "recent": completed(conn, since=None)["items"][:recent],
        "agents": [{"name": a["name"], "kind": a["kind"], "state": a["state"], "note": a["note"],
                    "holds": a["holds"], "owns": [o["name"] for o in a["owns"]]}
                   for a in agents if a["state"] != "gone"],
        "human_waiting": [{"id": a["id"], "title": a["title"], "project": a["project"]}
                          for a in sorted(ann.values(), key=lambda a: a["sort_key"])
                          if a["ready"] and a["doer"] == "human" and not a["project_archived"]],
        "spare_slots": cap["spare_slots"],
        "excess_sessions": cap["excess_sessions"],
        "advice": cap["advice"],
    }


def recent_events(conn, limit=40):
    return [dict(r) for r in conn.execute(
        "SELECT e.at, e.actor, e.change, e.item_id, i.title FROM events e LEFT JOIN items i ON i.id=e.item_id "
        "ORDER BY e.id DESC LIMIT ?", (limit,))]


def state(conn):
    """Everything the web page shows, in one read."""
    ann = annotate(conn)
    items = sorted(ann.values(), key=lambda a: a["sort_key"])
    return {
        "now": iso(now()),
        "projects": project_list(conn),
        "items": [{k: v for k, v in a.items() if k != "sort_key"} for a in items if not a["project_archived"]],
        "agents": [agent_status(conn, r["name"]) for r in conn.execute("SELECT name FROM agents ORDER BY name")],
        "capacity": capacity(conn, ann),
        "settings": config_list(conn),
        "events": recent_events(conn),
    }


# ---------------------------------------------------------------- go

ROLES = ("worker", "unblocker", "planner", "idle")


def go(conn, cwd, actor=None, project=None, role=None):
    """One call for a fresh agent session: find the project, name the session, pick a role, and brief it."""
    if role is not None and role not in ROLES:
        raise RiverError(f"role is one of {', '.join(ROLES)}")
    if project:
        names = [n.strip() for n in project.split(",") if n.strip()]
        for n in names:
            _project(conn, n)
    else:
        names = projects_for_dir(conn, cwd)
        if not names:
            listing = "; ".join(f"{p['name']}" + (f" ({p['path']})" if p.get("path") else "") for p in project_list(conn))
            raise RiverError(
                f"no project is linked to {Path(cwd).resolve()}. Either run: river go --project <name>, "
                f"or link this folder: river project path <name> . "
                f"Projects: {listing or '(none; river project add <name> --path .)'}")
    area = ",".join(names)

    # Identity: reuse a registered name, else make one and register it.
    new_name = False
    if actor:
        if not conn.execute("SELECT 1 FROM agents WHERE name=?", (actor,)).fetchone():
            register(conn, actor, note=f"started with river go in {area}")
            new_name = True
    else:
        import secrets
        actor = f"{names[0]}-{secrets.token_hex(2)}"
        register(conn, actor, note=f"started with river go in {area}")
        new_name = True
    activity(conn, actor)

    ann = annotate(conn)
    descs = {p["name"]: p["notes"] for p in project_list(conn) if p["name"] in names}
    in_area = [a for a in ann.values() if a["project"] in names]
    open_in_area = [a for a in in_area if a["status"] in OPEN_STATES]
    human_ready = sorted((a for a in in_area if a["ready"] and a["doer"] == "human"), key=lambda a: a["sort_key"])
    brief = {"agent": actor, "new_name": new_name, "projects": names, "descriptions": descs,
             "human_waiting": [{"id": a["id"], "title": a["title"]} for a in human_ready],
             "messages": unread(conn, actor)}

    # Resume: an item already held.
    held = conn.execute("SELECT id FROM items WHERE assignee=? AND status IN ('in_progress','held') ORDER BY id",
                        (actor,)).fetchall()
    if held and role in (None, "worker", "unblocker"):
        item = item_show(conn, held[0]["id"])
        brief.update(role="worker", resumed=True, item=item,
                     why=f"you already hold #{item['id']}; finish or release it first")
        _set_role_note(conn, actor, "worker", item["id"])
        return brief

    def try_claim(**kw):
        try:
            got = next_item(conn, claim=True, actor=actor, **kw)
        except RiverError as e:
            brief["claim_refused"] = str(e)
            return None
        return got[0] if got else None

    if role in (None, "worker"):
        item = try_claim(project=area)
        if item:
            brief.update(role="worker", item=item, why=f"#{item['id']} is the most important ready item in {area}")
            _set_role_note(conn, actor, "worker", item["id"])
            return brief
    if role in (None, "unblocker"):
        item = try_claim(unblocks=names[0]) if len(names) == 1 else None
        if item is None and len(names) > 1:
            for n in names:
                item = try_claim(unblocks=n)
                if item:
                    break
        if item:
            brief.update(role="unblocker", item=item,
                         why=f"nothing is ready in {area}; #{item['id']} ({item['project']}) clears the way for it")
            _set_role_note(conn, actor, "unblocker", item["id"])
            return brief

    outside = [a for a in open_in_area if a["status"] == "open" and a["blocked_reason"]]
    held_by_others = [a for a in open_in_area if a["status"] in ("in_progress", "held")]
    needs_plan = not open_in_area or (not held_by_others and len(outside) == len([a for a in open_in_area if a["status"] == "open"]))
    if role == "planner" or (role is None and needs_plan):
        brief.update(role="planner", item=None,
                     open_items=[{"id": a["id"], "title": a["title"], "blocked_reason": a["blocked_reason"],
                                  "open_blockers": a["open_blockers"]} for a in open_in_area],
                     why=("the project has no open items" if not open_in_area
                          else "every open item waits on something outside the queue"))
        _set_role_note(conn, actor, "planner", None)
        return brief

    brief.update(role="idle", item=None,
                 held_by_others=[{"id": a["id"], "title": a["title"], "assignee": a["assignee"]} for a in held_by_others],
                 why=("other sessions hold all the work that can move now" if held_by_others
                      else "nothing here can move now, and nothing it waits on is ready"))
    _set_role_note(conn, actor, "idle", None)
    return brief


def _set_role_note(conn, actor, role, item_id):
    note = f"role: {role}" + (f" on #{item_id}" if item_id else "")
    with tx(conn):
        conn.execute("UPDATE agents SET note=? WHERE name=?", (note, actor))
