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

# One database per user. A repository clone that already has data/river.db keeps using it until
# `river db move` copies it to the home folder (sandboxed agents may not be allowed to write there yet).
HOME_DB = Path("~/.biggestriver/river.db")
LEGACY_DB = Path(__file__).resolve().parent.parent / "data" / "river.db"

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
    # A goal owner's claim on its goal: how long the ownership lasts without a river command of the owner
    # (each command renews it), and how long its leases on the goal's items last. river goal own --lease 6h
    # picks another length for one ownership. While a goal has an owner, its agent items are reserved for it.
    "goal_lease": "4h",
    # How long an agent may hold an item that waits on a person's item (added with --keep). Then river
    # releases the item (it still waits on the person), the agent takes other work, and the person is reminded.
    "human_wait_max": "30m",
    "owner_ttl": "8h",
    "reserve_ttl": "2h",
    "keep_prereq_limit": "3",
    "replan_threshold": "3",
    "default_prerequisite_mode": "release",
    "max_leases": "1",
    # Items that serve the agent's own outcome count apart, against goal_max_leases: items of a goal it owns,
    # items such an item waits on, and the deploy items of a target it owns. So an owner can take an urgent
    # prerequisite, or run its deploy, while it holds other work.
    "goal_max_leases": "3",
    "away_after": "1h",
    "gone_after": "24h",
    "question_nudge_after": "30m",
    "serve_port": "8765",
    "notify_channels": "",
    "notify_interval": "30s",
    "notify_batch_window": "60s",
    "ntfy_url": "https://ntfy.sh",
    # Where a phone notification opens when river knows no session link for it (a local page cannot open there).
    "ntfy_click": "https://claude.ai/code",
    "ntfy_topic": "",
    "ntfy_token": "",
    "email_to": "",
    "email_from": "",
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "email_batch_window": "10m",
    "timezone": "",
    "auto_continue": "on",
    "due_warn_before": "3d",
    # Agents the page can start, as "Label=command" entries separated by ";". The first is the default.
    # Claude Code starts with Remote Control, so the session has a web link that notifications open.
    # {model} and {effort} take the launch dialog's choices; with no choice the flag before them drops out.
    "launch_agents": "Claude Code=claude --model {model} --effort {effort} go --remote-control",
    "launch_in": "tab",
    # How river reaches a running session through its own platform, so a working agent sees a queue
    # instruction, a stop, or a message at once: "Label=ENV_VAR: command" entries separated by ";". river go
    # records the platform whose ENV_VAR is set in the session's environment, and its value as the address;
    # delivery runs the command with {address} and {message} filled in. The command "uds {address} {message}"
    # is built in: river writes the message as one line to that Unix socket. Claude Code's session inbox
    # (CLAUDE_CODE_MESSAGING_SOCKET, code.claude.com/docs/en/cross-session-messaging) takes such a connection,
    # but its message format is not documented, and a probe did not see the line arrive, so Claude Code is
    # not in the default: add "Claude Code=CLAUDE_CODE_MESSAGING_SOCKET: uds {address} {message}" to try it.
    # A platform without an entry has no native path: the message waits in the queue and the inbox for the
    # agent's next river command.
    "native_message": "Codex=CODEX_THREAD_ID: codex queue --thread {address} --message {message}",
    "setup_done": "off",
    # Review before release: with review on, each deploy item waits on a review item that waits on
    # everything the release ships. review_prompt tells the reviewer what to do (your review process);
    # review_cmd, when set, must exit 0 before river review pass accepts the review.
    # Each project can also keep an ordered list of review steps (river review step add): the review
    # of a release follows the steps of every project it ships, next to review_prompt and review_cmd.
    # Set them globally or on the deploy project (deploy-<target>).
    # The outside tracker a project uses, in words an agent can act on: which tracker, where, and with
    # what tool, e.g. "github owner/shop via gh" or "jira PROJ via the Jira MCP server". Set it per
    # project (river project tracker). River has no tracker API code: agents use their own tools.
    "tracker": "",
    # A session with no work runs river wait: it takes new work when it comes, and ends after wait_max
    # without any (0: end at once). One river wait call returns after wait_step, below a shell time limit.
    "wait_max": "30m",
    "wait_step": "9m",
    # river cleanup lists a ready item that nobody claimed for this long.
    "stale_after": "14d",
    "review": "off",
    "review_prompt": "",
    "review_cmd": "",
    # Models, weakest to strongest, one list per family ("family: a, b, c", families separated by ";").
    # There is no order across families: a limit compares only models of the same family.
    "model_ladder": "claude: sonnet, opus, fable; openai: luna, terra, sol, astra",
    "effort_levels": "low, medium, high, xhigh, max",
    # What an item gets when it names none itself. Set them per project (--project) or per item kind
    # (--kind deploy); for example monitors: default_model sonnet, default_effort low, default_max_model sonnet.
    # A recommendation never blocks; min/max limits keep a session whose model is outside them off the item.
    "default_model": "",
    "default_effort": "",
    "default_min_model": "",
    "default_max_model": "",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS targets (
  id                INTEGER PRIMARY KEY,
  name              TEXT NOT NULL UNIQUE,
  description       TEXT NOT NULL DEFAULT '',
  monitor           TEXT NOT NULL DEFAULT '',  -- what a session watches after each deploy (river target monitor)
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
  blocked_at        TEXT,
  blocked_until     TEXT,
  blocked_set_by    TEXT,
  assignee          TEXT,
  claimed_at        TEXT,
  lease_expires_at  TEXT,
  output            TEXT NOT NULL DEFAULT '',
  context           TEXT NOT NULL DEFAULT '',
  touches           TEXT NOT NULL DEFAULT '',
  "check"           TEXT NOT NULL DEFAULT '',
  kind              TEXT NOT NULL DEFAULT 'work',
  target            TEXT,
  reserved_for      TEXT,
  reserved_until    TEXT,
  reserved_by       TEXT,
  hold_expires_at   TEXT,
  held_at           TEXT,
  replan            INTEGER NOT NULL DEFAULT 0,
  late_prereqs      INTEGER NOT NULL DEFAULT 0,
  due               TEXT,
  due_warned        INTEGER NOT NULL DEFAULT 0,
  found_during      INTEGER REFERENCES items(id),
  takeover_by       TEXT,
  takeover_kind     TEXT,
  takeover_note     TEXT,
  takeover_at       TEXT,
  takeover_seen     INTEGER NOT NULL DEFAULT 0,
  needs_check       INTEGER NOT NULL DEFAULT 0,
  model             TEXT,                     -- recommended model (NULL: the default_model setting)
  effort            TEXT,                     -- recommended effort level (NULL: default_effort)
  min_model         TEXT,                     -- hard limits, one model per family, comma list
  max_model         TEXT,
  created_at        TEXT NOT NULL,
  closed_at         TEXT
);

-- A goal is an outcome in a project with a "done when" test. One agent owns it at a time
-- and creates and takes the items that reach it; items carry goal tags (item_goals).
CREATE TABLE IF NOT EXISTS goals (
  id                INTEGER PRIMARY KEY,
  project_id        INTEGER NOT NULL REFERENCES projects(id),
  name              TEXT NOT NULL UNIQUE,
  outcome           TEXT NOT NULL DEFAULT '',
  done_when         TEXT NOT NULL DEFAULT '',
  rank              REAL NOT NULL,
  status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','complete')),
  owner             TEXT,
  owner_expires_at  TEXT,
  owner_lease       TEXT,                     -- river goal own --lease; NULL: the goal_lease setting
  result            TEXT NOT NULL DEFAULT '',
  created_at        TEXT NOT NULL,
  completed_at      TEXT
);

CREATE TABLE IF NOT EXISTS item_goals (
  item_id  INTEGER NOT NULL REFERENCES items(id),
  goal_id  INTEGER NOT NULL REFERENCES goals(id),
  PRIMARY KEY (item_id, goal_id)
);

-- Links to issues in outside trackers (Jira, GitHub Issues, Linear...). River stores the link only;
-- agents read and update the tracker with their own tools.
CREATE TABLE IF NOT EXISTS item_refs (
  item_id     INTEGER NOT NULL REFERENCES items(id),
  ref         TEXT NOT NULL,
  url         TEXT,
  created_at  TEXT NOT NULL,
  synced_at   TEXT,
  PRIMARY KEY (item_id, ref)
);

-- An agent's own queue: items it takes before the project queue, and instructions it reads first.
-- Ordered by pos; no expiry and no accept (unlike a push). An item is in at most one queue.
CREATE TABLE IF NOT EXISTS queue_entries (
  id            INTEGER PRIMARY KEY,
  agent         TEXT NOT NULL,
  pos           REAL NOT NULL,
  item_id       INTEGER REFERENCES items(id),
  body          TEXT,                         -- an instruction entry: the text
  kind          TEXT NOT NULL DEFAULT 'item' CHECK (kind IN ('item','message','stop')),
  added_by      TEXT,
  created_at    TEXT NOT NULL,
  delivered_at  TEXT,
  native_status TEXT                          -- native delivery: sent, failed: <why>, or no native channel
);
CREATE UNIQUE INDEX IF NOT EXISTS queue_item ON queue_entries(item_id) WHERE item_id IS NOT NULL;

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
  role           TEXT,
  session        TEXT,
  session_ref    TEXT,
  session_url    TEXT,                       -- web link to the agent's session (Claude Code Remote Control)
  model          TEXT,                       -- the model the session runs (RIVER_MODEL, river go --model)
  pid            INTEGER,                    -- the agent CLI process that runs river, its host, and its command line
  host           TEXT,
  pid_cmd        TEXT,
  platform       TEXT,                       -- the agent CLI (a native_message label) and the session's address in it
  native_address TEXT,
  stop_at        TEXT,                       -- river stop: when, who, and why; the session ends after it
  stop_by        TEXT,
  stop_reason    TEXT,
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
  closed_at   TEXT,
  nudged_at   TEXT,
  native_status TEXT
);

-- Something needs a person: a human item became ready, or a question or alert went to a human.
CREATE TABLE IF NOT EXISTS needs_you (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL CHECK (kind IN ('item','message')),
  item_id       INTEGER REFERENCES items(id),
  message_id    INTEGER REFERENCES messages(id),
  human         TEXT,                       -- NULL: any person
  summary       TEXT NOT NULL,
  opened_at     TEXT NOT NULL,
  closed_at     TEXT,
  close_reason  TEXT
);

-- One row per (event, channel): each event notifies once per channel; a failed send retries.
-- A project's release review, step by step: 'do' is a written instruction the reviewer confirms,
-- 'run' a command that must exit 0 in the project folder. pos orders the steps (1 first).
CREATE TABLE IF NOT EXISTS review_steps (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES projects(id),
  pos         INTEGER NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('do','run')),
  text        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
  id          INTEGER PRIMARY KEY,
  event_id    INTEGER NOT NULL REFERENCES needs_you(id),
  channel     TEXT NOT NULL,
  state       TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sent','failed')),
  attempts    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  created_at  TEXT NOT NULL,
  sent_at     TEXT,
  UNIQUE (event_id, channel)
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


WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_DAY_NAMES = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_CLOCK = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$")


def local_zone(name: str = ""):
    """The zone for times a person types and reads: the timezone setting, else the machine's own."""
    if name:
        # UTC needs no time zone database; Windows has none unless the tzdata package is installed.
        if name.upper() in ("UTC", "Z", "GMT", "ETC/UTC"):
            return timezone.utc
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError, available_timezones
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            if not available_timezones():
                raise RiverError(f"time zone {name!r}: this computer has no time zone database (Windows has none "
                                 f"by default). Install it: python -m pip install tzdata; or use UTC")
            raise RiverError(f"unknown time zone {name!r}: use a name such as America/New_York or UTC")
    return datetime.now().astimezone().tzinfo


def parse_when(s: str, zone: str = "", start: datetime | None = None) -> datetime:
    """A future moment from what a person types, in UTC.

    Accepts a duration ('2h', '3d'), an ISO time ('2026-09-28T07:00', with Z or an
    offset, else in the zone), or '[day] [time] [zone]' where day is today,
    tomorrow, a weekday (mon .. sun, the next one), or YYYY-MM-DD, time is 7,
    07:00, or 7am, and zone is a name such as America/New_York. A weekday or a
    time alone means the next such moment; a day alone means its midnight."""
    start = start or now()
    text = s.strip()
    if _DURATION.match(text):
        return start + parse_duration(text)
    words = text.split()
    if words and "/" in words[-1] or (words and words[-1].upper() in ("UTC", "Z")):
        zone, words = ("UTC" if words[-1].upper() in ("UTC", "Z") else words[-1]), words[:-1]
    tz = local_zone(zone)
    if len(words) == 1 and "T" in words[0]:
        try:
            t = datetime.fromisoformat(words[0].replace("Z", "+00:00"))
        except ValueError:
            raise RiverError(f"bad time {s!r}: use for example 2026-09-28T07:00")
        t = t if t.tzinfo else t.replace(tzinfo=tz)
        return t.astimezone(timezone.utc).replace(microsecond=0)
    here = start.astimezone(tz)
    day = weekday = clock = None
    for w in words:
        lw = w.lower()
        if lw in ("today", "tomorrow"):
            day = here.date() + timedelta(days=1 if lw == "tomorrow" else 0)
        elif lw[:3] in WEEKDAYS and lw in (WEEKDAYS[WEEKDAYS.index(lw[:3])], _DAY_NAMES[WEEKDAYS.index(lw[:3])]):
            weekday = WEEKDAYS.index(lw[:3])
        elif re.match(r"^\d{4}-\d{2}-\d{2}$", lw):
            day = datetime.strptime(lw, "%Y-%m-%d").date()
        elif _CLOCK.match(lw):
            m = _CLOCK.match(lw)
            h, mi = int(m.group(1)), int(m.group(2) or 0)
            if m.group(3):
                h = h % 12 + (12 if m.group(3) == "pm" else 0)
            if h > 23 or mi > 59:
                raise RiverError(f"bad time of day {w!r}")
            clock = (h, mi)
        else:
            raise RiverError(f"bad time {s!r}: use a duration (2h), an ISO time (2026-09-28T07:00), "
                             f"or a day and time such as 'mon 07:00 America/New_York'")
    if day is None and weekday is None and clock is None:
        raise RiverError(f"bad time {s!r}: give a day, a time of day, or both")
    h, mi = clock or (0, 0)

    def at(d):
        return datetime(d.year, d.month, d.day, h, mi, tzinfo=tz)

    if day is not None:
        t = at(day)
    elif weekday is not None:
        t = at(here.date() + timedelta(days=(weekday - here.weekday()) % 7))
        if t <= here:
            t += timedelta(days=7)
    else:
        t = at(here.date())
        if t <= here:
            t = at(here.date() + timedelta(days=1))
    return t.astimezone(timezone.utc)


def parse_due(s: str | None, zone: str = "") -> str | None:
    """A due date as stored (UTC ISO), or None for 'none' / ''. A date alone means the end of that day."""
    if s is None or s.strip().lower() in ("", "none"):
        return None
    words = s.split()
    if words and re.match(r"^\d{4}-\d{2}-\d{2}$", words[0]) and not any(_CLOCK.match(w.lower()) for w in words[1:]):
        words.insert(1, "23:59")  # "due 2026-10-15" means by the end of that day
    return iso(parse_when(" ".join(words), zone))


def show_time(iso_s: str | None, zone: str = "") -> str:
    """'Mon 7:00 EDT' for a stored UTC time, in the zone; the date too when it is more than six days away."""
    if not iso_s:
        return ""
    tz = local_zone(zone)
    t = parse_iso(iso_s).astimezone(tz)
    day = t.strftime("%a") if abs((t - now().astimezone(tz)).days) < 6 else t.strftime("%a %Y-%m-%d")
    return f"{day} {t.hour}:{t.minute:02d} {t.strftime('%Z')}".strip()


# ---------------------------------------------------------------- connection

def db_path() -> Path:
    """RIVER_DB, else ~/.biggestriver/river.db, except that an existing data/river.db in a clone is kept
    while the home file does not exist yet."""
    if os.environ.get("RIVER_DB"):
        return Path(os.environ["RIVER_DB"]).expanduser()
    home = HOME_DB.expanduser()
    return LEGACY_DB if not home.exists() and LEGACY_DB.exists() else home


def queue_note():
    """When RIVER_DB points away from the main queue: one line that says which queue this is; else ""."""
    if not os.environ.get("RIVER_DB"):
        return ""
    p, home = db_path().resolve(), HOME_DB.expanduser().resolve()
    return "" if p == home else f"QUEUE: {p} (set by RIVER_DB), not the main queue {home}"


def db_move(force=False):
    """Copy the clone's data/river.db to ~/.biggestriver/river.db (SQLite backup, safe while it is open),
    then rename the old file to river.db.moved so every later command uses the new one."""
    if os.environ.get("RIVER_DB"):
        raise RiverError("RIVER_DB is set, so river does not use the default location; unset it first")
    src, dst = LEGACY_DB, HOME_DB.expanduser()
    if dst.exists():
        raise RiverError(f"{dst} exists already; river uses it (river db path)")
    if not src.exists():
        raise RiverError(f"no {src} to move; river already uses {dst}")
    old = connect(src)
    try:
        if not force:
            busy = [r["name"] for r in old.execute("SELECT name, last_seen FROM agents WHERE kind='ai'")
                    if parse_iso(r["last_seen"]) > now() - timedelta(minutes=10)]
            if busy:
                raise RiverError(f"agents were active in the last 10 minutes ({', '.join(busy)}); they would keep "
                                 f"writing to the old file. Stop them (and river serve), then run it again, "
                                 f"or add --force")
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".db.tmp")
        new = sqlite3.connect(tmp)
        try:
            old.backup(new)
        finally:
            new.close()
        tmp.rename(dst)
        n = old.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        old.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        old.close()
    src.rename(src.with_suffix(".db.moved"))
    for side in ("-wal", "-shm"):
        f = Path(str(src) + side)
        if f.exists():
            f.unlink()
    return {"from": str(src), "to": str(dst), "items": n, "backup": str(src.with_suffix(".db.moved"))}


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
    if "nudged_at" not in {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}:
        conn.execute("ALTER TABLE messages ADD COLUMN nudged_at TEXT")
    acols = {r["name"] for r in conn.execute("PRAGMA table_info(agents)")}
    if "role" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN role TEXT")
    if "session" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN session TEXT")
    if "session_ref" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN session_ref TEXT")
    if "waiting_since" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN waiting_since TEXT")
    if "waiting_in" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN waiting_in TEXT")
    if "session_url" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN session_url TEXT")
    if "model" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN model TEXT")
    for col in ("stop_at", "stop_by", "stop_reason", "platform", "native_address", "host", "pid_cmd"):
        if col not in acols:
            conn.execute(f"ALTER TABLE agents ADD COLUMN {col} TEXT")
    if "pid" not in acols:
        conn.execute("ALTER TABLE agents ADD COLUMN pid INTEGER")
    if "auto" not in {r["name"] for r in conn.execute("PRAGMA table_info(deps)")}:
        conn.execute("ALTER TABLE deps ADD COLUMN auto INTEGER NOT NULL DEFAULT 0")
    icols = {r["name"] for r in conn.execute("PRAGMA table_info(items)")}
    for col in ("context", "touches", "check"):
        if col not in icols:
            conn.execute(f"ALTER TABLE items ADD COLUMN \"{col}\" TEXT NOT NULL DEFAULT ''")
    if "kind" not in icols:
        conn.execute("ALTER TABLE items ADD COLUMN kind TEXT NOT NULL DEFAULT 'work'")
    if "target" not in icols:
        conn.execute("ALTER TABLE items ADD COLUMN target TEXT")
    if "reserved_until" not in icols:
        conn.execute("ALTER TABLE items ADD COLUMN reserved_until TEXT")
        conn.execute("ALTER TABLE items ADD COLUMN reserved_by TEXT")
    if "takeover_by" not in icols:
        for col, typ in (("takeover_by", "TEXT"), ("takeover_kind", "TEXT"), ("takeover_note", "TEXT"),
                         ("takeover_at", "TEXT"), ("takeover_seen", "INTEGER NOT NULL DEFAULT 0")):
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} {typ}")
    if "found_during" not in icols:
        conn.execute("ALTER TABLE items ADD COLUMN found_during INTEGER REFERENCES items(id)")
    if "due" not in icols:
        conn.execute("ALTER TABLE items ADD COLUMN due TEXT")
        conn.execute("ALTER TABLE items ADD COLUMN due_warned INTEGER NOT NULL DEFAULT 0")
    if "late_prereqs" not in icols:
        conn.execute("ALTER TABLE items ADD COLUMN late_prereqs INTEGER NOT NULL DEFAULT 0")
    for col in ("blocked_at", "blocked_until", "blocked_set_by"):
        if col not in icols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
    for col in ("model", "effort", "min_model", "max_model"):
        if col not in icols:
            conn.execute(f"ALTER TABLE items ADD COLUMN {col} TEXT")
    if "needs_check" not in icols:
        conn.execute("ALTER TABLE items ADD COLUMN needs_check INTEGER NOT NULL DEFAULT 0")
    if "native_status" not in {r["name"] for r in conn.execute("PRAGMA table_info(queue_entries)")}:
        conn.execute("ALTER TABLE queue_entries ADD COLUMN native_status TEXT")
    if "native_status" not in {r["name"] for r in conn.execute("PRAGMA table_info(messages)")}:
        conn.execute("ALTER TABLE messages ADD COLUMN native_status TEXT")
    if "synced_at" not in {r["name"] for r in conn.execute("PRAGMA table_info(item_refs)")}:
        conn.execute("ALTER TABLE item_refs ADD COLUMN synced_at TEXT")
    if "reserved_for" not in icols:
        conn.execute("ALTER TABLE items ADD COLUMN reserved_for TEXT")
        conn.execute("ALTER TABLE items ADD COLUMN hold_expires_at TEXT")
        conn.execute("ALTER TABLE items ADD COLUMN replan INTEGER NOT NULL DEFAULT 0")
    if "monitor" not in {r["name"] for r in conn.execute("PRAGMA table_info(targets)")}:
        conn.execute("ALTER TABLE targets ADD COLUMN monitor TEXT NOT NULL DEFAULT ''")
    if "owner_lease" not in {r["name"] for r in conn.execute("PRAGMA table_info(goals)")}:
        conn.execute("ALTER TABLE goals ADD COLUMN owner_lease TEXT")
    if "held_at" not in {r["name"] for r in conn.execute("PRAGMA table_info(items)")}:
        conn.execute("ALTER TABLE items ADD COLUMN held_at TEXT")


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
            project_id: int | None = None, kind: str | None = None) -> str:
    """Most specific value wins: item, agent, item kind, project, global, built-in."""
    if key not in DEFAULT_SETTINGS:
        raise RiverError(f"unknown setting {key!r}; known: {', '.join(sorted(DEFAULT_SETTINGS))}")
    if item_id is not None and (project_id is None or kind is None):
        r = conn.execute("SELECT project_id, kind FROM items WHERE id=?", (item_id,)).fetchone()
        if r:
            project_id = r["project_id"] if project_id is None else project_id
            kind = r["kind"] if kind is None else kind
    scopes = []
    if item_id is not None:
        scopes.append(f"item:{item_id}")
    if agent:
        scopes.append(f"agent:{agent}")
    if kind:
        scopes.append(f"kind:{kind}")
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


def _scope(conn, project=None, item=None, agent=None, kind=None) -> str:
    given = [x for x in (project, item, agent, kind) if x is not None]
    if len(given) > 1:
        raise RiverError("give at most one of --project, --item, --agent, --kind")
    if kind is not None:
        if not re.match(r"^[a-z][a-z0-9_-]*$", kind):
            raise RiverError("--kind is an item kind, for example work, deploy, review")
        return f"kind:{kind}"
    if project is not None:
        _project(conn, project)
        return f"project:{project}"
    if item is not None:
        _item(conn, item)
        return f"item:{item}"
    if agent is not None:
        return f"agent:{agent}"
    return "global"


def config_set(conn, key, value, project=None, item=None, agent=None, actor=None, kind=None):
    if key not in DEFAULT_SETTINGS:
        raise RiverError(f"unknown setting {key!r}; known: {', '.join(sorted(DEFAULT_SETTINGS))}")
    if key.endswith(("_ttl", "_after", "_before", "_interval", "_window")) or key in ("wait_max", "wait_step", "human_wait_max", "goal_lease"):
        parse_duration(value)
    elif key in ("keep_prereq_limit", "replan_threshold", "max_leases", "goal_max_leases", "serve_port", "smtp_port"):
        if not value.isdigit():
            raise RiverError(f"{key} takes a whole number")
    elif key == "launch_agents":
        parse_launch_agents(value)
    elif key == "model_ladder":
        parse_ladder(value)
    elif key == "native_message":
        parse_native(value)
    elif key == "effort_levels":
        if not _levels(value):
            raise RiverError("effort_levels is a comma list, lowest first: low, medium, high, xhigh, max")
    elif key == "default_effort" and value:
        _check_effort(conn, value)
    elif key == "default_model" and value:
        _check_model_name(value)
    elif key in ("default_min_model", "default_max_model") and value:
        _limit_list(parse_ladder(setting(conn, "model_ladder")), value, key)
    elif key == "review" and value not in ("on", "off"):
        raise RiverError("review is on or off")
    elif key == "setup_done" and value not in ("on", "off"):
        raise RiverError("setup_done is on or off")
    elif key == "launch_in" and value not in ("tab", "window"):
        raise RiverError("launch_in is tab or window")
    elif key == "auto_continue" and value not in ("on", "off"):
        raise RiverError("auto_continue is on or off")
    elif key == "default_prerequisite_mode" and value not in ("keep", "release"):
        raise RiverError("default_prerequisite_mode is keep or release")
    elif key in ("email_to", "email_from") and value and not all(
            re.match(r"^[^@\s,]+@[^@\s,]+\.[^@\s,]+$", x.strip()) for x in value.split(",")):
        raise RiverError(f"{key} is an email address" + (" (a comma list is fine)" if key == "email_to" else ""))
    elif key == "ntfy_click" and value and not re.match(r"^https?://[^\s/]+", value):
        raise RiverError("ntfy_click is a web link, for example https://claude.ai/code")
    elif key == "ntfy_url" and not re.match(r"^https?://[^\s/]+", value):
        raise RiverError("ntfy_url is the server address, for example https://ntfy.sh")
    elif key == "ntfy_topic" and value and not re.match(r"^[A-Za-z0-9_-]{1,64}$", value):
        raise RiverError("ntfy_topic uses letters, digits, '-' and '_' (up to 64); river notify setup ntfy makes one")
    elif key == "notify_channels" and not all(re.match(r"^[a-z0-9_-]+$", c) for c in _channels(value)):
        raise RiverError("notify_channels is a comma list of channel names, for example: mac,ntfy (empty sends nothing)")
    sc = _scope(conn, project, item, agent, kind)
    with tx(conn):
        conn.execute("INSERT INTO settings(scope,key,value) VALUES (?,?,?) "
                     "ON CONFLICT(scope,key) DO UPDATE SET value=excluded.value", (sc, key, value))
        _event(conn, None, actor, f"setting {sc} {key}={mask(key, value)}")
    return {"scope": sc, "key": key, "value": mask(key, value)}


def config_unset(conn, key, project=None, item=None, agent=None, actor=None, kind=None):
    sc = _scope(conn, project, item, agent, kind)
    with tx(conn):
        conn.execute("DELETE FROM settings WHERE scope=? AND key=?", (sc, key))
        _event(conn, None, actor, f"setting {sc} {key} unset")
    return {"scope": sc, "key": key}


# Anyone who knows these can read or send the notifications; config output shows only their end.
SECRET_SETTINGS = ("ntfy_topic", "ntfy_token")


def mask(key, value):
    if key in SECRET_SETTINGS and value:
        return "…" + value[-4:] if len(value) > 8 else "…"
    return value


def config_list(conn):
    rows = [dict(r) for r in conn.execute("SELECT scope,key,value FROM settings ORDER BY scope,key")]
    for r in rows:
        r["value"] = mask(r["key"], r["value"])
    return {"defaults": DEFAULT_SETTINGS, "overrides": rows}


# ---------------------------------------------------------------- models

MODEL_FIELDS = ("model", "effort", "min_model", "max_model")


def parse_ladder(value):
    """model_ladder: {family: [models weakest first]}. "claude: sonnet, opus; openai: luna, sol"."""
    fams, seen = {}, set()
    for n, part in enumerate(x for x in value.split(";") if x.strip()):
        name, _, models = part.rpartition(":")
        name = name.strip().lower() or f"family{n + 1}"
        ms = _levels(models)
        if not ms:
            raise RiverError(f"model_ladder: family {name} lists no models; write "
                             f"\"claude: sonnet, opus, fable; openai: luna, terra, sol, astra\"")
        for m in ms:
            if m in seen:
                raise RiverError(f"model_ladder: {m} is in more than one place")
            seen.add(m)
        fams[name] = ms
    return fams


def _levels(value):
    return [x.strip().lower() for x in (value or "").split(",") if x.strip()]


def _family(ladder, model):
    """(family, position) of a model in the ladder, or (None, None)."""
    m = (model or "").strip().lower()
    for fam, ms in ladder.items():
        if m in ms:
            return fam, ms.index(m)
    return None, None


def _check_model_name(value):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", value):
        raise RiverError(f"model {value!r}: a model name uses letters, digits, '.', '_', '-'")
    return value.lower()


def _check_effort(conn, value, levels=None):
    levels = levels or _levels(setting(conn, "effort_levels"))
    if value.lower() not in levels:
        raise RiverError(f"effort is one of {', '.join(levels)} (the effort_levels setting)")
    return value.lower()


def _limit_list(ladder, value, what="limit"):
    """A min/max limit: one model per family, each in the ladder. Returns the normal form "a, b"."""
    out, fams = [], set()
    for m in _levels(value):
        fam, _ = _family(ladder, m)
        if fam is None:
            known = "; ".join(f"{f}: {', '.join(ms)}" for f, ms in ladder.items())
            raise RiverError(f"{what}: {m} is not in the model_ladder setting ({known}); "
                             f"a limit needs an order. Add it: river config set model_ladder \"...\"")
        if fam in fams:
            raise RiverError(f"{what}: give at most one model per family ({fam} twice)")
        fams.add(fam)
        out.append(m)
    return ", ".join(out)


def _check_limits(ladder, lo, hi):
    """min <= max inside each family that both name."""
    for a in _levels(lo):
        fa, pa = _family(ladder, a)
        for b in _levels(hi):
            fb, pb = _family(ladder, b)
            if fa == fb and pa > pb:
                raise RiverError(f"min model {a} is stronger than max model {b} ({fa}: {', '.join(ladder[fa])})")


def model_check(ladder, model, lo, hi):
    """Whether a session running `model` may take an item with limits lo/hi (comma lists).

    Returns (allowed, reason). A limit compares only models of the session's family; a limit that names
    no model of that family, or a model the ladder does not know, does not apply, and the reason says so."""
    if not (lo or hi):
        return True, ""
    if not model:
        return True, ""
    fam, pos = _family(ladder, model)
    if fam is None:
        return True, (f"limits (min {lo or '-'}, max {hi or '-'}) not checked: model {model} is not in "
                      f"model_ladder, so it has no order")
    notes = []
    for label, lim in (("min", lo), ("max", hi)):
        if not lim:
            continue
        same = [m for m in _levels(lim) if _family(ladder, m)[0] == fam]
        if not same:
            notes.append(f"{label} {lim} is another family than {model} ({fam}): no order across families, "
                         f"so the limit does not apply")
            continue
        _, p = _family(ladder, same[0])
        if label == "min" and pos < p:
            return False, f"needs at least {same[0]}; {model} is weaker ({fam}: {', '.join(ladder[fam])})"
        if label == "max" and pos > p:
            return False, f"allows at most {same[0]}; {model} is stronger ({fam}: {', '.join(ladder[fam])})"
    return True, "; ".join(notes)


def _model_defaults(conn):
    """The default_* settings by scope, read once for annotate."""
    rows = {}
    for r in conn.execute("SELECT scope, key, value FROM settings WHERE key IN "
                          "('default_model','default_effort','default_min_model','default_max_model')"):
        rows.setdefault(r["scope"], {})[r["key"]] = r["value"]
    return rows


# Built-in defaults per item kind, below every setting: a monitor watches, so a weak model is enough.
KIND_MODELS = {"monitor": {"model": "sonnet", "effort": "low", "max_model": "sonnet"}}


def _item_models(it, project, defaults, ladder_text=DEFAULT_SETTINGS["model_ladder"]):
    """Effective model, effort, and limits of an item: its own, else the most specific default_* setting,
    else the built-in default of its kind (KIND_MODELS)."""
    scopes = [f"item:{it['id']}", f"kind:{it['kind']}", f"project:{project}", "global"]
    out = {}
    for f in MODEL_FIELDS:
        own = it[f]
        if own:
            out[f], out[f + "_from"] = own, "item"
            continue
        out[f], out[f + "_from"] = None, None
        for sc in scopes:
            v = defaults.get(sc, {}).get("default_" + f)
            if v is not None:
                if v:
                    out[f], out[f + "_from"] = v, sc
                break
        else:
            v = KIND_MODELS.get(it["kind"], {}).get(f)
            if v:
                out[f], out[f + "_from"] = v, f"kind:{it['kind']}"
    # A default limit that contradicts the item's own limit gives way to it.
    lo, hi = out["min_model"], out["max_model"]
    if lo and hi and (out["min_model_from"] == "item") != (out["max_model_from"] == "item"):
        try:
            _check_limits(parse_ladder(ladder_text), lo, hi)
        except RiverError:
            f = "max_model" if out["min_model_from"] == "item" else "min_model"
            out[f], out[f + "_from"] = None, None
    return out


def agent_model(conn, name):
    r = conn.execute("SELECT model FROM agents WHERE name=?", (name,)).fetchone() if name else None
    return r["model"] if r else None


def set_agent_model(conn, name, model):
    """Record the model a session runs; go, next, and claim keep it off items whose limits exclude it."""
    model = _check_model_name(model) if model else None
    with tx(conn):
        if conn.execute("UPDATE agents SET model=? WHERE name=? AND model IS NOT ?", (model, name, model)).rowcount:
            _event(conn, None, name, f"model {model or 'unset'}")
    return model


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


def project_path(conn, name, path, actor=None, move=False):
    """Link a project to a folder, so `river go` run inside that folder finds it. A project linked to another
    folder that still exists moves only with move=True: agents in the old folder would stop finding it."""
    full = str(Path(path).expanduser().resolve()) if path else None
    with tx(conn):
        old = _project(conn, name)["path"]
        if old and full and old != full and Path(old).is_dir() and not move:
            raise RiverError(f"project {name} is linked to {old}; linking it to {full} moves it, and agents in "
                             f"{old} stop finding it. Only the user decides that: river project path {name} "
                             f"{full} --move. A new project for this folder instead: river init")
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
        goals=goal_list(conn, name, include_complete=False),
        tracker=setting(conn, "tracker", project_id=p["id"]),
    )
    return p


def project_list(conn):
    rows = [dict(r) for r in conn.execute(
        "SELECT p.*, (SELECT COUNT(*) FROM items i WHERE i.project_id=p.id AND i.status IN ('open','in_progress','held')) open_items "
        "FROM projects p WHERE archived=0 ORDER BY rank, id")]
    for r in rows:
        r["tracker"] = setting(conn, "tracker", project_id=r["id"])
    return rows


def project_tracker(conn, name, text=None, actor=None):
    """Show, set, or (with none) clear the outside tracker a project uses."""
    p = _project(conn, name)
    if text is not None:
        text = text.strip()
        if text.lower() in ("", "none"):
            if conn.execute("SELECT 1 FROM settings WHERE scope=? AND key='tracker'", (f"project:{name}",)).fetchone():
                config_unset(conn, "tracker", project=name, actor=actor)
        else:
            config_set(conn, "tracker", text, project=name, actor=actor)
    return {"name": p["name"], "tracker": setting(conn, "tracker", project_id=p["id"])}


# ---------------------------------------------------------------- goals

def _goal(conn, name):
    r = conn.execute("SELECT * FROM goals WHERE name=?", (name,)).fetchone()
    if not r:
        known = [x["name"] for x in conn.execute("SELECT name FROM goals WHERE status='open' ORDER BY rank")]
        raise RiverError(f"no goal {name!r}" + (f"; open goals: {', '.join(known)}" if known
                                                else "; add one: river goal add <project> <name> --outcome \"...\""))
    return r


def _tag(conn, item_id, goal, actor):
    g = _goal(conn, goal)
    if conn.execute("INSERT OR IGNORE INTO item_goals(item_id, goal_id) VALUES (?,?)", (item_id, g["id"])).rowcount:
        _event(conn, item_id, actor, f"tagged goal {goal}")
        if g["owner"] and actor and g["owner"] != actor:
            it = _item(conn, item_id)
            _send(conn, "notice", "river", f"{actor} added #{item_id} {it['title']} to your goal {goal}",
                  to=g["owner"], item_id=item_id)


def _goal_notice(conn, item_id, actor, verb, detail=None):
    """Tell each goal's owner when someone else claims or finishes an item tagged with it."""
    for g in conn.execute("SELECT g.name, g.owner FROM item_goals ig JOIN goals g ON g.id=ig.goal_id "
                          "WHERE ig.item_id=? AND g.owner IS NOT NULL AND g.status='open'", (item_id,)).fetchall():
        if actor and g["owner"] != actor:
            it = _item(conn, item_id)
            _send(conn, "notice", "river", f"{actor} {verb} #{item_id} {it['title']} (your goal {g['name']})"
                  + (f": {detail}" if detail else ""),
                  to=g["owner"], item_id=item_id)


def _goal_view(conn, g, ann=None):
    ann = ann if ann is not None else annotate(conn)
    d = dict(g)
    d["project"] = _project_name(conn, g["project_id"])
    ids = [r["item_id"] for r in conn.execute("SELECT item_id FROM item_goals WHERE goal_id=?", (g["id"],))]
    items = [ann[i] for i in ids if i in ann]
    d["items_open"] = sorted(a["id"] for a in items if a["status"] in OPEN_STATES)
    d["items_done"] = sorted(a["id"] for a in items if a["status"] == "done")
    d["items_dropped"] = sorted(a["id"] for a in items if a["status"] == "dropped")
    return d


def goal_add(conn, project, name, outcome="", done_when="", actor=None, rank=None):
    if not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", name):
        raise RiverError("goal names use lower-case letters, digits, '.', '_', '-' (up to 64), like project names")
    with tx(conn):
        p = _project(conn, project)
        if conn.execute("SELECT 1 FROM goals WHERE name=?", (name,)).fetchone():
            raise RiverError(f"goal {name} already exists: river goal show {name}")
        top = conn.execute("SELECT COALESCE(MAX(rank),0) m FROM goals WHERE project_id=?", (p["id"],)).fetchone()["m"]
        conn.execute("INSERT INTO goals(project_id,name,outcome,done_when,rank,created_at) VALUES (?,?,?,?,?,?)",
                     (p["id"], name, outcome or "", done_when or "", top + 1, iso(now())))
        _event(conn, None, actor, f"goal {name} added to {project}")
    if rank is not None:
        goal_rank(conn, name, rank, actor)
    return goal_show(conn, name)


def goal_list(conn, project=None, include_complete=False):
    """Goals in order: project rank, then goal rank. Each with its owner and tagged-item progress."""
    ann = annotate(conn)
    sql = ("SELECT g.* FROM goals g JOIN projects p ON p.id=g.project_id WHERE p.archived=0"
           + ("" if include_complete else " AND g.status='open'") + (" AND p.name=?" if project else "")
           + " ORDER BY g.status='complete', p.rank, g.rank, g.id")
    return [_goal_view(conn, g, ann) for g in conn.execute(sql, (project,) if project else ())]


def goal_show(conn, name):
    g = _goal(conn, name)
    ann = annotate(conn)
    d = _goal_view(conn, g, ann)
    d["items"] = [{k: v for k, v in ann[i].items() if k != "sort_key"}
                  for i in sorted(d["items_open"] + d["items_done"] + d["items_dropped"],
                                  key=lambda i: (ann[i]["status"] in CLOSED_STATES, ann[i]["sort_key"]))]
    return d


def goal_rank(conn, name, rank, actor=None):
    """Put a goal at position `rank` (1 = first) among its project's goals."""
    with tx(conn):
        g = _goal(conn, name)
        names = [r["name"] for r in conn.execute("SELECT name FROM goals WHERE project_id=? AND name<>? "
                                                  "ORDER BY rank, id", (g["project_id"], name))]
        names.insert(max(0, min(int(rank) - 1, len(names))), name)
        for i, n in enumerate(names, 1):
            conn.execute("UPDATE goals SET rank=? WHERE name=?", (i, n))
        _event(conn, None, actor, f"goal {name} ranked {rank}")
    return goal_show(conn, name)


def goal_edit(conn, name, outcome=None, done_when=None, new_name=None, actor=None):
    with tx(conn):
        g = _goal(conn, name)
        if outcome is not None and outcome != g["outcome"]:
            conn.execute("UPDATE goals SET outcome=? WHERE id=?", (outcome, g["id"]))
            _event(conn, None, actor, f"goal {name}: outcome changed")
        if done_when is not None and done_when != g["done_when"]:
            conn.execute("UPDATE goals SET done_when=? WHERE id=?", (done_when, g["id"]))
            _event(conn, None, actor, f"goal {name}: done-when changed")
        if new_name and new_name != name:
            if not re.match(r"^[a-z0-9][a-z0-9._-]{0,63}$", new_name):
                raise RiverError("goal names use lower-case letters, digits, '.', '_', '-' (up to 64)")
            if conn.execute("SELECT 1 FROM goals WHERE name=?", (new_name,)).fetchone():
                raise RiverError(f"goal {new_name} already exists")
            conn.execute("UPDATE goals SET name=? WHERE id=?", (new_name, g["id"]))
            _event(conn, None, actor, f"goal {name} renamed to {new_name}")
            name = new_name
    return goal_show(conn, name)


def _goal_lease(conn, g, actor):
    """How long a goal owner's claim lasts: the length it chose (goal own --lease), else goal_lease."""
    return parse_duration(g["owner_lease"] or setting(conn, "goal_lease", agent=actor))


def _lease_for(conn, item_id, actor):
    """An item lease: goal_lease (or the owner's --lease) when the actor owns a goal the item serves, else lease_ttl."""
    g = conn.execute("SELECT g.* FROM item_goals ig JOIN goals g ON g.id=ig.goal_id WHERE ig.item_id=? AND g.owner=? "
                     "AND g.status='open' ORDER BY g.rank LIMIT 1", (item_id, actor)).fetchone() if actor else None
    if g is not None:
        return _goal_lease(conn, g, actor)
    return parse_duration(setting(conn, "lease_ttl", item_id=item_id, agent=actor))


def goal_own(conn, name, actor=None, lease=None):
    """Own a goal: create and take the items that reach it. One owner at a time. Its agent items are
    reserved for the owner, and its leases last goal_lease (or --lease); the ownership frees after that
    long without a river command of the owner."""
    if lease is not None:
        parse_duration(lease)
    if not actor:
        raise RiverError("owning a goal needs an agent name: set RIVER_AGENT or pass --as <name>")
    with tx(conn):
        _sweep(conn)
        g = _goal(conn, name)
        if g["status"] != "open":
            raise RiverError(f"goal {name} is complete; reopen it first: river goal reopen {name}")
        if g["owner"] and g["owner"] != actor:
            raise RiverError(f"goal {name} is owned by {g['owner']}; ask them (river send question --to {g['owner']} ...), "
                             f"or take another: river goal list")
        conn.execute("UPDATE goals SET owner=?, owner_lease=? WHERE id=?",
                     (actor, lease if lease is not None else (g["owner_lease"] if g["owner"] == actor else None), g["id"]))
        ttl = _goal_lease(conn, _goal(conn, name), actor)
        conn.execute("UPDATE goals SET owner_expires_at=? WHERE id=?", (iso(now() + ttl), g["id"]))
        if g["owner"] != actor:
            _event(conn, None, actor, f"owns goal {name} (its agent items are reserved for {actor}; lease {_short(ttl)})")
    return goal_show(conn, name)


def goal_release(conn, name, actor=None):
    with tx(conn):
        g = _goal(conn, name)
        if g["owner"] != actor:
            raise RiverError(f"goal {name} is " + (f"owned by {g['owner']}" if g["owner"] else "not owned"))
        conn.execute("UPDATE goals SET owner=NULL, owner_expires_at=NULL, owner_lease=NULL WHERE id=?", (g["id"],))
        _event(conn, None, actor, f"released goal {name}; its items are open to every agent")
    return goal_show(conn, name)


def goal_give(conn, name, to, actor=None):
    """Hand a goal you own to another agent; they get a notice."""
    with tx(conn):
        g = _goal(conn, name)
        if g["owner"] != actor:
            raise RiverError(f"goal {name} is " + (f"owned by {g['owner']}" if g["owner"] else "not owned")
                             + "; only its owner gives it")
        rec = _agent(conn, to)
        if to == actor:
            raise RiverError("you own it already")
        ttl = parse_duration(setting(conn, "goal_lease", agent=to))
        conn.execute("UPDATE goals SET owner=?, owner_expires_at=?, owner_lease=NULL WHERE id=?",
                     (rec["name"], iso(now() + ttl), g["id"]))
        _event(conn, None, actor, f"gave goal {name} to {to}")
        _send(conn, "notice", actor, f"{actor} gave you goal {name}: {g['outcome']} (river goal show {name})", to=to)
    return goal_show(conn, name)


def goal_owner(conn, name):
    """The owner of a goal, for messages sent --goal <name>."""
    g = _goal(conn, name)
    if not g["owner"]:
        raise RiverError(f"nobody owns goal {name}; message an item holder instead (river goal show {name})")
    return g["owner"]


def goal_done(conn, name, result, actor=None, drop_open=False):
    """Declare a goal complete with a one-line result. Refused while tagged items are open, unless
    drop_open, which drops them with the result as the note."""
    if not (result or "").strip():
        raise RiverError("say what the goal achieved: river goal done <name> --result \"<one line>\"")
    g = _goal(conn, name)
    if g["status"] == "complete":
        raise RiverError(f"goal {name} is already complete")
    if g["owner"] and actor and g["owner"] != actor:
        raise RiverError(f"goal {name} is owned by {g['owner']}; the owner declares it complete")
    still = _goal_view(conn, g)["items_open"]
    if still and not drop_open:
        raise RiverError(f"goal {name} has open items: {', '.join('#' + str(i) for i in still)}. Finish them, untag them "
                         f"(river edit <id> --untag {name}), or drop them: river goal done {name} --result \"...\" --drop-open")
    for i in still:
        drop(conn, i, actor, note=f"goal {name} completed without it: {result}")
    with tx(conn):
        conn.execute("UPDATE goals SET status='complete', result=?, completed_at=?, owner=NULL, owner_expires_at=NULL "
                     "WHERE id=?", (result.strip(), iso(now()), g["id"]))
        _event(conn, None, actor, f"goal {name} complete: {result.strip()}")
    return goal_show(conn, name)


def goal_reopen(conn, name, actor=None):
    with tx(conn):
        g = _goal(conn, name)
        if g["status"] == "open":
            raise RiverError(f"goal {name} is open already")
        conn.execute("UPDATE goals SET status='open', completed_at=NULL WHERE id=?", (g["id"],))
        _event(conn, None, actor, f"goal {name} reopened")
    return goal_show(conn, name)


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


def target_monitor(conn, name, text, actor=None):
    """What to watch after each deploy of the target: health links, logs, error rates, and for how long.
    With it set, claiming a deploy item adds a monitor item, and river serve opens a session for it."""
    with tx(conn):
        _target(conn, name)
        conn.execute("UPDATE targets SET monitor=? WHERE name=?", (text.strip(), name))
        _event(conn, None, actor, f"target {name} monitor " + ("changed" if text.strip() else "removed"))
    return target_show(conn, name)


def _add_monitor(conn, dep, actor):
    """When a deploy starts: a monitor item for this release, if the target has a monitor text and none exists."""
    tg = conn.execute("SELECT * FROM targets WHERE name=?", (dep["target"],)).fetchone()
    if not tg or not tg["monitor"] or conn.execute(
            "SELECT 1 FROM items WHERE kind='monitor' AND found_during=?", (dep["id"],)).fetchone():
        return None
    ships = conn.execute("SELECT i.id, i.title FROM deps d JOIN items i ON i.id=d.blocked_by WHERE d.item_id=? "
                         "AND i.kind NOT IN ('review') ORDER BY i.id", (dep["id"],)).fetchall()
    context = (tg["monitor"] + "\nRelease: deploy #" + str(dep["id"]) + " of " + tg["name"]
               + (" ships " + "; ".join(f"#{r['id']} {r['title']}" for r in ships) if ships else ""))
    top = conn.execute("SELECT COALESCE(MAX(rank),0) m FROM items WHERE project_id=?", (dep["project_id"],)).fetchone()["m"]
    cur = conn.execute(
        "INSERT INTO items(project_id,title,notes,priority,rank,doer,context,kind,target,found_during,created_at) "
        "VALUES (?,?,?,?,?,'ai',?,'monitor',?,?,?)",
        (dep["project_id"], f"Monitor the {tg['name']} deploy #{dep['id']}",
         "Follow the deploy as the target's monitor text says; done when all is well, else alert and propose a rollback.",
         dep["priority"], top + 1, context, tg["name"], dep["id"], iso(now())))
    _event(conn, cur.lastrowid, actor, f"monitor for deploy #{dep['id']} ({tg['name']}) added")
    _event(conn, dep["id"], actor, f"found work: #{cur.lastrowid} monitor")
    return cur.lastrowid


def pending_monitors(conn):
    """Monitor items that no session holds and for which river opened no session yet."""
    return [dict(r) for r in conn.execute(
        "SELECT i.id, i.target, i.found_during FROM items i WHERE i.kind='monitor' AND i.status='open' "
        "AND i.assignee IS NULL AND i.reserved_for IS NULL AND NOT EXISTS (SELECT 1 FROM events e "
        "WHERE e.item_id=i.id AND e.change LIKE 'monitor session opened%') ORDER BY i.id")]


def target_own(conn, name, actor=None, takeover=None):
    """Become the one owner of a target. Refused while another agent owns it, unless that owner is
    away or gone and `takeover` says why: then the target moves now and the old owner is told."""
    if not actor:
        raise RiverError("owning a target needs an agent name: set RIVER_AGENT or pass --as <name>")
    reason = (takeover or "").strip()
    with tx(conn):
        _sweep(conn)
        _agent(conn, actor)
        t = _target(conn, name)
        prior = t["owner"] if t["owner"] and t["owner"] != actor else None
        if prior:
            owner = conn.execute("SELECT * FROM agents WHERE name=?", (prior,)).fetchone()
            state = _agent_state(conn, owner) if owner else "gone"
            left = parse_iso(t["owner_expires_at"]) - now()
            if state == "active" or takeover is None:
                hint = (f"; {prior} is {state} (last seen {owner['last_seen'] if owner else 'never'}): "
                        f"take it over with river target own {name} --takeover \"<why>\""
                        if state != "active" else "")
                raise RiverError(f"refused: target {name} is owned by {prior} "
                                 f"(until {t['owner_expires_at']}, {_short(left)} left unless they renew). "
                                 f"Ask them: river send question --to {prior} \"...\", "
                                 f"or they can hand it over: river target give {name} --to {actor}{hint}")
            if not reason:
                raise RiverError(f"say why you take target {name} from {prior}: "
                                 f"river target own {name} --takeover \"<why>\"")
        ttl = parse_duration(setting(conn, "owner_ttl", agent=actor))
        cur = conn.execute("UPDATE targets SET owner=?, owner_expires_at=? WHERE name=? AND (owner IS NULL OR owner=?)",
                           (actor, iso(now() + ttl), name, prior or actor))
        if cur.rowcount != 1:
            raise RiverError(f"target {name} changed owner while you asked; see: river target show {name}")
        if prior:
            _event(conn, None, actor, f"target {name} taken over from {prior} ({state}) by {actor}: {reason}")
            _send(conn, "notice", actor, f"{actor} took over target {name} while you were {state}: {reason}. "
                  f"You no longer run its deploys; ask {actor} or a person to give it back: "
                  f"river target give {name} --to {prior}", to=prior)
        elif t["owner"] != actor:
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
    """Hand a target to another agent. The owner can give it, and so can a person, who decides for the
    agents: the old owner is then told."""
    with tx(conn):
        _sweep(conn)
        t = _target(conn, name)
        giver = conn.execute("SELECT kind FROM agents WHERE name=?", (actor,)).fetchone() if actor else None
        by_person = bool(giver) and giver["kind"] == "human" and t["owner"] != actor
        if t["owner"] != actor and not by_person:
            raise RiverError(f"only the owner can give target {name}; it is owned by {t['owner'] or 'nobody'}"
                             + ("" if t["owner"] else f" (take it: river target own {name})"))
        _agent(conn, to)
        ttl = parse_duration(setting(conn, "owner_ttl", agent=to))
        conn.execute("UPDATE targets SET owner=?, owner_expires_at=? WHERE name=?", (to, iso(now() + ttl), name))
        _event(conn, None, actor, f"target {name} given to {to}"
               + (f" by {actor} (was {t['owner']})" if by_person and t["owner"] else ""))
        _send(conn, "notice", actor, f"{actor} gave you target {name}: you now run its deploys. "
              f"See: river target show {name}", to=to)
        if by_person and t["owner"] and t["owner"] != to:
            _send(conn, "notice", actor, f"{actor} gave target {name} to {to}; you no longer run its deploys",
                  to=t["owner"])
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


def targets_view(conn, ann=None):
    """Each target with its owner, projects, open deploy items (with what they ship), and last finished deploy."""
    ann = ann if ann is not None else annotate(conn)
    ships = lambda a: [{"id": b, "title": ann[b]["title"], "status": ann[b]["status"], "project": ann[b]["project"]}
                       for b in a["waits_on"] if b in ann and ann[b]["kind"] != "review"]
    review = lambda a: next(({"id": b, "status": ann[b]["status"], "assignee": ann[b]["assignee"]}
                             for b in a["waits_on"] if b in ann and ann[b]["kind"] == "review"
                             and ann[b]["status"] in OPEN_STATES), None)
    monitors = {}
    for a in ann.values():
        if a["kind"] == "monitor" and a["found_during"]:
            monitors.setdefault(a["found_during"], []).append(
                {"id": a["id"], "status": a["status"], "assignee": a["assignee"], "output": a["output"]})
    out = []
    for t in target_list(conn):
        deploys = [a for a in ann.values() if a["kind"] == "deploy" and a["target"] == t["name"]]
        pending = sorted((a for a in deploys if a["status"] in OPEN_STATES), key=lambda a: a["id"])
        done = sorted((a for a in deploys if a["status"] == "done"), key=lambda a: a["closed_at"] or "")
        last = done[-1] if done else None
        out.append(dict(t,
            project_names=[r["name"] for r in conn.execute(
                "SELECT name FROM projects WHERE target=? AND archived=0 AND name<>? ORDER BY rank, id",
                (t["name"], f"deploy-{t['name']}"))],
            pending=[{"id": a["id"], "title": a["title"], "status": a["status"], "assignee": a["assignee"],
                      "ready": a["ready"], "ships": ships(a), "review": review(a),
                      "monitors": monitors.get(a["id"], [])} for a in pending],
            last_deploy=({"id": last["id"], "title": last["title"], "closed_at": last["closed_at"],
                          "output": last["output"], "ships": ships(last)} if last else None),
            history=[{"id": a["id"], "title": a["title"], "closed_at": a["closed_at"], "output": a["output"],
                      "ships": ships(a), "done_by": _done_by(conn, a["id"]), "monitors": monitors.get(a["id"], [])}
                     for a in reversed(done[-10:])]))
    return out


def _done_by(conn, item_id):
    r = conn.execute("SELECT actor FROM events WHERE item_id=? AND change LIKE 'done%' ORDER BY id DESC LIMIT 1",
                     (item_id,)).fetchone()
    return r["actor"] if r else None


def deploy_now(conn, target, review=False, actor=None):
    """Deploy now (the page's Targets tab): the target's open deploy item should go out now.

    Refuses when it collects nothing. With review, the release first waits on one review of everything
    it ships, also when the review setting is off. Returns what to start (the review or the deploy item),
    whether it is ready, and what it still waits on; a ready deploy item alerts the target owner."""
    with tx(conn):
        _sweep(conn)
        tg = _target(conn, target)
        dep = conn.execute(f"SELECT * FROM items WHERE kind='deploy' AND target=? AND status IN {OPEN_STATES} "
                           "ORDER BY id LIMIT 1", (tg["name"],)).fetchone()
        if dep is not None and dep["status"] != "open":
            raise RiverError(f"deploy #{dep['id']} for {tg['name']} is already {dep['status'].replace('_', ' ')}"
                             + (f" by {dep['assignee']}" if dep["assignee"] else ""))
        shipped = [r["blocked_by"] for r in conn.execute(
            "SELECT d.blocked_by FROM deps d JOIN items i ON i.id=d.blocked_by WHERE d.item_id=? AND i.kind<>'review'",
            (dep["id"],))] if dep is not None else []
        if not shipped:
            raise RiverError(f"nothing is collected for {tg['name']}: ask for items to go out first "
                             f"(river ship <id>, or done --ship)")
        rv = _release_review(conn, dep, tg, actor, force=True) if review else None
        _event(conn, dep["id"], actor, "deploy now" + (" after a review" if review else ""))
    ann = annotate(conn)
    start = ann[rv["id"] if rv is not None else dep["id"]]
    out = {"target": tg["name"], "owner": tg["owner"], "deploy": {"id": dep["id"], "title": dep["title"]},
           "review": {"id": rv["id"], "title": rv["title"]} if rv is not None else None,
           "start": {"id": start["id"], "title": start["title"], "kind": start["kind"]}, "ready": start["ready"],
           "waits_on": [{"id": b, "title": ann[b]["title"], "status": ann[b]["status"]}
                        for b in start["waits_on"] if ann[b]["status"] in OPEN_STATES]}
    if start["ready"] and start["kind"] == "deploy" and tg["owner"]:
        with tx(conn):
            _send(conn, "alert", actor or "river", f"deploy now: #{dep['id']} {dep['title']} is ready; "
                  f"river go gives it to you", to=tg["owner"], item_id=dep["id"])
        out["alerted"] = tg["owner"]
    return out


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
             context="", touches=None, check="", blocks=None, mode=None, found_during=None, feeds=(), due=None,
             goals=None, refs=None, models=None):
    """Add an item. `after`: items it waits on. `feeds`: items it waits on and whose output it reads."""
    if doer not in DOERS:
        raise RiverError(f"doer is one of {', '.join(DOERS)}")
    if not (0 <= int(priority) <= 4):
        raise RiverError("priority is 0 (highest) to 4 (lowest)")
    with tx(conn):
        if found_during is not None:
            _item(conn, found_during)
        p = _project(conn, project)
        top = conn.execute("SELECT COALESCE(MAX(rank),0) m FROM items WHERE project_id=?", (p["id"],)).fetchone()["m"]
        cur = conn.execute(
            'INSERT INTO items(project_id,title,notes,priority,rank,doer,context,touches,"check",created_at) '
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (p["id"], title, notes, int(priority), top + 1, doer, context or "", _touches(touches) or "",
             check or "", iso(now())))
        iid = cur.lastrowid
        _event(conn, iid, actor, f"added to {project} at P{priority}")
        # Goal tags: the ones named, else the goal the actor owns in this item's project (if any).
        # goals=[] means no goal.
        names = list(goals) if goals is not None else [r["name"] for r in conn.execute(
            "SELECT name FROM goals WHERE owner=? AND status='open' AND project_id=? ORDER BY rank, id LIMIT 1",
            (actor, p["id"]))] if actor else []
        for g in names:
            _tag(conn, iid, g, actor)
        if refs:
            _add_refs(conn, iid, refs, actor)
        if models:
            _set_models(conn, conn.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone(), models, actor)
        if due:
            t = parse_due(due, setting(conn, "timezone"))
            conn.execute("UPDATE items SET due=? WHERE id=?", (t, iid))
            _event(conn, iid, actor, f"due {show_time(t, setting(conn, 'timezone'))}")
        if found_during is not None:
            conn.execute("UPDATE items SET found_during=? WHERE id=?", (int(found_during), iid))
            _event(conn, iid, actor, f"found during #{found_during}")
            _event(conn, int(found_during), actor, f"found work: #{iid} {title}")
        for b in after:
            _dep_add(conn, iid, int(b), actor)
        for b in feeds or ():
            _dep_add(conn, iid, int(b), actor, "feeds")
        _sync_conflicts(conn, iid, actor)
        if blocks is not None:
            _dep_add(conn, int(blocks), iid, actor)
            _prereq_mode(conn, int(blocks), [iid], actor, mode)
    a = item_show(conn, iid)
    if blocks is not None and doer == "human":
        parent = _item(conn, blocks)
        if parent["status"] == "held" and parent["assignee"] == actor:
            a["holder_wait"] = {"item": parent["id"], "until": parent["hold_expires_at"],
                                "max": setting(conn, "human_wait_max", item_id=parent["id"], agent=actor)}
    return a


def _set_models(conn, it, models, actor):
    """Set an item's model, effort, min_model, max_model from a dict; "" or "none" clears one (the default applies)."""
    ladder = parse_ladder(setting(conn, "model_ladder"))
    new = {f: it[f] for f in MODEL_FIELDS}
    for f, v in models.items():
        if f not in MODEL_FIELDS:
            raise RiverError(f"unknown model field {f}")
        if v is None:
            continue
        v = v.strip()
        if v.lower() in ("", "none"):
            new[f] = None
        elif f == "model":
            new[f] = _check_model_name(v)
        elif f == "effort":
            new[f] = _check_effort(conn, v)
        else:
            new[f] = _limit_list(ladder, v, f.replace("_", " "))
    _check_limits(ladder, new["min_model"], new["max_model"])
    for f in MODEL_FIELDS:
        if new[f] != it[f]:
            conn.execute(f"UPDATE items SET {f}=? WHERE id=?", (new[f], it["id"]))
            _event(conn, it["id"], actor, f"{f.replace('_', ' ')} {new[f] or 'unset'}")


REF_RE = re.compile(r"^[a-z][a-z0-9_.-]*:\S+$")


def ref_url(ref):
    """The web link a ref implies without settings: GitHub issues only."""
    m = re.match(r"^github:([\w.-]+/[\w.-]+)#(\d+)$", ref)
    return f"https://github.com/{m.group(1)}/issues/{m.group(2)}" if m else None


def parse_refs(refs, urls=()):
    """Pair --ref values with --ref-url values by position; check the form <tracker>:<key>."""
    refs, urls = [r.strip() for r in refs or ()], [u.strip() for u in urls or ()]
    if len(urls) > len(refs):
        raise RiverError("more --ref-url than --ref: give each URL after the ref it belongs to")
    out = []
    for i, r in enumerate(refs):
        if not REF_RE.match(r):
            raise RiverError(f"ref {r!r} is not <tracker>:<key>, for example jira:PROJ-123, github:owner/repo#12, "
                             f"linear:ENG-42")
        u = urls[i] if i < len(urls) and urls[i] else ref_url(r)
        if u and not re.match(r"^https?://\S+$", u):
            raise RiverError(f"ref URL {u!r} is not a web link")
        out.append((r, u))
    return out


def _add_refs(conn, item_id, pairs, actor):
    """Link refs to an item. Refused when an open item in the same project already has the ref."""
    it = _item(conn, item_id)
    for ref, url in pairs:
        other = conn.execute(f"SELECT i.id, i.title FROM item_refs r JOIN items i ON i.id=r.item_id WHERE r.ref=? "
                             f"AND i.id<>? AND i.project_id=? AND i.status IN {OPEN_STATES}",
                             (ref, it["id"], it["project_id"])).fetchone()
        if other:
            raise RiverError(f"{ref} is already linked to #{other['id']} {other['title']}, which is open in the "
                             f"same project: river show {other['id']}")
        old = conn.execute("SELECT url FROM item_refs WHERE item_id=? AND ref=?", (it["id"], ref)).fetchone()
        if old is None:
            conn.execute("INSERT INTO item_refs(item_id, ref, url, created_at) VALUES (?,?,?,?)",
                         (it["id"], ref, url, iso(now())))
            _event(conn, it["id"], actor, f"linked {ref}")
        elif url and url != old["url"]:
            conn.execute("UPDATE item_refs SET url=? WHERE item_id=? AND ref=?", (url, it["id"], ref))
            _event(conn, it["id"], actor, f"link {ref} URL changed")


def project_for_add(conn, cwd, related=None):
    """The project a new item goes to when none is named: the related item's, else the folder's."""
    if related is not None:
        return _project_name(conn, _item(conn, related)["project_id"])
    names = projects_for_dir(conn, cwd)
    if len(names) == 1:
        return names[0]
    if names:
        raise RiverError(f"this folder belongs to {', '.join(names)}; name one: river add <project> \"<title>\"")
    raise RiverError("no project named and this folder is not linked to one; "
                     "use: river add <project> \"<title>\" (or --blocks/--found-during <id>)")


def _project_name(conn, pid):
    return conn.execute("SELECT name FROM projects WHERE id=?", (pid,)).fetchone()["name"]


_PLAN_BULLET = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_PLAN_BOX = re.compile(r"^\[( |x|X)\]\s+")
_PLAN_PRIO = re.compile(r"^[Pp]([0-4])\b[:\s]*")
_PLAN_DOER = re.compile(r"\s*\((human|ai|agent|anyone|any)\)\s*$", re.I)


def parse_plan(text, priority=2, doer="any"):
    """Lines of a plan file as items, read as an outline: one item per line, and a line waits on the
    lines indented under it (its steps come first; `parent` is the line it is a step of). Markdown bullets, numbers, and '[ ]' boxes are dropped; a '[x]' line
    is skipped (already done), and so are blank lines, '#' headings, and '>' quotes. A leading 'P0'..'P4'
    sets the priority; a trailing '(human)', '(ai)', or '(anyone)' sets who can do it."""
    rows, stack = [], []  # stack: (indent, row index)
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.expandtabs(4)
        body = line.strip()
        if not body or body.startswith(("#", ">")):
            continue
        indent = len(line) - len(line.lstrip())
        body = _PLAN_BULLET.sub("", body)
        box = _PLAN_BOX.match(body)
        if box:
            body = body[box.end():]
        m = _PLAN_PRIO.match(body)
        prio = int(m.group(1)) if m else priority
        body = body[m.end():] if m else body
        d = _PLAN_DOER.search(body)
        who = doer if not d else {"agent": "ai", "anyone": "any"}.get(d.group(1).lower(), d.group(1).lower())
        body = body[:d.start()] if d else body
        while stack and stack[-1][0] >= indent:
            stack.pop()
        parent = stack[-1][1] if stack else None
        if box and box.group(1) in "xX":
            continue  # done already; its children wait on nothing from it
        if not body.strip():
            raise RiverError(f"plan line {n} has no title: {raw.strip()!r}")
        rows.append({"line": n, "title": body.strip(), "priority": prio, "doer": who, "parent": parent})
        stack.append((indent, len(rows) - 1))
    if not rows:
        raise RiverError("the plan has no items: one item per line; indent a line to make it wait on the line above")
    return rows


def add_plan(conn, project, text, actor=None, priority=2, doer="any", dry_run=False):
    """Add every item of a plan file (see parse_plan) to one project; each line waits on its steps."""
    rows = parse_plan(text, priority, doer)
    _project(conn, project)
    if dry_run:
        return {"project": project, "dry_run": True, "items": rows}
    ids = [item_add(conn, project, r["title"], r["priority"], "", r["doer"], (), actor)["id"] for r in rows]
    for r, i in zip(rows, ids):
        r["id"] = i
        if r["parent"] is not None:
            dep_add(conn, ids[r["parent"]], [i], actor)
    return {"project": project, "dry_run": False, "items": rows}


def item_edit(conn, item_id, title=None, notes=None, doer=None, project=None, actor=None,
              context=None, touches=None, check=None, due=None, goals=None, untag=None, refs=None, unref=None,
              models=None):
    with tx(conn):
        it = _item(conn, item_id)
        if models:
            _set_models(conn, it, models, actor)
        if refs:
            _add_refs(conn, it["id"], refs, actor)
        for r in unref or ():
            if not conn.execute("DELETE FROM item_refs WHERE item_id=? AND ref=?", (it["id"], r.strip())).rowcount:
                raise RiverError(f"#{it['id']} has no link {r}")
            _event(conn, it["id"], actor, f"unlinked {r.strip()}")
        for g in goals or ():
            _tag(conn, it["id"], g, actor)
        for g in untag or ():
            gid = _goal(conn, g)["id"]
            if conn.execute("DELETE FROM item_goals WHERE item_id=? AND goal_id=?", (it["id"], gid)).rowcount:
                _event(conn, it["id"], actor, f"untagged goal {g}")
        if due is not None:
            zone = setting(conn, "timezone")
            t = parse_due(due, zone)
            if t != it["due"]:
                conn.execute("UPDATE items SET due=?, due_warned=0 WHERE id=?", (t, it["id"]))
                _event(conn, it["id"], actor, f"due {show_time(t, zone)}" if t else "due date removed")
        if title is not None and title != it["title"]:
            conn.execute("UPDATE items SET title=? WHERE id=?", (title, it["id"]))
            _event(conn, it["id"], actor, "title changed")
        if notes is not None and notes != it["notes"]:
            conn.execute("UPDATE items SET notes=? WHERE id=?", (notes, it["id"]))
            _event(conn, it["id"], actor, "notes changed")
        if doer is not None and doer != it["doer"]:
            if doer not in DOERS:
                raise RiverError(f"doer is one of {', '.join(DOERS)}")
            conn.execute("UPDATE items SET doer=? WHERE id=?", (doer, it["id"]))
            _event(conn, it["id"], actor, f"doer {it['doer']} -> {doer}")
        if project is not None and _project(conn, project)["id"] != it["project_id"]:
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


def _goal_work(conn, item_id, actor):
    """Whether an item serves the actor's own outcome: it carries a goal the actor owns, an open item of such
    a goal waits on it, or it deploys a target the actor owns. Such items count toward goal_max_leases."""
    if not actor:
        return False
    it = _item(conn, item_id)
    if it["kind"] == "deploy" and conn.execute("SELECT 1 FROM targets WHERE name=? AND owner=?",
                                               (it["target"], actor)).fetchone():
        return True
    tagged = {r[0] for r in conn.execute(
        "SELECT ig.item_id FROM item_goals ig JOIN goals g ON g.id=ig.goal_id WHERE g.owner=? AND g.status='open' "
        "AND g.owner_expires_at >= ?", (actor, iso(now())))}
    if not tagged:
        return False
    if it["id"] in tagged:
        return True
    return bool(tagged & _reachable(conn, it["id"], "SELECT d.item_id FROM deps d JOIN items i ON i.id=d.item_id "
                                    "WHERE d.blocked_by=? AND d.kind<>'conflicts' AND i.status IN ('open','in_progress','held')"))


def _lease_room(conn, item_id, actor, states=("in_progress", "held")):
    """(held, limit, key): the items the actor holds that count against the same limit as this item."""
    goal = _goal_work(conn, item_id, actor)
    key = "goal_max_leases" if goal else "max_leases"
    rows = conn.execute(f"SELECT id FROM items WHERE assignee=? AND status IN ({','.join('?' * len(states))})",
                        (actor, *states)).fetchall()
    held = sum(1 for r in rows if _goal_work(conn, r["id"], actor) == goal)
    return held, int(setting(conn, key, agent=actor)), key


def _link(conn, a, b):
    """Any dependency row between two items, in either direction."""
    return conn.execute("SELECT * FROM deps WHERE (item_id=? AND blocked_by=?) OR (item_id=? AND blocked_by=?)",
                        (a, b, b, a)).fetchone()


def _dep_add(conn, item_id, blocked_by, actor, kind="blocks", auto=False, alert=True):
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
    if alert:
        _alert_new_prereq(conn, item_id, blocked_by, actor)


def _alert_new_prereq(conn, item_id, blocked_by, actor):
    """An open prerequisite added to an item someone holds, or to a deploy item, is news that must stop
    them: alert the holder and the deploy target's owner (not whoever added it)."""
    it, b = _item(conn, item_id), _item(conn, blocked_by)
    if it["status"] not in OPEN_STATES or b["status"] not in OPEN_STATES:
        return
    to = []
    if it["assignee"] and it["status"] in ("in_progress", "held"):
        to.append(it["assignee"])
    if it["kind"] == "deploy" and it["target"]:
        tg = conn.execute("SELECT owner FROM targets WHERE name=?", (it["target"],)).fetchone()
        if tg and tg["owner"]:
            to.append(tg["owner"])
    for who in dict.fromkeys(to):
        if who == actor:
            continue
        what = "deploy" if it["kind"] == "deploy" else "item"
        _send(conn, "alert", actor or "river",
              f"#{b['id']} {b['title']} was added before your {what} #{it['id']} {it['title']}. "
              f"Stop and wait for it: river done {it['id']} is refused while it is open (river blockers {it['id']}).",
              to=who, item_id=it["id"])


def dep_add(conn, item_id, on, actor=None, kind="blocks", mode=None):
    """Link items. With mode keep or release, also hold or release the parent (design 7.3)."""
    with tx(conn):
        for b in on:
            _dep_add(conn, int(item_id), int(b), actor, kind)
        if mode and kind != "conflicts":
            _prereq_mode(conn, int(item_id), [int(b) for b in on], actor, mode)
    return item_show(conn, item_id)


def dep_remove(conn, item_id, on, actor=None):
    with tx(conn):
        for b in on:
            i, b = int(item_id), int(b)
            conn.execute("DELETE FROM deps WHERE (item_id=? AND blocked_by=?) "
                         "OR (kind='conflicts' AND item_id=? AND blocked_by=?)", (i, b, b, i))
            _event(conn, i, actor, f"no longer linked to {b}")
    return item_show(conn, item_id)


def _norm_path(p):
    """One form for comparing paths: forward slashes (Windows gives backslashes), and no case on Windows."""
    p = p.replace("\\", "/")
    return p.lower() if os.name == "nt" else p


def _paths_overlap(a, b):
    """Same file, or one path is a directory that holds the other."""
    a, b = _norm_path(a).rstrip("/"), _norm_path(b).rstrip("/")
    return a == b or b.startswith(a + "/") or a.startswith(b + "/")


def _touch_keys(touches, project_id, root):
    """Touches as comparable keys: a full path when the project has a folder (or the touch is absolute),
    else the relative path scoped to its project, so two repositories' 'public/' never meet."""
    r = os.path.realpath(os.path.expanduser(root)) if root else None
    keys = []
    for t in touches_list(touches):
        p = os.path.expanduser(t)
        if os.path.isabs(p):
            keys.append((None, os.path.realpath(p)))
        elif r:
            keys.append((None, os.path.normpath(os.path.join(r, p))))
        else:
            keys.append((project_id, os.path.normpath(p)))
    return keys


def _keys_overlap(a, b):
    return a[0] == b[0] and _paths_overlap(a[1], b[1])


def _sync_conflicts(conn, item_id, actor):
    """Keep automatic conflict links equal to the open items whose touches overlap this item's."""
    it = _item(conn, item_id)
    root = lambda pid: conn.execute("SELECT path FROM projects WHERE id=?", (pid,)).fetchone()["path"]
    mine = _touch_keys(it["touches"], it["project_id"], root(it["project_id"]))
    want = set()
    if it["status"] in OPEN_STATES and mine:
        for r in conn.execute(f"SELECT i.id, i.touches, i.project_id, p.path FROM items i JOIN projects p "
                              f"ON p.id=i.project_id WHERE i.id<>? AND i.status IN {OPEN_STATES} AND i.touches<>''",
                              (it["id"],)):
            theirs = _touch_keys(r["touches"], r["project_id"], r["path"])
            if any(_keys_overlap(x, y) for x in mine for y in theirs):
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


def _open_prereqs(conn, item_id):
    return [r["id"] for r in conn.execute(
        "SELECT i.id FROM deps d JOIN items i ON i.id=d.blocked_by "
        f"WHERE d.item_id=? AND d.kind<>'conflicts' AND i.status IN {OPEN_STATES}", (item_id,))]


def _human_prereqs(conn, item_id):
    """Open items for a person that this item waits on."""
    return [r["id"] for r in conn.execute(
        "SELECT i.id FROM deps d JOIN items i ON i.id=d.blocked_by "
        f"WHERE d.item_id=? AND d.kind<>'conflicts' AND i.doer='human' AND i.status IN {OPEN_STATES} ORDER BY i.id",
        (item_id,))]


def _hold_until(conn, item_id, actor, t):
    """When a hold ends: hold_ttl from t, but while a person's item is open before the held item, at most
    human_wait_max after the agent began to wait on it (the later of the hold and that item)."""
    until = t + parse_duration(setting(conn, "hold_ttl", item_id=item_id, agent=actor))
    human = _human_prereqs(conn, item_id)
    if human:
        held_at = conn.execute("SELECT held_at FROM items WHERE id=?", (item_id,)).fetchone()["held_at"]
        made = min(conn.execute(f"SELECT created_at FROM items WHERE id IN ({','.join('?' * len(human))})",
                                human).fetchall(), key=lambda r: r["created_at"])["created_at"]
        start = max(parse_iso(held_at) if held_at else t, parse_iso(made))
        until = min(until, start + parse_duration(setting(conn, "human_wait_max", item_id=item_id, agent=actor)))
    return until


def _hold(conn, parent, actor, reserve):
    t = now()
    conn.execute("UPDATE items SET held_at=CASE WHEN status='held' AND assignee=? AND held_at IS NOT NULL "
                 "THEN held_at ELSE ? END, status='held', assignee=?, lease_expires_at=NULL WHERE id=?",
                 (actor, iso(t), actor, parent))
    conn.execute("UPDATE items SET hold_expires_at=? WHERE id=?", (iso(_hold_until(conn, parent, actor, t)), parent))
    for r in reserve:
        conn.execute("UPDATE items SET reserved_for=? WHERE id=? AND status='open' AND reserved_for IS NULL", (actor, r))
    _event(conn, parent, actor, f"held by {actor} while it does " + ", ".join(f"#{r}" for r in reserve) if reserve
           else f"held by {actor} until its prerequisites are done")


def _unhold(conn, parent, actor, why):
    """Turn a hold into a release: the parent is open to everyone and its reservations end."""
    it = _item(conn, parent)
    conn.execute("UPDATE items SET status='open', assignee=NULL, claimed_at=NULL, lease_expires_at=NULL, "
                 "hold_expires_at=NULL WHERE id=?", (parent,))
    if it["assignee"]:
        for r in _open_prereqs(conn, parent):
            conn.execute("UPDATE items SET reserved_for=NULL WHERE id=? AND reserved_for=?", (r, it["assignee"]))
    _event(conn, parent, actor, why)


def _count_late(conn, parent, n, actor):
    """Count prerequisites added to a claimed item; at replan_threshold, mark it replan.

    Work found after an agent starts means the item was bigger than planned, so
    a planner looks at it again (river plan lists it; river replanned clears it)."""
    conn.execute("UPDATE items SET late_prereqs=late_prereqs+? WHERE id=?", (n, parent))
    it = _item(conn, parent)
    limit = int(setting(conn, "replan_threshold", item_id=parent, agent=actor))
    if it["late_prereqs"] >= limit and not it["replan"]:
        conn.execute("UPDATE items SET replan=1 WHERE id=?", (parent,))
        _event(conn, parent, "river", f"marked replan: {it['late_prereqs']} prerequisites added while it was "
               f"claimed (replan_threshold {limit})")


def replanned(conn, item_id, note=None, actor=None):
    """Clear the replan mark after a planner looked at the item again; the count starts over."""
    with tx(conn):
        it = _item(conn, item_id)
        conn.execute("UPDATE items SET replan=0, late_prereqs=0 WHERE id=?", (it["id"],))
        _event(conn, it["id"], actor, "replanned" + (f": {note}" if note else ""))
    return item_show(conn, item_id)


def _prereq_mode(conn, parent, new, actor, mode):
    """After prerequisites land on a parent: keep it (hold) or release it, per design 7.3."""
    p = _item(conn, parent)
    if p["status"] in ("in_progress", "held"):
        _count_late(conn, parent, len(new), actor)
    if p["status"] not in ("in_progress", "held") or p["assignee"] != actor:
        if mode == "keep":
            raise RiverError(f"--keep needs you to hold #{parent}; it is {p['status']}"
                             + (f" (held by {p['assignee']})" if p["assignee"] else ""))
        return
    mode = mode or setting(conn, "default_prerequisite_mode", item_id=parent, agent=actor)
    if mode == "keep":
        limit = int(setting(conn, "keep_prereq_limit", item_id=parent, agent=actor))
        n = len(_open_prereqs(conn, parent))
        if n > limit:
            conn.execute("UPDATE items SET replan=1 WHERE id=?", (parent,))
            _unhold(conn, parent, actor, f"released, not kept: {n} open prerequisites is more than "
                    f"keep_prereq_limit {limit}; marked replan")
            return
        _hold(conn, parent, actor, new)
    else:
        _unhold(conn, parent, actor, "released: new prerequisites " + ", ".join(f"#{r}" for r in new))


def keep(conn, item_id, actor=None):
    """Turn a release back into a hold: own the parent again and reserve its free open prerequisites."""
    if not actor:
        raise RiverError("keeping needs an agent name: set RIVER_AGENT or pass --as <name>")
    with tx(conn):
        _sweep(conn)
        p = _item(conn, item_id)
        if p["status"] == "held" and p["assignee"] == actor:
            return item_show(conn, item_id)
        if p["status"] not in ("open", "in_progress") or (p["assignee"] and p["assignee"] != actor):
            raise RiverError(f"#{item_id} is {p['status']}" + (f" by {p['assignee']}" if p["assignee"] else "")
                             + "; only an open item, or one you hold, can be kept")
        prereqs = _open_prereqs(conn, item_id)
        if not prereqs:
            raise RiverError(f"#{item_id} waits on nothing open; claim it instead: river claim {item_id}")
        taken = [r for r in conn.execute(
            f"SELECT id, assignee, reserved_for FROM items WHERE id IN ({','.join('?' * len(prereqs))})", prereqs)
            if (r["assignee"] and r["assignee"] != actor) or (r["reserved_for"] and r["reserved_for"] != actor)]
        if taken:
            raise RiverError("refused: " + ", ".join(f"#{r['id']} is taken by {r['assignee'] or r['reserved_for']}"
                                                    for r in taken) + f"; #{item_id} stays open")
        limit = int(setting(conn, "keep_prereq_limit", item_id=item_id, agent=actor))
        if len(prereqs) > limit:
            raise RiverError(f"refused: #{item_id} has {len(prereqs)} open prerequisites, more than keep_prereq_limit "
                             f"{limit}. Plan it instead: split the work, or let other agents take the prerequisites")
        _agent(conn, actor)
        _hold(conn, item_id, actor, prereqs)
    return item_show(conn, item_id)


def push(conn, item_id, to, note=None, actor=None):
    """Reserve an open item for one agent and alert it. It expires after reserve_ttl if nobody answers."""
    with tx(conn):
        _sweep(conn)
        it = _item(conn, item_id)
        _agent(conn, to)
        if it["status"] != "open":
            raise RiverError(f"#{item_id} is {it['status']}" + (f" by {it['assignee']}" if it["assignee"] else "")
                             + "; only an open item can be pushed")
        if it["reserved_for"] and it["reserved_for"] != to:
            raise RiverError(f"#{item_id} is already reserved for {it['reserved_for']}"
                             + (" (pushed)" if it["reserved_until"] else " (a prerequisite of an item it holds)"))
        ttl = parse_duration(setting(conn, "reserve_ttl", item_id=it["id"], agent=to))
        until = now() + ttl
        conn.execute("UPDATE items SET reserved_for=?, reserved_until=?, reserved_by=? WHERE id=?",
                     (to, iso(until), actor, it["id"]))
        _event(conn, it["id"], actor, f"pushed to {to}" + (f": {note}" if note else ""))
        _send(conn, "alert", actor or "river",
              f"{actor or 'someone'} pushed #{it['id']} {it['title']} to you" + (f": {note}" if note else "") +
              f". Take it: river accept {it['id']}   or: river decline {it['id']} --note \"why\"   "
              f"(reserved for you for {_short(ttl)})", to=to, item_id=it["id"])
    return item_show(conn, item_id)


def accept(conn, item_id, actor=None):
    """Take an item pushed to you."""
    it = _item(conn, item_id)
    if it["reserved_for"] != actor or not it["reserved_until"]:
        raise RiverError(f"#{item_id} is not pushed to {actor}" +
                         (f" (reserved for {it['reserved_for']})" if it["reserved_for"] else "") +
                         f"; claim it instead: river claim {item_id}")
    return claim(conn, item_id, actor)


def decline(conn, item_id, note=None, actor=None):
    """Hand a pushed item back: it is open to everyone again and the pusher hears why."""
    with tx(conn):
        it = _item(conn, item_id)
        if it["reserved_for"] != actor or not it["reserved_until"]:
            raise RiverError(f"#{item_id} is not pushed to {actor}")
        conn.execute("UPDATE items SET reserved_for=NULL, reserved_until=NULL, reserved_by=NULL WHERE id=?", (it["id"],))
        conn.execute("UPDATE messages SET state='declined', read_at=COALESCE(read_at, ?) "
                     "WHERE kind='alert' AND item_id=? AND to_agent=? AND state='open'", (iso(now()), it["id"], actor))
        _event(conn, it["id"], actor, "declined the push" + (f": {note}" if note else ""))
        if it["reserved_by"] and it["reserved_by"] != actor:
            _send(conn, "notice", actor, f"{actor} declined #{it['id']} {it['title']}" + (f": {note}" if note else "")
                  + "; it is open to every agent again", to=it["reserved_by"], item_id=it["id"])
    return item_show(conn, item_id)


def offer(conn, body, item, to=None, actor=None, goal=None):
    """Offer help to the agent that holds an item you are blocked on (design 7.5 step 4)."""
    if not actor:
        raise RiverError("offering needs an agent name: set RIVER_AGENT or pass --as <name>")
    if goal is not None and to is None:
        to = goal_owner(conn, goal)
    with tx(conn):
        it = _item(conn, item)
        to = to or it["assignee"] or it["reserved_for"]
        if not to:
            raise RiverError(f"nobody holds #{item}; take it yourself: river claim {item} (or river next --unblocks {item} --claim)")
        if to == actor:
            raise RiverError("you hold it yourself")
        _agent(conn, to)
        mid = _send(conn, "offer", actor, body.strip(), to=to, item_id=it["id"])
        _event(conn, it["id"], actor, f"offer #{mid} to {to}")
        conn.execute("UPDATE messages SET body=body || ? WHERE id=?", (
            f"\n(answer: river give <id> --to {actor}   or: river split {it['id']} \"<smaller piece>\" ...   "
            f"or: river decline {mid} --message --note \"why\")", mid))
    return message_show(conn, mid)


def _accept_offers(conn, holder, helper, t):
    conn.execute("UPDATE messages SET state='accepted', read_at=COALESCE(read_at, ?), closed_at=? "
                 "WHERE kind='offer' AND state='open' AND to_agent=? AND from_agent=?", (t, t, holder, helper))


def _goal_holder(conn, item_id):
    """Who owns an open goal of an open agent item right now (its items are reserved for them), or None."""
    it = _item(conn, item_id)
    if it["status"] != "open" or it["doer"] == "human":
        return None
    g = conn.execute("SELECT g.owner FROM item_goals ig JOIN goals g ON g.id=ig.goal_id WHERE ig.item_id=? "
                     "AND g.status='open' AND g.owner IS NOT NULL AND g.owner_expires_at >= ? ORDER BY g.rank LIMIT 1",
                     (item_id, iso(now()))).fetchone()
    return g["owner"] if g else None


def give(conn, item_id, to, actor=None):
    """Hand an item you hold (or that is reserved for you) to another agent; the lease moves with it."""
    with tx(conn):
        _sweep(conn)
        it = _item(conn, item_id)
        rec = _agent(conn, to)
        if to == actor:
            raise RiverError("you already have it")
        t = now()
        if it["assignee"] == actor and it["status"] in ("in_progress", "held"):
            if rec["role"] == "planner":
                raise RiverError(f"{to} is a planner session and takes no work")
            n, limit, key = _lease_room(conn, it["id"], to, ("in_progress",))
            if it["status"] == "in_progress" and n >= limit:
                raise RiverError(f"refused: {to} already holds {n} item(s) ({key} {limit}); they can release one first")
            ttl = _lease_for(conn, it["id"], to)
            if it["status"] == "in_progress":
                conn.execute("UPDATE items SET assignee=?, claimed_at=?, lease_expires_at=? WHERE id=?",
                             (to, iso(t), iso(t + ttl), it["id"]))
            else:
                conn.execute("UPDATE items SET assignee=?, hold_expires_at=? WHERE id=?",
                             (to, iso(_hold_until(conn, it["id"], to, t)), it["id"]))
                for r in _open_prereqs(conn, it["id"]):
                    conn.execute("UPDATE items SET reserved_for=? WHERE id=? AND reserved_for=?", (to, r, actor))
        elif it["reserved_for"] == actor and it["status"] == "open":
            conn.execute("UPDATE items SET reserved_for=? WHERE id=?", (to, it["id"]))
        elif it["status"] == "open" and not it["reserved_for"] and _goal_holder(conn, it["id"]) == actor:
            # A goal owner hands on one of the goal's items: it is reserved for the other agent like a push,
            # and comes back to the goal when they do not take it within reserve_ttl.
            ttl = parse_duration(setting(conn, "reserve_ttl", item_id=it["id"], agent=to))
            conn.execute("UPDATE items SET reserved_for=?, reserved_until=?, reserved_by=? WHERE id=?",
                         (to, iso(t + ttl), actor, it["id"]))
        else:
            raise RiverError(f"#{item_id} is not yours to give (" + (f"{it['status']} by {it['assignee']}" if it["assignee"]
                             else f"reserved for {it['reserved_for']}" if it["reserved_for"] else it["status"]) + ")")
        _event(conn, it["id"], actor, f"given to {to}")
        _accept_offers(conn, actor, to, iso(t))
        _send(conn, "notice", actor, f"{actor} gave you #{it['id']} {it['title']}; it is yours now "
              f"(river show {it['id']})", to=to, item_id=it["id"])
    return item_show(conn, item_id)


def split(conn, item_id, titles, actor=None, doer="any"):
    """Add smaller prerequisites anyone can take; if you hold the item it waits for them, still yours."""
    if not titles:
        raise RiverError("give at least one title for a smaller piece")
    with tx(conn):
        it = _item(conn, item_id)
        pname = _project_name(conn, it["project_id"])
    new = [item_add(conn, pname, t, it["priority"], "", doer, (), actor, found_during=None)["id"] for t in titles]
    with tx(conn):
        for n in new:
            _dep_add(conn, it["id"], n, actor)
            _event(conn, n, actor, f"split from #{it['id']}")
        cur = _item(conn, item_id)
        if cur["assignee"] == actor and cur["status"] in ("in_progress", "held"):
            _hold(conn, it["id"], actor, [])
        conn.execute("UPDATE messages SET state='accepted', closed_at=? WHERE kind='offer' AND state='open' "
                     "AND to_agent=? AND item_id=?", (iso(now()), actor, it["id"]))
    res = item_show(conn, item_id)
    res["split_into"] = new
    return res


def accept_message(conn, msg_id, actor=None):
    """Say yes to an alert (design 7.4): claim its item now, or, while you hold other work, keep it
    reserved for you until after that (reserve_ttl). The sender hears which."""
    with tx(conn):
        m = _message(conn, msg_id)
        if m["kind"] != "alert":
            raise RiverError(f"message {msg_id} is {'an' if m['kind'][0] in 'aeiou' else 'a'} {m['kind']}; only alerts are "
                             f"accepted by message id (an offer: river give <item> --to <agent>, or river split)")
        if m["to_agent"] != actor:
            raise RiverError(f"message {msg_id} is for {m['to_agent']}, not {actor}")
        if m["state"] not in ("open", "read"):  # reading an alert does not answer it
            raise RiverError(f"message {msg_id} is already {m['state']}")
        t = iso(now())
        conn.execute("UPDATE messages SET state='accepted', read_at=COALESCE(read_at, ?), closed_at=? WHERE id=?",
                     (t, t, m["id"]))
        if m["item_id"] is None:
            _send(conn, "note", actor, f"yes to your alert #{m['id']}", to=m["from_agent"], reply_to=m["id"])
            return message_show(conn, msg_id)
        it = _item(conn, m["item_id"])
        busy = conn.execute("SELECT id FROM items WHERE assignee=? AND status IN ('in_progress','held')",
                            (actor,)).fetchall()
        ann = annotate(conn)
        now_ok = it["status"] == "open" and not busy and ann[it["id"]]["ready"] and not (
            it["reserved_for"] and it["reserved_for"] != actor)
        if not now_ok:
            if it["status"] != "open" or (it["reserved_for"] and it["reserved_for"] != actor):
                raise RiverError(f"#{it['id']} is {it['status']}" + (f" by {it['assignee']}" if it["assignee"] else "")
                                 + (f", reserved for {it['reserved_for']}" if it["reserved_for"] else "")
                                 + f"; decline instead: river decline {msg_id} --message --note \"why\"")
            ttl = parse_duration(setting(conn, "reserve_ttl", item_id=it["id"], agent=actor))
            conn.execute("UPDATE items SET reserved_for=?, reserved_until=?, reserved_by=? WHERE id=?",
                         (actor, iso(now() + ttl), m["from_agent"], it["id"]))
            _event(conn, it["id"], actor, f"accepted alert #{m['id']}; kept for after "
                   + (", ".join(f"#{r['id']}" for r in busy) or "its prerequisites"))
            _send(conn, "note", actor, f"yes to your alert #{m['id']}: I take #{it['id']} after "
                  + (", ".join(f"#{r['id']}" for r in busy) or "what it waits on"),
                  to=m["from_agent"], item_id=it["id"], reply_to=m["id"])
            return message_show(conn, msg_id)
        _send(conn, "note", actor, f"yes to your alert #{m['id']}: I am taking #{it['id']} now",
              to=m["from_agent"], item_id=it["id"], reply_to=m["id"])
    claim(conn, it["id"], actor)
    return message_show(conn, msg_id)


def _holder_of(conn, item_id):
    it = _item(conn, item_id)
    if it["status"] not in ("in_progress", "held") or not it["assignee"]:
        raise RiverError(f"nobody holds #{item_id} ({it['status']}); name the agent, or use --item {item_id} "
                         f"so the next holder gets it")
    return it["assignee"]


def message(conn, kind, body, to=None, holder_of=None, item=None, file=None, cwd=None, actor=None, goal=None):
    """The shortcuts river alert / ask / note (design 7.4): to an agent, to the holder of an item, or
    (questions) to every agent whose held items touch a file. Returns the messages sent."""
    if sum(x is not None for x in (to, holder_of, file, goal)) > 1:
        raise RiverError("give one of: an agent name, --holder-of <id>, --goal <name>, or --file <path>")
    if goal is not None:
        to = goal_owner(conn, goal)
    if file is not None:
        if kind != "question":
            raise RiverError("--file is for questions: river ask --file <path> \"...\"")
        targets = [a["name"] for a in who(conn, file=file, cwd=cwd) if a["name"] != actor]
        if not targets:
            raise RiverError(f"no agent holds an item that touches {file} (river who --file {file})")
        return [send(conn, kind, body, t, item, None, actor) for t in targets]
    if holder_of is not None:
        to = _holder_of(conn, holder_of)
        if item is None:
            item = holder_of
    if to is None and item is None:
        raise RiverError("say who gets it: an agent name, --holder-of <id>, or --item <id> (its holder)")
    return [send(conn, kind, body, to, item, None, actor)]


def decline_message(conn, msg_id, note=None, actor=None):
    """Say no to an offer or alert; the sender hears why."""
    with tx(conn):
        m = _message(conn, msg_id)
        if m["kind"] not in ("offer", "alert"):
            raise RiverError(f"message {msg_id} is {'an' if m['kind'][0] in 'aeiou' else 'a'} {m['kind']}; only offers and alerts are declined")
        if m["to_agent"] != actor:
            raise RiverError(f"message {msg_id} is for {m['to_agent']}, not {actor}")
        if m["state"] not in ("open", "read"):  # reading an alert does not answer it
            raise RiverError(f"message {msg_id} is already {m['state']}")
        t = iso(now())
        conn.execute("UPDATE messages SET state='declined', read_at=COALESCE(read_at, ?), closed_at=? WHERE id=?", (t, t, m["id"]))
        _send(conn, "note", actor, f"no to your {m['kind']} #{m['id']}" + (f": {note}" if note else ""),
              to=m["from_agent"], item_id=m["item_id"], reply_to=m["id"])
    return message_show(conn, msg_id)


def cancel_push(conn, item_id, actor=None):
    """Take a push back before it is answered; the agent it went to hears about it."""
    with tx(conn):
        it = _item(conn, item_id)
        if not it["reserved_until"] or it["status"] != "open":
            raise RiverError(f"#{item_id} has no open push")
        to = it["reserved_for"]
        conn.execute("UPDATE items SET reserved_for=NULL, reserved_until=NULL, reserved_by=NULL WHERE id=?", (it["id"],))
        conn.execute("UPDATE messages SET state='declined', read_at=COALESCE(read_at, ?) "
                     "WHERE kind='alert' AND item_id=? AND to_agent=? AND state='open'", (iso(now()), it["id"], to))
        _event(conn, it["id"], actor, f"push to {to} cancelled")
        if to != actor:
            _send(conn, "notice", actor or "river", f"the push of #{it['id']} {it['title']} to you was cancelled",
                  to=to, item_id=it["id"])
    return item_show(conn, item_id)


def _resume_holds(conn, closed_id):
    """A prerequisite closed: every held parent with nothing left open goes back to its holder, in progress."""
    back = []
    for r in conn.execute("SELECT i.id, i.assignee FROM deps d JOIN items i ON i.id=d.item_id "
                          "WHERE d.blocked_by=? AND d.kind<>'conflicts' AND i.status='held'", (closed_id,)).fetchall():
        if not _open_prereqs(conn, r["id"]):
            ttl = _lease_for(conn, r["id"], r["assignee"])
            conn.execute("UPDATE items SET status='in_progress', hold_expires_at=NULL, claimed_at=?, lease_expires_at=? "
                         "WHERE id=?", (iso(now()), iso(now() + ttl), r["id"]))
            _event(conn, r["id"], "river", f"prerequisites done; back in progress for {r['assignee']}")
            back.append(r["id"])
    return back


def block(conn, item_id, reason=None, actor=None, until=None):
    """Record a blocker that is not an item in the queue ("waiting on Stripe review").

    With `until` (see parse_when) the blocker ends by itself at that time: the
    sweep clears it, and the item is ready again."""
    if not reason and not until:
        raise RiverError("say what the item waits on: --reason \"<what>\", --until <time>, or both")
    with tx(conn):
        it = _item(conn, item_id)
        zone = setting(conn, "timezone")
        t = iso(parse_when(until, zone)) if until else None
        if t and t <= iso(now()):
            raise RiverError(f"{until!r} is not in the future ({show_time(t, zone)})")
        reason = reason or "waiting for a set time"
        conn.execute("UPDATE items SET blocked_reason=?, blocked_until=?, blocked_set_by=?, "
                     "blocked_at=COALESCE(CASE WHEN blocked_reason IS NOT NULL THEN blocked_at END, ?) WHERE id=?",
                     (reason, t, actor, iso(now()), it["id"]))
        _event(conn, it["id"], actor, f"blocked: {reason}" + (f" (until {show_time(t, zone)})" if t else ""))
    return item_show(conn, item_id)


def unblock(conn, item_id, actor=None):
    with tx(conn):
        it = _item(conn, item_id)
        conn.execute("UPDATE items SET blocked_reason=NULL, blocked_until=NULL, blocked_at=NULL, blocked_set_by=NULL "
                     "WHERE id=?", (it["id"],))
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
    zone = setting(conn, "timezone")
    tags: dict[int, list] = {}
    for r in conn.execute("SELECT ig.item_id, g.name FROM item_goals ig JOIN goals g ON g.id=ig.goal_id "
                          "ORDER BY g.rank, g.id"):
        tags.setdefault(r["item_id"], []).append(r["name"])
    # A goal with an owner reserves its agent items for the owner (a person's items stay theirs).
    goal_owners = {r["name"]: r["owner"] for r in conn.execute(
        "SELECT name, owner FROM goals WHERE status='open' AND owner IS NOT NULL AND owner_expires_at >= ?",
        (iso(now()),))}
    queued = {r["item_id"]: r["agent"] for r in conn.execute(
        "SELECT item_id, agent FROM queue_entries WHERE item_id IS NOT NULL")}
    refs: dict[int, list] = {}
    for r in conn.execute("SELECT item_id, ref, url, synced_at FROM item_refs ORDER BY created_at, ref"):
        refs.setdefault(r["item_id"], []).append({"ref": r["ref"], "url": r["url"], "synced_at": r["synced_at"]})
    now_s = iso(now())
    soon_s = iso(now() + parse_duration(setting(conn, "due_warn_before")))
    model_defaults = _model_defaults(conn)
    ladder_text = setting(conn, "model_ladder")

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
        due, due_from = it["due"], None
        for d in deps_i:
            if items[d]["due"] and (due is None or items[d]["due"] < due):
                due, due_from = items[d]["due"], d
        a["effective_due"], a["due_from"] = (due, due_from) if is_open[i] else (it["due"], None)
        a.update(_item_models(it, p["name"], model_defaults, ladder_text))
        a["goals"] = tags.get(i, [])
        a["goal_reserved"] = None
        a["queued_for"] = queued.get(i)
        if a["queued_for"] and it["status"] == "open":
            a["reserved_for"] = a["queued_for"]  # other agents skip a queued item
        if not a["reserved_for"] and it["doer"] != "human" and it["status"] == "open":
            g = next((g for g in a["goals"] if g in goal_owners), None)
            if g:
                a["reserved_for"], a["goal_reserved"] = goal_owners[g], g
        a["refs"] = refs.get(i, [])
        a["due_text"] = show_time(a["effective_due"], zone)
        a["due_state"] = (None if not is_open[i] or not a["effective_due"]
                          else "overdue" if a["effective_due"] <= now_s
                          else "soon" if a["effective_due"] <= soon_s else None)
        a["blocked_until_text"] = show_time(it["blocked_until"], zone)
        a["blocked_text"] = ("" if not it["blocked_reason"] else "blocked" + (
            f" until {a['blocked_until_text']}" if it["blocked_until"] else "") + f": {it['blocked_reason']}")
        a["reason"] = _reason(a)
        a["sort_key"] = (eff, p["rank"], -len(deps_i), it["rank"], it["created_at"], i)
        out[i] = a
    return out


def _reason(a):
    parts = [f"P{a['effective_priority']}"]
    if a["priority_from"] is not None:
        parts[0] += f" inherited from #{a['priority_from']} (own P{a['priority']})"
    parts.append(f"project {a['project']} (rank {a['project_rank']})")
    if a["unblocks_count"]:
        n = a["unblocks_count"]
        parts.append(f"unblocks {n} item{'s' if n != 1 else ''}")
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


# ---------------------------------------------------------------- agent queues

def may_change_queue(conn, actor, agent):
    """Who may change an agent's queue: a person, or a manager session. Returns None or why not."""
    if not actor:
        return "name yourself: --as <name>"
    r = conn.execute("SELECT kind, role FROM agents WHERE name=?", (actor,)).fetchone()
    if r and (r["kind"] == "human" or r["role"] == "manager"):
        return None
    return (f"{actor} is an agent; a person or a manager changes queues. An agent lists its own queue "
            f"(river queue list) and removes its own instruction entries after it acts on them")


def _queue_rows(conn, agent):
    return conn.execute("SELECT * FROM queue_entries WHERE agent=? ORDER BY kind='item', pos, id", (agent,)).fetchall()


def _queue_pos(conn, agent, first=False, before=None, after=None):
    rows = conn.execute("SELECT item_id, pos FROM queue_entries WHERE agent=? ORDER BY pos", (agent,)).fetchall()
    if not rows:
        return 1.0
    if first:
        return rows[0]["pos"] - 1
    ref = before if before is not None else after
    if ref is None:
        return rows[-1]["pos"] + 1
    k = next((n for n, r in enumerate(rows) if r["item_id"] == int(ref)), None)
    if k is None:
        raise RiverError(f"#{ref} is not in the queue of {agent}")
    if before is not None:
        return (rows[k - 1]["pos"] + rows[k]["pos"]) / 2 if k else rows[k]["pos"] - 1
    return (rows[k]["pos"] + rows[k + 1]["pos"]) / 2 if k + 1 < len(rows) else rows[k]["pos"] + 1


def queue_add(conn, agent, item=None, message=None, first=False, before=None, actor=None, kind=None):
    """Put an item (at the end, first, or before another) or an instruction into an agent's queue."""
    why = may_change_queue(conn, actor, agent)
    if why:
        raise RiverError(f"refused: {why}")
    if (item is None) == (message is None):
        raise RiverError("give an item id or --message \"...\"")
    with tx(conn):
        ag = _agent(conn, agent)
        if ag["kind"] != "ai":
            raise RiverError(f"{agent} is a person; a queue is for an agent session")
        t = iso(now())
        if message is not None:
            k = kind or "message"
            eid = conn.execute("INSERT INTO queue_entries(agent,pos,body,kind,added_by,created_at) VALUES (?,?,?,?,?,?)",
                               (agent, _queue_pos(conn, agent, first=True) if k == "stop" else _queue_pos(conn, agent),
                                message, k, actor, t)).lastrowid
            _event(conn, None, actor, f"queue {agent}: {k} added")
            _send(conn, "notice", actor or "river", f"new {'stop request' if k == 'stop' else 'instruction'} in your "
                  f"queue: {message}", to=agent)
    if message is not None:
        _deliver_entry(conn, eid)
        return queue_list(conn, agent)
    with tx(conn):
        it = _item(conn, item)
        if it["status"] in CLOSED_STATES:
            raise RiverError(f"#{it['id']} is {it['status']}")
        if ag["kind"] != "ai":
            raise RiverError(f"{agent} is a person; a queue is for an agent session")
        if it["doer"] == "human":
            raise RiverError(f"#{it['id']} is for a person")
        q = conn.execute("SELECT agent FROM queue_entries WHERE item_id=?", (it["id"],)).fetchone()
        if q:
            raise RiverError(f"#{it['id']} is already in the queue of {q['agent']}" + (
                "" if q["agent"] == agent else f"; remove it there first: river queue remove {q['agent']} {it['id']}"))
        if it["status"] != "open" and it["assignee"] != agent:
            raise RiverError(f"#{it['id']} is {it['status']} by {it['assignee']}")
        conn.execute("INSERT INTO queue_entries(agent,pos,item_id,kind,added_by,created_at) VALUES (?,?,?,'item',?,?)",
                     (agent, _queue_pos(conn, agent, first, before), it["id"], actor, t))
        _event(conn, it["id"], actor, f"queued for {agent}")
        _send(conn, "notice", actor or "river", f"#{it['id']} {it['title']} is in your queue now; river go takes it "
              f"when it is ready", to=agent, item_id=it["id"])
    return queue_list(conn, agent)


def queue_list(conn, agent):
    ann = annotate(conn)
    out = []
    for r in _queue_rows(conn, agent):
        e = {"entry": r["id"], "kind": r["kind"], "added_by": r["added_by"], "created_at": r["created_at"],
             "delivered_at": r["delivered_at"], "native_status": r["native_status"]}
        if r["item_id"] is not None and r["item_id"] in ann:
            a = ann[r["item_id"]]
            e.update(item=a["id"], title=a["title"], project=a["project"], ready=a["ready"], status=a["status"],
                     open_blockers=a["open_blockers"])
        else:
            e["body"] = r["body"]
        out.append(e)
    return {"agent": agent, "entries": out}


def _queue_entry(conn, agent, ref):
    """An entry of the agent's queue by item id (12) or entry id (e5)."""
    ref = str(ref).strip().lstrip("#")
    if ref[:1] in ("e", "E") and ref[1:].isdigit():
        r = conn.execute("SELECT * FROM queue_entries WHERE agent=? AND id=?", (agent, int(ref[1:]))).fetchone()
    elif ref.isdigit():
        r = conn.execute("SELECT * FROM queue_entries WHERE agent=? AND item_id=?", (agent, int(ref))).fetchone()
    else:
        raise RiverError("name an item id (12) or an entry (e5; river queue list shows them)")
    if not r:
        raise RiverError(f"{ref} is not in the queue of {agent} (river queue list {agent})")
    return r


def queue_remove(conn, agent, ref, actor=None):
    with tx(conn):
        r = _queue_entry(conn, agent, ref)
        own_note = actor == agent and r["kind"] == "message"
        why = None if own_note else may_change_queue(conn, actor, agent)
        if why:
            raise RiverError(f"refused: {why}")
        conn.execute("DELETE FROM queue_entries WHERE id=?", (r["id"],))
        if r["kind"] == "stop":
            conn.execute("UPDATE agents SET stop_at=NULL, stop_by=NULL, stop_reason=NULL WHERE name=?", (agent,))
            _event(conn, None, actor, f"stop of {agent} withdrawn")
        _event(conn, r["item_id"], actor, f"removed from the queue of {agent}" if r["item_id"] else
               f"queue {agent}: {r['kind']} e{r['id']} removed")
    return queue_list(conn, agent)


def queue_move(conn, agent, item, before=None, after=None, actor=None):
    why = may_change_queue(conn, actor, agent)
    if why:
        raise RiverError(f"refused: {why}")
    if (before is None) == (after is None):
        raise RiverError("give --before <id> or --after <id>")
    with tx(conn):
        r = _queue_entry(conn, agent, item)
        if r["item_id"] is None:
            raise RiverError("instructions come first, in the order they were added; move items only")
        conn.execute("DELETE FROM queue_entries WHERE id=?", (r["id"],))
        pos = _queue_pos(conn, agent, before=before, after=after)
        conn.execute("INSERT INTO queue_entries(id,agent,pos,item_id,kind,added_by,created_at) VALUES (?,?,?,?,'item',?,?)",
                     (r["id"], agent, pos, r["item_id"], r["added_by"], r["created_at"]))
        _event(conn, r["item_id"], actor, f"moved in the queue of {agent}")
    return queue_list(conn, agent)


def queue_instructions(conn, agent, mark=True):
    """The agent's instruction entries, in order; marks them delivered."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM queue_entries WHERE agent=? AND kind<>'item' "
                                          "ORDER BY kind<>'stop', pos, id", (agent,))]
    if mark and rows:
        with tx(conn):
            conn.execute("UPDATE queue_entries SET delivered_at=? WHERE agent=? AND kind<>'item' AND delivered_at IS NULL",
                         (iso(now()), agent))
    return rows


def _queue_ready(conn, agent, ann=None):
    """The agent's queued items that are ready now, in queue order."""
    ann = ann or annotate(conn)
    return [ann[r["item_id"]] for r in conn.execute(
        "SELECT item_id FROM queue_entries WHERE agent=? AND item_id IS NOT NULL ORDER BY pos", (agent,))
        if r["item_id"] in ann and ann[r["item_id"]]["ready"]]


def _drop_queue(conn, agent, why, items_only=False):
    """The agent is gone or stopped: its queued items go back to the main queue; an active manager hears it.
    items_only keeps the instructions (a stop entry stays, so a person can withdraw the stop)."""
    rows = conn.execute("SELECT item_id FROM queue_entries WHERE agent=? AND item_id IS NOT NULL", (agent,)).fetchall()
    conn.execute("DELETE FROM queue_entries WHERE agent=?" + (" AND item_id IS NOT NULL" if items_only else ""), (agent,))
    for r in rows:
        _event(conn, r["item_id"], "river", f"back to the main queue ({agent} {why})")
    if rows:
        for m in conn.execute("SELECT * FROM agents WHERE role='manager' AND name<>?", (agent,)).fetchall():
            if _agent_state(conn, m) == "active":
                _send(conn, "notice", "river", f"{agent} {why}; its queued items went back to the main queue: "
                      + ", ".join(f"#{r['item_id']}" for r in rows), to=m["name"])
    return [r["item_id"] for r in rows]


# ---------------------------------------------------------------- agent processes

SHELLS = {"sh", "bash", "zsh", "fish", "dash", "ksh", "tcsh", "csh", "env", "timeout", "cmd.exe", "powershell.exe",
          "pwsh", "pwsh.exe", "sandbox-exec", "script"}


def this_host():
    import socket
    return socket.gethostname()


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def proc_info(pid):
    """(parent pid, program name, command line) of a process, from ps; None when it does not run or no ps."""
    import subprocess
    try:
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
        args = subprocess.run(["ps", "-o", "args=", "-p", str(pid)], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip()
    if out.returncode or not line:
        return None
    ppid, _, comm = line.partition(" ")
    return int(ppid), os.path.basename(comm.strip()), args.stdout.strip()


def agent_process(start=None):
    """The agent CLI process that runs this river command: the first ancestor above the shell(s) that is not
    a shell (SHELLS). Returns (pid, command line), or (None, None) when ps is missing (Windows) or it finds none."""
    pid = start or os.getppid()
    for _ in range(12):
        info = proc_info(pid)
        if info is None:
            return None, None
        ppid, comm, args = info
        if comm.lower().lstrip("-") not in SHELLS:
            return pid, args
        if ppid <= 1:
            return None, None
        pid = ppid
    return None, None


def set_process(conn, name, pid, cmd, host=None):
    with tx(conn):
        conn.execute("UPDATE agents SET pid=?, pid_cmd=?, host=? WHERE name=?", (pid, cmd, host or this_host(), name))


def kill_agent(conn, agent, reason, actor=None, grace=5.0, sleep=None):
    """Emergency only (the normal path is river stop): end the agent's process on this host, then release
    everything it held. Checks that the PID still runs the recorded command, so a reused PID is not killed."""
    import signal
    import subprocess
    import time
    sleep = sleep or time.sleep
    if not reason or not reason.strip():
        raise RiverError("say why: river stop <agent> --kill --reason \"...\"")
    why = may_change_queue(conn, actor, agent)
    if why:
        raise RiverError(f"refused: a person or a manager kills an agent ({why})")
    ag = _agent(conn, agent)
    if not ag["pid"]:
        raise RiverError(f"{agent} has no recorded process (river go records it); ask it to stop instead: "
                         f"river stop {agent} --reason \"...\"")
    if ag["host"] != this_host():
        raise RiverError(f"{agent} runs on {ag['host']}, not on this host ({this_host()}); kill it there")
    killed = False
    if pid_alive(ag["pid"]):
        info = proc_info(ag["pid"])
        if info is not None and ag["pid_cmd"] and info[2] != ag["pid_cmd"]:
            raise RiverError(f"refused: PID {ag['pid']} now runs another command ({info[2][:80]}), not {agent}'s; "
                             f"nothing was killed")
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(ag["pid"]), "/T", "/F"], capture_output=True)
        else:
            os.kill(ag["pid"], signal.SIGTERM)
            waited = 0.0
            while pid_alive(ag["pid"]) and waited < grace:
                sleep(0.2)
                waited += 0.2
                try:
                    os.waitpid(ag["pid"], os.WNOHANG)  # a child of this process (tests): reap it
                except (ChildProcessError, OSError):
                    pass
            if pid_alive(ag["pid"]):
                os.kill(ag["pid"], signal.SIGKILL)
        killed = True
    with tx(conn):
        held = [dict(r) for r in conn.execute(
            "SELECT id, title FROM items WHERE assignee=? AND status IN ('in_progress','held')", (agent,))]
        for h in held:
            conn.execute("UPDATE items SET status='open', assignee=NULL, claimed_at=NULL, lease_expires_at=NULL, "
                         "hold_expires_at=NULL, needs_check=1 WHERE id=?", (h["id"],))
            _event(conn, h["id"], actor, f"released: {agent} killed by {actor}: {reason.strip()}")
            for d in conn.execute("SELECT DISTINCT i.id, i.assignee FROM deps x JOIN items i ON i.id=x.item_id "
                                  "WHERE x.blocked_by=? AND i.assignee IS NOT NULL AND i.assignee<>? "
                                  "AND i.status IN ('in_progress','held')", (h["id"], agent)).fetchall():
                _send(conn, "notice", "river", f"#{h['id']} {h['title']}, which your #{d['id']} waits on, is open again: "
                      f"its agent {agent} was killed ({reason.strip()})", to=d["assignee"], item_id=d["id"])
        conn.execute("UPDATE items SET reserved_for=NULL, reserved_until=NULL, reserved_by=NULL "
                     "WHERE reserved_for=? AND status='open'", (agent,))
        for g in conn.execute("SELECT name FROM goals WHERE owner=?", (agent,)).fetchall():
            conn.execute("UPDATE goals SET owner=NULL, owner_expires_at=NULL, owner_lease=NULL WHERE name=?", (g["name"],))
            _event(conn, None, "river", f"goal {g['name']} released: {agent} killed")
        for t in conn.execute("SELECT name FROM targets WHERE owner=?", (agent,)).fetchall():
            conn.execute("UPDATE targets SET owner=NULL, owner_expires_at=NULL WHERE name=?", (t["name"],))
            _event(conn, None, "river", f"target {t['name']} released: {agent} killed")
        queued = _drop_queue(conn, agent, "was killed")
        conn.execute("UPDATE agents SET stop_at=?, stop_by=?, stop_reason=?, role='stopped', note='killed' WHERE name=?",
                     (iso(now()), actor, "killed: " + reason.strip(), agent))
        _event(conn, None, actor, f"killed {agent} (by {actor}: {reason.strip()})")
    return {"agent": agent, "pid": ag["pid"], "killed": killed, "released": held, "queued_back": queued,
            "warning": "Uncommitted work in the agent's folder is not saved: look at git status there."}


# ---------------------------------------------------------------- native delivery

NATIVE_RUNNER = None  # tests set a fake: f(args) -> (returncode, output)


def parse_native(value):
    """native_message: [(label, env_var, command template)]."""
    out = []
    for part in (value or "").split(";"):
        if not part.strip():
            continue
        label, sep, rest = part.partition("=")
        var, sep2, cmd = rest.partition(":")
        if not sep or not sep2 or not label.strip() or not re.match(r"^[A-Z][A-Z0-9_]*$", var.strip()) \
                or "{address}" not in cmd or "{message}" not in cmd:
            raise RiverError(f"native_message entry {part.strip()!r} needs the form Label=ENV_VAR: command with "
                             f"{{address}} and {{message}}, for example "
                             f"'Codex=CODEX_THREAD_ID: codex queue --thread {{address}} --message {{message}}'")
        out.append((label.strip(), var.strip(), cmd.strip()))
    return out


def native_from_env(conn, env):
    """(platform, address) of a session from its environment, or (None, None)."""
    for label, var, _ in parse_native(setting(conn, "native_message")):
        if env.get(var):
            return label, env[var]
    return None, None


def set_native(conn, name, platform, address):
    with tx(conn):
        if conn.execute("UPDATE agents SET platform=?, native_address=? WHERE name=? AND "
                        "(platform IS NOT ? OR native_address IS NOT ?)",
                        (platform, address, name, platform, address)).rowcount:
            _event(conn, None, name, f"native channel {platform or 'none'}")


def deliver_native(conn, agent, text):
    """Send text into the agent's running session through its platform. Returns the status to record:
    'sent', 'failed: <why>', or 'no native channel' (the queue and inbox still hold it)."""
    import shlex
    import subprocess
    r = conn.execute("SELECT platform, native_address FROM agents WHERE name=?", (agent,)).fetchone()
    if not r or not r["platform"] or not r["native_address"]:
        return "no native channel"
    entry = next((e for e in parse_native(setting(conn, "native_message")) if e[0] == r["platform"]), None)
    if entry is None:
        return f"no native channel ({r['platform']} has no native_message entry)"
    text = " ".join(text.split())  # one line: the socket reads a message per line
    args = [a.replace("{address}", r["native_address"]).replace("{message}", text) for a in shlex.split(entry[2])]
    try:
        if NATIVE_RUNNER is not None:
            code, out = NATIVE_RUNNER(args)
        elif args[0] == "uds":
            code, out = _uds_send(args[1], args[2])
        else:
            p = subprocess.run(args, capture_output=True, text=True, timeout=15)
            code, out = p.returncode, (p.stderr or p.stdout)
    except (OSError, subprocess.SubprocessError) as e:
        return f"failed: {e}"
    return "sent" if code == 0 else f"failed: exit {code}: {(out or '').strip()[:200]}"


def _uds_send(path, text, timeout=5):
    """Write one line to a Unix socket (a Claude Code session's inbox). Returns (code, error text)."""
    import socket
    if not hasattr(socket, "AF_UNIX"):
        return 1, "no Unix sockets on this system"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sk:
            sk.settimeout(timeout)
            sk.connect(path)
            sk.sendall((text + "\n").encode())
    except OSError as e:
        return 1, f"{path}: {e.strerror or e}"
    return 0, ""


def _native_text(kind, sender, body):
    return f"[river {kind} from {sender}] {body} (river --as <you> inbox; river go shows your queue)"


def _deliver_entry(conn, entry_id):
    r = conn.execute("SELECT * FROM queue_entries WHERE id=?", (entry_id,)).fetchone()
    st = deliver_native(conn, r["agent"], _native_text("stop request" if r["kind"] == "stop" else "instruction",
                                                       r["added_by"] or "river", r["body"]))
    with tx(conn):
        conn.execute("UPDATE queue_entries SET native_status=? WHERE id=?", (st, entry_id))
    return st


def stop_agent(conn, agent, reason, actor=None):
    """river stop: a request, not a kill. A stop entry goes to the front of the agent's queue; a waiting agent
    ends at once (river wait returns STOP), a working one on its next river command, after it commits and
    releases its item. A person or a manager may stop any agent; an agent may stop only itself."""
    if not reason or not reason.strip():
        raise RiverError("say why: river stop <agent> --reason \"...\"")
    if actor != agent:
        why = may_change_queue(conn, actor, agent)
        if why:
            raise RiverError(f"refused: an agent may not stop another; a person or a manager stops agents ({why})")
    with tx(conn):
        ag = _agent(conn, agent)
        if ag["kind"] != "ai":
            raise RiverError(f"{agent} is a person")
        if ag["stop_at"]:
            raise RiverError(f"{agent} is already asked to stop (by {ag['stop_by']}: {ag['stop_reason']})")
        t = iso(now())
        conn.execute("UPDATE agents SET stop_at=?, stop_by=?, stop_reason=? WHERE name=?", (t, actor, reason.strip(), agent))
        eid = conn.execute("INSERT INTO queue_entries(agent,pos,body,kind,added_by,created_at) VALUES (?,?,?,'stop',?,?)",
                           (agent, _queue_pos(conn, agent, first=True), reason.strip(), actor, t)).lastrowid
        _event(conn, None, actor, f"stopped {agent} (by {actor}: {reason.strip()})")
        holds = [dict(r) for r in conn.execute(
            "SELECT id, title, status FROM items WHERE assignee=? AND status IN ('in_progress','held') ORDER BY id", (agent,))]
        for h in holds:
            _event(conn, h["id"], actor, f"stop requested for {agent}: {reason.strip()}")
        _send(conn, "alert", actor or "river", f"STOP requested: {reason.strip()}. Commit finished work, release or "
              f"hand back your item with a note, then end this session.", to=agent)
    waiting = ag["role"] == "waiting" and not holds
    native = None if waiting else _deliver_entry(conn, eid)
    return {"agent": agent, "reason": reason.strip(), "waiting": waiting, "holds": holds, "native": native,
            "ends": "now: river wait returns STOP within seconds" if waiting
            else "after its next river command, once it commits and releases its item"}


def stop_request(conn, name):
    """The stop asked for this agent, or None."""
    r = conn.execute("SELECT stop_at, stop_by, stop_reason FROM agents WHERE name=?", (name,)).fetchone() if name else None
    return dict(r) if r and r["stop_at"] else None


def _finish_stop(conn, agent):
    """The stopped agent holds nothing now: release its goals and targets, give its queued items back."""
    with tx(conn):
        if conn.execute("SELECT 1 FROM items WHERE assignee=? AND status IN ('in_progress','held')", (agent,)).fetchone():
            return False
        for g in conn.execute("SELECT name FROM goals WHERE owner=?", (agent,)).fetchall():
            conn.execute("UPDATE goals SET owner=NULL, owner_expires_at=NULL, owner_lease=NULL WHERE name=?", (g["name"],))
            _event(conn, None, "river", f"goal {g['name']} released: {agent} stopped")
        for t in conn.execute("SELECT name FROM targets WHERE owner=?", (agent,)).fetchall():
            conn.execute("UPDATE targets SET owner=NULL, owner_expires_at=NULL WHERE name=?", (t["name"],))
            _event(conn, None, "river", f"target {t['name']} released: {agent} stopped")
        _drop_queue(conn, agent, "stopped", items_only=True)
        conn.execute("UPDATE agents SET role='stopped', note='stopped', waiting_since=NULL, waiting_in=NULL "
                     "WHERE name=?", (agent,))
        if not conn.execute("SELECT 1 FROM events WHERE actor='river' AND change=?", (f"{agent} ended (stopped)",)).fetchone():
            _event(conn, None, "river", f"{agent} ended (stopped)")
    return True


# ---------------------------------------------------------------- registry and leases

def _touch_agent(conn, actor):
    """Renew every lease and target ownership the actor holds, and record that it was seen."""
    if not actor:
        return
    t = now()
    conn.execute("UPDATE agents SET last_seen=? WHERE name=?", (iso(t), actor))
    for r in conn.execute("SELECT id FROM items WHERE assignee=? AND status='held'", (actor,)).fetchall():
        conn.execute("UPDATE items SET hold_expires_at=? WHERE id=?", (iso(_hold_until(conn, r["id"], actor, t)), r["id"]))
    owner_ttl = parse_duration(setting(conn, "owner_ttl", agent=actor))
    conn.execute("UPDATE targets SET owner_expires_at=? WHERE owner=?", (iso(t + owner_ttl), actor))
    # A goal owner keeps the goal while it runs river commands; it frees after goal_lease without one.
    for g in conn.execute("SELECT * FROM goals WHERE owner=? AND status='open'", (actor,)).fetchall():
        conn.execute("UPDATE goals SET owner_expires_at=? WHERE id=?", (iso(t + _goal_lease(conn, g, actor)), g["id"]))
    for r in conn.execute("SELECT id FROM items WHERE assignee=? AND status='in_progress'", (actor,)).fetchall():
        conn.execute("UPDATE items SET lease_expires_at=? WHERE id=?", (iso(t + _lease_for(conn, r["id"], actor)), r["id"]))


def _sweep(conn):
    t = iso(now())
    expired = conn.execute(
        "SELECT id, assignee FROM items WHERE status='in_progress' AND lease_expires_at < ?", (t,)).fetchall()
    for r in expired:
        # The session may have done part or all of the work: the next taker checks first (river check).
        conn.execute("UPDATE items SET status='open', assignee=NULL, claimed_at=NULL, lease_expires_at=NULL, "
                     "needs_check=1 WHERE id=?", (r["id"],))
        _event(conn, r["id"], "river", f"lease expired (was {r['assignee']}); back to open")
        _send(conn, "notice", "river", f"your lease on #{r['id']} expired; the item is open again. "
              f"Claim it again if you still work on it: river claim {r['id']}", to=r["assignee"], item_id=r["id"])
    for r in conn.execute("SELECT id, title, reserved_for, reserved_by FROM items WHERE reserved_until < ? "
                          "AND status='open'", (t,)).fetchall():
        conn.execute("UPDATE items SET reserved_for=NULL, reserved_until=NULL, reserved_by=NULL WHERE id=?", (r["id"],))
        _event(conn, r["id"], "river", f"push to {r['reserved_for']} expired; open to everyone")
        for who in {r["reserved_for"], r["reserved_by"]} - {None}:
            _send(conn, "notice", "river", f"the push of #{r['id']} {r['title']} to {r['reserved_for']} expired "
                  f"without an answer; it is open to every agent again", to=who, item_id=r["id"])
    for r in conn.execute("SELECT id, title, blocked_reason, blocked_set_by, doer FROM items "
                          "WHERE blocked_until <= ? AND blocked_reason IS NOT NULL", (t,)).fetchall():
        conn.execute("UPDATE items SET blocked_reason=NULL, blocked_until=NULL, blocked_at=NULL, blocked_set_by=NULL "
                     "WHERE id=?", (r["id"],))
        _event(conn, r["id"], "river", f"wait ended ({r['blocked_reason']}); outside blocker cleared")
        # A person's item reaches them through Needs you (sync_needs_you opens it once it is ready);
        # for other items, tell whoever set the wait.
        if r["blocked_set_by"] and r["doer"] != "human":
            _send(conn, "notice", "river", f"the wait on #{r['id']} {r['title']} ended "
                  f"({r['blocked_reason']}); it can start now", to=r["blocked_set_by"], item_id=r["id"])
    _due_warnings(conn, t)
    for r in conn.execute("SELECT DISTINCT a.* FROM agents a JOIN queue_entries q ON q.agent=a.name").fetchall():
        if _agent_state(conn, r) == "gone":
            _drop_queue(conn, r["name"], "is gone")
    for r in conn.execute("SELECT name, owner FROM goals WHERE owner IS NOT NULL AND owner_expires_at < ?",
                          (t,)).fetchall():
        conn.execute("UPDATE goals SET owner=NULL, owner_expires_at=NULL, owner_lease=NULL WHERE name=?", (r["name"],))
        _event(conn, None, "river", f"goal {r['name']} ownership expired (was {r['owner']})")
        _send(conn, "notice", "river", f"your ownership of goal {r['name']} expired (goal_lease without a river "
              f"command); nobody owns it now, and its items are open to every agent. "
              f"Take it again if you still work on it: river goal own {r['name']}", to=r["owner"])
    _question_nudges(conn)
    for r in conn.execute("SELECT id, title, assignee FROM items WHERE status='held' AND hold_expires_at < ?",
                          (t,)).fetchall():
        human = _human_prereqs(conn, r["id"])
        if human:
            _human_wait_ended(conn, r, human)
            continue
        _unhold(conn, r["id"], "river", f"hold expired (was {r['assignee']}); released")
        _send(conn, "notice", "river", f"your hold on #{r['id']} expired, so it is open to every agent now, and its "
              f"prerequisites are no longer reserved for you. Hold it again: river keep {r['id']}",
              to=r["assignee"], item_id=r["id"])
    for r in conn.execute("SELECT name, owner FROM targets WHERE owner IS NOT NULL AND owner_expires_at < ?",
                          (t,)).fetchall():
        conn.execute("UPDATE targets SET owner=NULL, owner_expires_at=NULL WHERE name=?", (r["name"],))
        _event(conn, None, "river", f"target {r['name']} ownership expired (was {r['owner']})")
        _send(conn, "notice", "river", f"your ownership of target {r['name']} expired; nobody owns it now. "
              f"Take it again if you still deploy there: river target own {r['name']}", to=r["owner"])
    return [r["id"] for r in expired]


def _human_wait_ended(conn, r, human):
    """An agent waited human_wait_max on a person's item: release its item (it still waits on the person),
    send the agent to other work, and remind every person of the answer it waits for."""
    wait = setting(conn, "human_wait_max", item_id=r["id"], agent=r["assignee"])
    names = ", ".join(f"#{h} {_item(conn, h)['title']}" for h in human)
    _unhold(conn, r["id"], "river", f"waited {wait} (human_wait_max) on {names}; released (was {r['assignee']})")
    _send(conn, "notice", "river", f"you waited {wait} (human_wait_max) for a person on {names}, so #{r['id']} "
          f"{r['title']} is open again and still waits on it. Take other work now: river go. When the person "
          f"finishes, #{r['id']} is ready for whoever runs go.", to=r["assignee"], item_id=r["id"])
    for h in (x["name"] for x in conn.execute("SELECT name FROM agents WHERE kind='human' ORDER BY name").fetchall()):
        _send(conn, "alert", "river", f"#{r['id']} {r['title']} waits on you: {names}. {r['assignee']} waited "
              f"{wait} and took other work; the item continues when you finish.", to=h, item_id=human[0])


def _due_warnings(conn, t):
    """Warn every person once when an open item's own due date comes close (due_warn_before), and
    once more when it passes. The warning is an alert, so it shows in Needs you and notifies."""
    zone = setting(conn, "timezone")
    humans = [r["name"] for r in conn.execute("SELECT name FROM agents WHERE kind='human' ORDER BY name")]
    for r in conn.execute(f"SELECT id, title, due, due_warned FROM items WHERE due IS NOT NULL "
                          f"AND status IN {OPEN_STATES} AND due_warned < 2").fetchall():
        soon = iso(parse_iso(r["due"]) - parse_duration(setting(conn, "due_warn_before", item_id=r["id"])))
        stage = 2 if r["due"] <= t else 1 if soon <= t else 0
        if stage <= r["due_warned"]:
            continue
        conn.execute("UPDATE items SET due_warned=? WHERE id=?", (stage, r["id"]))
        left_ = _open_prereqs(conn, r["id"])
        text = (f"#{r['id']} {r['title']} " + ("is past its due date" if stage == 2 else "is due soon")
                + f" ({show_time(r['due'], zone)}). "
                + (f"Still open before it: {', '.join('#' + str(x) for x in left_)}." if left_ else "Nothing waits before it."))
        _event(conn, r["id"], "river", "overdue" if stage == 2 else "due soon")
        for h in humans:
            _send(conn, "alert", "river", text, to=h)


def _question_nudges(conn):
    """A question unanswered after question_nudge_after whose receiver is away or gone: tell the asker
    once, so it can ask someone else (design 7.4). The receiver's own count says how long it waits."""
    for r in conn.execute("SELECT m.id, m.from_agent, m.to_agent, m.item_id, m.created_at, a.last_seen "
                          "FROM messages m JOIN agents a ON a.name=m.to_agent WHERE m.kind='question' "
                          "AND m.state='open' AND m.nudged_at IS NULL").fetchall():
        wait = parse_duration(setting(conn, "question_nudge_after", agent=r["to_agent"]))
        if parse_iso(r["created_at"]) + wait > now():
            continue
        state = _agent_state(conn, {"name": r["to_agent"], "last_seen": r["last_seen"]})
        if state == "active":
            continue
        conn.execute("UPDATE messages SET nudged_at=? WHERE id=?", (iso(now()), r["id"]))
        _send(conn, "notice", "river", f"{r['to_agent']} is {state} (last seen {r['last_seen'][:16].replace('T', ' ')} UTC) "
              f"and has not answered your question #{r['id']}. Ask someone else"
              + (f": river ask --holder-of {r['item_id']} \"...\", or river who" if r["item_id"] else ": river who"),
              to=r["from_agent"], item_id=r["item_id"], reply_to=r["id"])
    # Other messages (alerts, notes, offers) that a gone agent never read: tell the sender once.
    for r in conn.execute("SELECT m.id, m.kind, m.from_agent, m.to_agent, m.item_id, a.last_seen FROM messages m "
                          "JOIN agents a ON a.name=m.to_agent WHERE m.kind IN ('alert','note','offer') "
                          "AND m.read_at IS NULL AND m.nudged_at IS NULL AND m.from_agent<>'river'").fetchall():
        if _agent_state(conn, {"name": r["to_agent"], "last_seen": r["last_seen"]}) != "gone":
            continue
        conn.execute("UPDATE messages SET nudged_at=? WHERE id=?", (iso(now()), r["id"]))
        _send(conn, "notice", "river", f"{r['to_agent']} is gone (last seen {r['last_seen'][:16].replace('T', ' ')} UTC) "
              f"and never read your {r['kind']} #{r['id']}. Send it to someone else if it still matters (river who)",
              to=r["from_agent"], item_id=r["item_id"], reply_to=r["id"])


def activity(conn, actor):
    """Run at the start of every command: expire old leases, then renew the actor's own."""
    with tx(conn):
        expired = _sweep(conn)
        _touch_agent(conn, actor)
        sync_needs_you(conn)
    return expired


def session_url_from_env(env=None):
    """The web link of the Claude Code session this command runs in, when Remote Control gives it one."""
    sid = (env if env is not None else os.environ).get("CLAUDE_CODE_BRIDGE_SESSION_ID", "").strip()
    return f"https://claude.ai/code/{sid}" if re.match(r"^session_[A-Za-z0-9]+$", sid) else None


def record_session_url(conn, name, url):
    """Keep an agent's session link current, so a notification about its work opens that session."""
    if not (name and url):
        return
    with tx(conn):
        conn.execute("UPDATE agents SET session_url=? WHERE name=? AND kind='ai' AND session_url IS NOT ?",
                     (url, name, url))


def origin_session_url(conn, item_id=None, message_id=None):
    """The session link of the agent that asked (a message) or added the item (its first event), if known."""
    who = None
    if message_id is not None:
        r = conn.execute("SELECT from_agent FROM messages WHERE id=?", (message_id,)).fetchone()
        who = r and r["from_agent"]
    elif item_id is not None:
        r = conn.execute("SELECT actor FROM events WHERE item_id=? ORDER BY id LIMIT 1", (item_id,)).fetchone()
        who = r and r["actor"]
    if not who:
        return None
    r = conn.execute("SELECT session_url FROM agents WHERE name=?", (who,)).fetchone()
    return r and r["session_url"]


# ---------------------------------------------------------------- needs you

NOTIFY_MAX_ATTEMPTS = 5


def _channels(value):
    return [c.strip() for c in (value or "").split(",") if c.strip()]


def _open_event(conn, kind, summary, item_id=None, message_id=None, human=None):
    t = iso(now())
    eid = conn.execute("INSERT INTO needs_you(kind,item_id,message_id,human,summary,opened_at) VALUES (?,?,?,?,?,?)",
                       (kind, item_id, message_id, human, summary, t)).lastrowid
    for ch in _channels(setting(conn, "notify_channels", item_id=item_id, agent=human)):
        conn.execute("INSERT OR IGNORE INTO notifications(event_id,channel,created_at) VALUES (?,?,?)", (eid, ch, t))
    if item_id is not None:
        _event(conn, item_id, "river", f"needs you: {summary}")
    return eid


def sync_needs_you(conn):
    """Open and close needs-you events so they match the queue. Safe to run any number of times.

    Runs inside the caller's transaction. Opens an event for each ready human
    item and each open question or unread alert to a human, and closes events
    whose reason is gone (done, dropped, claimed, blocked again, answered, read).
    """
    humans = {r["name"] for r in conn.execute("SELECT name FROM agents WHERE kind='human'")}
    ann = annotate(conn)
    want_items = {a["id"]: a for a in ann.values()
                  if a["ready"] and a["doer"] == "human" and not a["project_archived"]}
    want_msgs = {}
    if humans:
        for r in conn.execute(
                f"SELECT * FROM messages WHERE kind IN ('question','alert') AND to_agent IN ({','.join('?' * len(humans))}) "
                "AND ((kind='question' AND state='open') OR (kind='alert' AND read_at IS NULL))", sorted(humans)):
            want_msgs[r["id"]] = r
    t = iso(now())
    for ev in conn.execute("SELECT * FROM needs_you WHERE closed_at IS NULL").fetchall():
        if ev["kind"] == "item" and ev["item_id"] not in want_items:
            it = ann.get(ev["item_id"])
            why = (it["status"] if it and it["status"] in CLOSED_STATES
                   else "claimed" if it and it["status"] in ("in_progress", "held")
                   else "no longer ready")
            conn.execute("UPDATE needs_you SET closed_at=?, close_reason=? WHERE id=?", (t, why, ev["id"]))
        elif ev["kind"] == "message" and ev["message_id"] not in want_msgs:
            m = conn.execute("SELECT kind, state FROM messages WHERE id=?", (ev["message_id"],)).fetchone()
            why = m["state"] if m and m["state"] != "open" else "read"
            conn.execute("UPDATE needs_you SET closed_at=?, close_reason=? WHERE id=?", (t, why, ev["id"]))
    have_items = {r[0] for r in conn.execute("SELECT item_id FROM needs_you WHERE kind='item' AND closed_at IS NULL")}
    have_msgs = {r[0] for r in conn.execute("SELECT message_id FROM needs_you WHERE kind='message' AND closed_at IS NULL")}
    for iid in sorted(set(want_items) - have_items):
        a = want_items[iid]
        _open_event(conn, "item", f"#{iid} {a['title']} ({a['project']}) is ready for a person", item_id=iid)
    for mid in sorted(set(want_msgs) - have_msgs):
        m = want_msgs[mid]
        _open_event(conn, "message", f"{m['kind']} from {m['from_agent']}: {m['body'][:120]}",
                    item_id=m["item_id"], message_id=mid, human=m["to_agent"])


def needs_you(conn, human=None, include_closed=False, limit=100):
    """Needs-you events for one person (theirs and the ones for anyone), most important first.

    Open events come before closed ones. Among open events, questions and alerts come
    first, oldest first, because an agent waits on the answer now; then items in
    queue order (effective priority, project rank, what they unblock). Closed
    events follow, newest first. Each item event carries its effective priority,
    where that priority comes from, and how many open items it unblocks."""
    with tx(conn):
        sync_needs_you(conn)
    sql = ("SELECT n.*, i.title item_title, p.name project, m.kind message_kind, m.from_agent, m.body "
           "FROM needs_you n LEFT JOIN items i ON i.id=n.item_id LEFT JOIN projects p ON p.id=i.project_id "
           "LEFT JOIN messages m ON m.id=n.message_id WHERE 1=1")
    args = []
    if not include_closed:
        sql += " AND n.closed_at IS NULL"
    if human is not None:
        sql += " AND (n.human IS NULL OR n.human=?)"
        args.append(human)
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY n.id DESC", args)]
    ann = annotate(conn)
    for r in rows:
        a = ann.get(r["item_id"]) if r["kind"] == "item" else None
        r["priority"] = a["effective_priority"] if a else None
        r["priority_from"] = a["priority_from"] if a else None
        r["unblocks_count"] = a["unblocks_count"] if a else 0
        if r["closed_at"]:
            r["_key"] = (3, -r["id"])
        elif a:
            r["_key"] = (1,) + a["sort_key"]
        elif r["kind"] == "message":
            r["_key"] = (0, r["id"])
        else:
            r["_key"] = (2, -r["id"])  # an item the queue no longer knows
    rows.sort(key=lambda r: r["_key"])
    rows = rows[:limit]
    for r in rows:
        del r["_key"]
        r["notifications"] = [dict(x) for x in conn.execute(
            "SELECT channel, state, attempts, last_error, sent_at FROM notifications WHERE event_id=? ORDER BY channel",
            (r["id"],))]
    return rows


def outbox(conn, channel=None):
    """Notifications still to send: pending, or failed with attempts left. For the dispatcher."""
    sql = ("SELECT o.*, n.summary, n.kind, n.item_id, n.message_id, n.human "
           "FROM notifications o JOIN needs_you n ON n.id=o.event_id "
           "WHERE (o.state='pending' OR (o.state='failed' AND o.attempts < ?)) "
           "AND n.closed_at IS NULL")  # a person already acted: do not notify about it
    args = [NOTIFY_MAX_ATTEMPTS]
    if channel:
        sql += " AND o.channel=?"
        args.append(channel)
    return [dict(r) for r in conn.execute(sql + " ORDER BY o.id", args)]


def outbox_mark(conn, notification_id, ok, error=None):
    """Record one send attempt: sent, or failed with the error (it retries until NOTIFY_MAX_ATTEMPTS)."""
    with tx(conn):
        if not conn.execute("SELECT 1 FROM notifications WHERE id=?", (notification_id,)).fetchone():
            raise RiverError(f"no notification {notification_id}")
        if ok:
            conn.execute("UPDATE notifications SET state='sent', attempts=attempts+1, sent_at=?, last_error=NULL "
                         "WHERE id=?", (iso(now()), notification_id))
        else:
            conn.execute("UPDATE notifications SET state='failed', attempts=attempts+1, last_error=? WHERE id=?",
                         (str(error or "unknown error")[:500], notification_id))
    return dict(conn.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)).fetchone())


def register(conn, name, human=False, note="", session=None):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$", name):
        raise RiverError("agent names use letters, digits, '.', '_', '-' (up to 64)")
    t = iso(now())
    with tx(conn):
        conn.execute(
            "INSERT INTO agents(name,kind,note,registered_at,last_seen) VALUES (?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, note=excluded.note, last_seen=excluded.last_seen",
            (name, "human" if human else "ai", note, t, t))
        _event(conn, None, name, f"registered as {'human' if human else 'ai'}")
    if session:
        set_session(conn, name, session)
    return agent_status(conn, name)


def set_session(conn, name, session, ref=None):
    """Record the Claude Code session an agent runs in, so people and agents can message that session.

    Session names can repeat, so keep the short ref too: ListAgents prints 'name [ref]', and either
    form is accepted here ('name [ref]' in one string, or name plus ref)."""
    m = re.match(r"^\s*(\S+)\s*(?:\[\s*([0-9A-Za-z]+)\s*\])?\s*$", session or "")
    if not m or len(m.group(1)) > 128:
        raise RiverError("a session name is one word of up to 128 characters, optionally with its ref: "
                         "river session <name> --ref <ref>  (ListAgents prints 'This session is <name> [<ref>]')")
    session, ref = m.group(1), (ref or m.group(2) or "").strip("[] ") or None
    if ref and not re.match(r"^[0-9A-Za-z]{1,32}$", ref):
        raise RiverError("a session ref is the short code in brackets that ListAgents prints, such as b5e2e0")
    with tx(conn):
        old = _agent(conn, name)
        if (old["session"], old["session_ref"]) != (session, ref):
            conn.execute("UPDATE agents SET session=?, session_ref=? WHERE name=?", (session, ref, name))
            _event(conn, None, name, f"session: {session}" + (f" [{ref}]" if ref else ""))
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
        _drop_queue(conn, name, "stopped")
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
    if "stop_at" in a.keys() and a["stop_at"]:
        return "stopped"
    # Its process on this host has ended: gone at once, not after gone_after.
    if "pid" in a.keys() and a["pid"] and a["host"] == this_host() and not pid_alive(a["pid"]):
        return "gone"
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
        "SELECT id, title, status, lease_expires_at, hold_expires_at FROM items WHERE assignee=? "
        "AND status IN ('in_progress','held') ORDER BY id",
        (name,))]
    a["owns"] = [dict(r) for r in conn.execute(
        "SELECT name, owner_expires_at FROM targets WHERE owner=? ORDER BY name", (name,))]
    return a


def _rel_path(path, root, cwd=None):
    """A path as the touches of a project in `root` write it: relative, no './'. A relative path is
    taken from `cwd` when that lies inside the project, else as already relative to the project."""
    p = os.path.expanduser(path)
    r = os.path.realpath(os.path.expanduser(root)) if root else None
    inside = lambda q: q == r or q.startswith(r.rstrip(os.sep) + os.sep)
    if not os.path.isabs(p) and cwd and r and inside(os.path.realpath(os.path.join(cwd, p))):
        p = os.path.join(cwd, p)
    if os.path.isabs(p):
        p = os.path.realpath(p)
        if not r or not inside(p):
            return None
        p = os.path.relpath(p, r)
    p = os.path.normpath(p)
    return "" if p == "." else p


def who(conn, item=None, project=None, file=None, cwd=None):
    """Every agent and what it holds; or only the holder of an item, the agents in a project, or the
    agents whose held items touch a file or directory (each with the matching `touching` paths)."""
    agents = [agent_status(conn, r["name"]) for r in conn.execute("SELECT name FROM agents ORDER BY name")]
    if file is not None:
        rows = conn.execute("SELECT i.id, i.title, i.assignee, i.touches, p.path FROM items i "
                            "JOIN projects p ON p.id=i.project_id "
                            "WHERE i.status IN ('in_progress','held') AND i.touches<>''").fetchall()
        by = {}
        for r in rows:
            rel = _rel_path(file, r["path"], cwd)
            if rel is None:
                continue
            hit = [t for t in touches_list(r["touches"]) if rel == "" or _paths_overlap(rel, t)]
            if hit:
                by.setdefault(r["assignee"], []).append({"id": r["id"], "title": r["title"], "paths": hit})
        for a in agents:
            a["touching"] = by.get(a["name"], [])
        return [a for a in agents if a["touching"]]
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
    if ag["stop_at"]:
        raise RiverError(f"refused: {actor} is asked to stop (by {ag['stop_by']}: {ag['stop_reason']}); it takes no "
                         f"new work. Commit finished work, release your item, then end the session")
    if ag["role"] == "planner":
        raise RiverError(f"refused: {actor} is a planner session; planners change the plan and do not take work. "
                         f"To work instead, run: river --as {actor} go")
    it0 = _item(conn, item_id)
    if it0["doer"] == "human" and ag["kind"] == "ai":
        raise RiverError(f"refused: #{item_id} is for a person. If you can do it or work around it, take it over "
                         f"(the user is told, and can undo it): river takeover {item_id} --note \"<how you will do it>\"")
    if it0["reserved_for"] and it0["reserved_for"] != actor:
        raise RiverError(f"refused: #{item_id} is reserved for {it0['reserved_for']}, who holds the item it unblocks")
    q = conn.execute("SELECT agent FROM queue_entries WHERE item_id=?", (item_id,)).fetchone()
    if q and q["agent"] != actor and ag["kind"] == "ai":
        raise RiverError(f"refused: #{item_id} is in the queue of {q['agent']} (river queue list {q['agent']})")
    # A goal's items are its owner's, unless one was pushed or given to this agent.
    if ag["kind"] == "ai" and it0["doer"] != "human" and it0["reserved_for"] != actor:
        g = conn.execute("SELECT g.name, g.owner FROM item_goals ig JOIN goals g ON g.id=ig.goal_id WHERE ig.item_id=? "
                         "AND g.status='open' AND g.owner IS NOT NULL AND g.owner<>? AND g.owner_expires_at >= ? "
                         "ORDER BY g.rank LIMIT 1", (item_id, actor, iso(now()))).fetchone()
        if g:
            raise RiverError(f"refused: #{item_id} is reserved for {g['owner']}, who owns goal {g['name']}. "
                             f"Offer help: river offer \"<what you can take>\" --goal {g['name']}")
    # A hold does not use up a lease when the agent takes one of its own reserved prerequisites.
    held, limit, key = _lease_room(conn, item_id, actor, ("in_progress",) if it0["reserved_for"] == actor
                                   else ("in_progress", "held"))
    if held >= limit:
        what = "items of its own goals and targets" if key == "goal_max_leases" else "item(s)"
        raise RiverError(f"refused: {actor} already holds {held} {what} ({key} {limit}). "
                         f"Finish one (river done <id>), release one (river release <id>), "
                         f"or raise the limit (river config set {key} <n> --agent {actor})")
    why = _model_refusal(conn, it0, actor)
    if why:
        raise RiverError(f"refused: #{item_id} {why}. Take other work (river go), or a session with an allowed "
                         f"model takes it; a planner can change the limits: river edit {item_id} --min-model/--max-model")
    it = _item(conn, item_id)
    if it["kind"] == "deploy":
        owner = conn.execute("SELECT owner FROM targets WHERE name=?", (it["target"],)).fetchone()
        if not owner or owner["owner"] != actor:
            raise RiverError(f"refused: #{item_id} deploys to target {it['target']}, and only its owner can take it "
                             f"(owner: {owner['owner'] if owner and owner['owner'] else 'nobody'}). "
                             f"Become the owner first: river target own {it['target']}")
    ttl = _lease_for(conn, item_id, actor)
    t = now()
    cur = conn.execute(
        "UPDATE items SET status='in_progress', assignee=?, claimed_at=?, lease_expires_at=? "
        "WHERE id=? AND status='open'", (actor, iso(t), iso(t + ttl), item_id))
    if cur.rowcount != 1:
        raise RiverError(f"item {item_id} is no longer open")
    if it0["reserved_until"]:
        conn.execute("UPDATE items SET reserved_until=NULL WHERE id=?", (item_id,))
        conn.execute("UPDATE messages SET state='accepted', read_at=COALESCE(read_at, ?) "
                     "WHERE kind='alert' AND item_id=? AND to_agent=? AND state='open'", (iso(t), item_id, actor))
    _event(conn, item_id, actor, f"claimed (lease {_short(ttl)})")
    if conn.execute("DELETE FROM queue_entries WHERE item_id=?", (item_id,)).rowcount:
        _event(conn, item_id, actor, "left the queue (claimed)")
    _goal_notice(conn, item_id, actor, "claimed")
    if it["kind"] == "deploy":
        _add_monitor(conn, it, actor)
    return ag


def _model_refusal(conn, it, actor):
    """Why the actor's model may not take this item (its min/max limits), or None."""
    model = agent_model(conn, actor)
    if not model:
        return None
    ladder_text = setting(conn, "model_ladder")
    m = _item_models(it, _project_name(conn, it["project_id"]), _model_defaults(conn), ladder_text)
    ok, why = model_check(parse_ladder(ladder_text), model, m["min_model"], m["max_model"])
    return None if ok else why


def next_item(conn, project=None, unblocks=None, claim=False, actor=None, limit=1, near=None, mine=False,
              model=None, skipped=None):
    """Show, or claim, the first ready item from the area the agent chose.

    model: the session's model (default: the one the agent recorded). Items whose min/max limits exclude it
    are left out; `skipped`, a list, receives them with the reason."""
    model = model or agent_model(conn, actor)
    ladder = parse_ladder(setting(conn, "model_ladder")) if model else None
    doer_for = None
    if actor:
        r = conn.execute("SELECT kind FROM agents WHERE name=?", (actor,)).fetchone()
        doer_for = r["kind"] if r else None
    if mine and not actor:
        raise RiverError("--mine needs an agent name: set RIVER_AGENT or pass --as <name>")
    who_mine = actor if mine else None

    def fits(pool):
        if not model:
            return pool
        out = []
        for a in pool:
            ok, why = model_check(ladder, model, a["min_model"], a["max_model"])
            if ok:
                a["model_note"] = why
                out.append(a)
            elif skipped is not None and a["id"] not in {x["id"] for x in skipped}:
                skipped.append({"id": a["id"], "title": a["title"], "why": why})
        return out

    def takeable(pool):
        if not actor:
            return fits(pool)
        owned = {r["name"] for r in conn.execute("SELECT name FROM targets WHERE owner=?", (actor,))}
        pool = [a for a in pool if (a["kind"] != "deploy" or a["target"] in owned)
                and a["reserved_for"] in (None, actor)]
        # The agent's own queue comes first, in its order and from any project; then items pushed to it.
        # The sort is stable, so graph order holds inside each group.
        qpos = {r["item_id"]: n for n, r in enumerate(conn.execute(
            "SELECT item_id FROM queue_entries WHERE agent=? AND item_id IS NOT NULL ORDER BY pos", (actor,)))}
        have = {a["id"] for a in pool}
        pool = [a for a in _queue_ready(conn, actor) if a["id"] not in have] + pool
        pool = fits(pool)
        return sorted(pool, key=lambda a: (a["id"] not in qpos, qpos.get(a["id"], 0),
                                           not (a["reserved_for"] == actor and a["reserved_until"])))

    if not claim:
        return [{k: v for k, v in a.items() if k != 'sort_key'}
                for a in takeable(ready_list(conn, project, unblocks, doer_for, near=near, mine=who_mine))[:limit]]
    with tx(conn):
        _sweep(conn)
        pool = takeable(ready_list(conn, project, unblocks, doer_for, near=near, mine=who_mine))
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
                   else a["blocked_text"] if a["blocked_reason"]
                   else f"conflicts with {', '.join('#' + str(b) for b in a['busy_conflicts'])}, which is in progress "
                        f"(both edit the same files)" if a["busy_conflicts"] and a["status"] == "open"
                   else f"status is {a['status']}" + (f" (held by {a['assignee']})" if a["assignee"] else ""))
            raise RiverError(f"item {item_id} is not ready: {why}. See: river blockers {item_id}")
        _claim_row(conn, a["id"], actor)
    return item_show(conn, item_id)


def _close(conn, item_id, status, actor, output=None, note=None, force=None):
    with tx(conn):
        it = _item(conn, item_id)
        if it["status"] in CLOSED_STATES:
            raise RiverError(f"item {item_id} is already {it['status']}")
        # Done means everything it waits on is done: a deploy never goes out past an open review or
        # an unfinished item it ships. Other items can be closed anyway with a reason (--force).
        waits = _open_prereqs(conn, it["id"]) if status == "done" else []
        if waits:
            ids = ", ".join(f"#{x}" for x in waits)
            if it["kind"] == "deploy":
                raise RiverError(f"refused: deploy #{item_id} still waits on {ids} (river blockers {item_id}); "
                                 f"finish or drop those first, then deploy")
            if not (force or "").strip():
                raise RiverError(f"refused: #{item_id} still waits on {ids} (river blockers {item_id}). Finish those "
                                 f"first, or close it anyway with a reason: river done {item_id} --force \"<why>\"")
            _event(conn, it["id"], actor, f"done while {ids} still open: {force.strip()}")
        if it["doer"] == "human" and _is_ai(conn, actor):
            if not (note or "").strip():
                raise RiverError(f"#{item_id} is for a person; to mark it {status} as an agent, say why the user "
                                 f"no longer needs to do it: river {'done' if status == 'done' else 'drop'} {item_id} "
                                 f"--note \"<why>\"   (the user is told, and can undo it)")
            _record_takeover(conn, it, "done" if status == "done" else "dropped", note, actor)
        if it["assignee"] and actor and it["assignee"] != actor:
            raise RiverError(f"item {item_id} is held by {it['assignee']}; ask them, or release it first")
        conn.execute("UPDATE items SET status=?, closed_at=?, lease_expires_at=NULL, hold_expires_at=NULL, "
                     "reserved_for=NULL, needs_check=0, output=COALESCE(?, output) WHERE id=?",
                     (status, iso(now()), output, it["id"]))
        _event(conn, it["id"], actor, status + (f": {output}" if output else ""))
        _goal_notice(conn, it["id"], actor, "finished" if status == "done" else status, output)
        newly, resumed = [], []
        if status in CLOSED_STATES:
            resumed = _resume_holds(conn, it["id"])
            ann = annotate(conn)
            newly = [d for d in ann[it["id"]]["unblocks"] if ann[d]["ready"]]
            # Tell whoever holds an item that waits on this one (design 7.4: "a prerequisite you waited on is done").
            for d in ann[it["id"]]["unblocks"]:
                a = ann[d]
                if a["assignee"] and a["status"] in ("in_progress", "held") and a["assignee"] != actor:
                    left_ = a["open_blockers"]
                    _send(conn, "notice", "river", f"#{it['id']} {it['title']} is {status}"
                          + (f" (output: {output})" if output else "") + f"; your #{d} waits on it. "
                          + (f"Still open before #{d}: " + ", ".join(f"#{x}" for x in left_) if left_
                             else (f"Nothing is left before #{d}; it is in progress again" if d in resumed
                                   else f"Nothing is left before #{d}")),
                          to=a["assignee"], item_id=d)
    res = item_show(conn, item_id)
    res["now_ready"] = newly
    res["resumed"] = resumed
    return res


def synced(conn, item_id, ref=None, actor=None):
    """Record that the tracker issues of a closed item got the result (comment, close). One ref, or all."""
    with tx(conn):
        it = _item(conn, item_id)
        if it["status"] not in CLOSED_STATES:
            raise RiverError(f"#{item_id} is {it['status']}; the write-back comes after river done {item_id}")
        rows = conn.execute("SELECT ref FROM item_refs WHERE item_id=?" + (" AND ref=?" if ref else ""),
                            (it["id"], ref.strip()) if ref else (it["id"],)).fetchall()
        if not rows:
            raise RiverError(f"#{item_id} has no tracker link" + (f" {ref}" if ref else "")
                             + f"; its links: river show {item_id}")
        for r in rows:
            if conn.execute("UPDATE item_refs SET synced_at=? WHERE item_id=? AND ref=? AND synced_at IS NULL",
                            (iso(now()), it["id"], r["ref"])).rowcount:
                _event(conn, it["id"], actor, f"tracker updated: {r['ref']}")
    return item_show(conn, item_id)


def unsynced(conn, actor=None):
    """Closed items with tracker links not yet updated; with actor, only the items that agent closed."""
    rows = conn.execute(
        "SELECT DISTINCT i.id FROM items i JOIN item_refs r ON r.item_id=i.id WHERE r.synced_at IS NULL "
        f"AND i.status IN {CLOSED_STATES}" + (" AND i.assignee=?" if actor else "") + " ORDER BY i.closed_at",
        (actor,) if actor else ()).fetchall()
    ann = annotate(conn) if rows else {}
    return [{"id": r["id"], "title": ann[r["id"]]["title"], "status": ann[r["id"]]["status"],
             "project": ann[r["id"]]["project"], "output": ann[r["id"]]["output"],
             "tracker": setting(conn, "tracker", item_id=r["id"]),
             "refs": [x for x in ann[r["id"]]["refs"] if not x["synced_at"]]} for r in rows]


def _approved_fixes(conn, it, actor):
    """A person approved the fixes a review proposed (an item of kind fixes is done): add each '- ' line
    of its notes as a fix item that the review it blocks waits on."""
    titles = [l[2:].strip() for l in (it["notes"] or "").splitlines() if l.startswith("- ") and l[2:].strip()]
    reviews = [r["item_id"] for r in conn.execute(
        "SELECT d.item_id FROM deps d JOIN items i ON i.id=d.item_id WHERE d.blocked_by=? AND i.kind='review' "
        f"AND i.status IN {OPEN_STATES}", (it["id"],))]
    if not titles or not reviews:
        return []
    project = _project_name(conn, it["project_id"])
    added = [item_add(conn, project, t, priority=it["priority"], doer="ai", actor=actor,
                      context=f"Approved in #{it['id']} ({it['title']}): {it['context']}") for t in titles]
    for r in reviews:
        dep_add(conn, r, [a["id"] for a in added], actor)
    return [{"id": a["id"], "title": a["title"]} for a in added]


def done(conn, item_id, output=None, actor=None, ship_it=False, note=None, synced_=False, force=None):
    """Close an item as done. Refused while an item it waits on is open; force (a reason) closes a
    non-deploy item anyway and records the reason."""
    res = _close(conn, item_id, "done", actor, output, note, force)
    if res["kind"] == "fixes":
        res["fixes_added"] = _approved_fixes(conn, _item(conn, item_id), actor)
    if synced_ and res["refs"]:
        res = dict(synced(conn, item_id, actor=actor), now_ready=res["now_ready"], resumed=res["resumed"])
    res["tracker"] = setting(conn, "tracker", item_id=item_id) if res["refs"] else ""
    if ship_it:
        res["shipped_in"] = ship(conn, item_id, actor)["id"]
    return res


def ship(conn, item_id, actor=None):
    """Ask for the item to go out: add it to its target's open deploy item, or start one.

    The deploy item waits on everything it ships, sits in the project
    deploy-<target>, and only the target's owner can take it.
    """
    with tx(conn):
        it = _item(conn, item_id)
        if it["kind"] == "deploy":
            raise RiverError(f"#{item_id} is itself a deploy item")
        if it["status"] == "dropped":
            raise RiverError(f"#{item_id} is dropped; there is nothing to ship")
        proj = conn.execute("SELECT name, target FROM projects WHERE id=?", (it["project_id"],)).fetchone()
        if not proj["target"]:
            raise RiverError(f"project {proj['name']} has no deploy target; set one: "
                             f"river project target {proj['name']} <target>  (river target list)")
        tg = _target(conn, proj["target"])
        dep = conn.execute("SELECT * FROM items WHERE kind='deploy' AND target=? AND status='open' ORDER BY id LIMIT 1",
                           (tg["name"],)).fetchone()
        if dep is None:
            pname = f"deploy-{tg['name']}"
            if not conn.execute("SELECT 1 FROM projects WHERE name=?", (pname,)).fetchone():
                top = conn.execute("SELECT COALESCE(MAX(rank),0) m FROM projects WHERE archived=0").fetchone()["m"]
                conn.execute("INSERT INTO projects(name,rank,notes,target,created_at) VALUES (?,?,?,?,?)",
                             (pname, top + 1, f"Deploys to target {tg['name']}, made by river ship. "
                              f"Only the target owner takes these items.", tg["name"], iso(now())))
                _event(conn, None, actor, f"project {pname} added for target {tg['name']}")
            pid = conn.execute("SELECT id FROM projects WHERE name=?", (pname,)).fetchone()["id"]
            top = conn.execute("SELECT COALESCE(MAX(rank),0) m FROM items WHERE project_id=?", (pid,)).fetchone()["m"]
            cur = conn.execute(
                "INSERT INTO items(project_id,title,notes,priority,rank,doer,context,kind,target,created_at) "
                "VALUES (?,?,?,?,?,'any',?,'deploy',?,?)",
                (pid, f"Deploy {tg['name']}", "Ship every item this waits on; put the release id in the output.",
                 it["priority"], top + 1, tg["description"], tg["name"], iso(now())))
            dep = _item(conn, cur.lastrowid)
            _event(conn, dep["id"], actor, f"deploy item for target {tg['name']} started")
        rv = _release_review(conn, dep, tg, actor)
        if rv is not None and not conn.execute("SELECT 1 FROM deps WHERE item_id=? AND blocked_by=?",
                                               (rv["id"], it["id"])).fetchone():
            _dep_add(conn, rv["id"], it["id"], actor)
        if conn.execute("SELECT 1 FROM deps WHERE item_id=? AND blocked_by=?", (dep["id"], it["id"])).fetchone():
            return item_show(conn, dep["id"])
        _dep_add(conn, dep["id"], it["id"], actor, alert=False)  # the ship request notice below says it
        if it["priority"] < dep["priority"]:
            conn.execute("UPDATE items SET priority=? WHERE id=?", (it["priority"], dep["id"]))
        _event(conn, it["id"], actor, f"ship requested in #{dep['id']} ({tg['name']})")
        if tg["owner"] and tg["owner"] != actor:
            _send(conn, "notice", actor or "river", f"ship request: #{it['id']} {it['title']} joins deploy #{dep['id']} "
                  f"for {tg['name']}", to=tg["owner"], item_id=dep["id"])
    return item_show(conn, dep["id"])


def _release_review(conn, dep, tg, actor, force=False):
    """With review on, the open review item the deploy item waits on; started when there is none.

    A new review waits on the release's items that no finished review of this deploy covered."""
    if not force and setting(conn, "review", item_id=dep["id"]) != "on":
        return None
    rv = conn.execute("SELECT i.* FROM items i JOIN deps d ON d.blocked_by=i.id WHERE d.item_id=? AND i.kind='review' "
                      f"AND i.status IN {OPEN_STATES} ORDER BY i.id LIMIT 1", (dep["id"],)).fetchone()
    if rv is not None:
        return rv
    top = conn.execute("SELECT COALESCE(MAX(rank),0) m FROM items WHERE project_id=?", (dep["project_id"],)).fetchone()["m"]
    cur = conn.execute(
        "INSERT INTO items(project_id,title,notes,priority,rank,doer,context,\"check\",kind,target,created_at) "
        "VALUES (?,?,?,?,?,'any',?,?,'review',?,?)",
        (dep["project_id"], f"Review release {tg['name']}",
         "Review everything this release ships before it deploys. Pass: river review pass <id>; "
         "problems: river review fail <id> \"<fix>\" ... (fix items the review waits on).",
         dep["priority"], top + 0.5, setting(conn, "review_prompt", item_id=dep["id"]),
         setting(conn, "review_cmd", item_id=dep["id"]), tg["name"], iso(now())))
    rv = _item(conn, cur.lastrowid)
    _event(conn, rv["id"], actor, f"review for deploy #{dep['id']} ({tg['name']}) started")
    # Every shipped item that no finished review of this deploy covered (done ones too: Review and deploy
    # can start after the work is finished).
    for r in conn.execute("SELECT i.id FROM items i JOIN deps d ON d.blocked_by=i.id WHERE d.item_id=? "
                          "AND i.kind<>'review' AND i.status<>'dropped' AND i.id NOT IN ("
                          "  SELECT d2.blocked_by FROM deps d2 JOIN items r ON r.id=d2.item_id JOIN deps d3 ON d3.blocked_by=r.id "
                          "  WHERE d3.item_id=? AND r.kind='review' AND r.status='done')",
                          (dep["id"], dep["id"])).fetchall():
        _dep_add(conn, rv["id"], r["id"], actor)
    _dep_add(conn, dep["id"], rv["id"], actor)
    return rv


def _review_item(conn, item_id, actor):
    it = _item(conn, item_id)
    if it["kind"] != "review":
        raise RiverError(f"#{item_id} is not a review item; reviews come with river ship when the setting review is on")
    if it["status"] != "in_progress" or (actor and it["assignee"] != actor):
        raise RiverError(f"#{item_id} is {it['status']}" + (f" (held by {it['assignee']})" if it["assignee"] else "")
                         + f"; take it first: river claim {item_id}")
    return it


# ---------------------------------------------------------------- review steps

def _review_step(conn, step_id):
    r = conn.execute("SELECT s.*, p.name project FROM review_steps s JOIN projects p ON p.id=s.project_id WHERE s.id=?",
                     (int(step_id),)).fetchone()
    if not r:
        raise RiverError(f"no review step {step_id}; list them: river review step list [<project>]")
    return r


def _renumber_steps(conn, project_id):
    ids = [r["id"] for r in conn.execute("SELECT id FROM review_steps WHERE project_id=? ORDER BY pos, id", (project_id,))]
    for n, sid in enumerate(ids, 1):
        conn.execute("UPDATE review_steps SET pos=? WHERE id=?", (n, sid))


def review_steps(conn, project=None):
    """The review steps of one project, or of every project, in order."""
    q = ("SELECT s.id, s.pos, s.kind, s.text, p.name project FROM review_steps s JOIN projects p ON p.id=s.project_id "
         + ("WHERE p.name=? " if project else "WHERE p.archived=0 ") + "ORDER BY p.rank, p.id, s.pos, s.id")
    if project:
        _project(conn, project)
    return [dict(r) for r in conn.execute(q, (project,) if project else ())]


def review_step_add(conn, project, text, run=False, at=None, actor=None):
    """Add a review step to a project: a written instruction, or with run a command that must exit 0."""
    p = _project(conn, project)
    text = (text or "").strip()
    if not text:
        raise RiverError("say what the step is: river review step add <project> \"<instruction>\" (or --run \"<command>\")")
    with tx(conn):
        n = conn.execute("SELECT COUNT(*) FROM review_steps WHERE project_id=?", (p["id"],)).fetchone()[0]
        pos = n + 1 if at is None else max(1, min(int(at), n + 1))
        conn.execute("UPDATE review_steps SET pos=pos+1 WHERE project_id=? AND pos>=?", (p["id"], pos))
        cur = conn.execute("INSERT INTO review_steps(project_id,pos,kind,text,created_at) VALUES (?,?,?,?,?)",
                           (p["id"], pos, "run" if run else "do", text, iso(now())))
        _event(conn, None, actor, f"review step {pos} added to {project}: {text}")
    return dict(_review_step(conn, cur.lastrowid))


def review_step_edit(conn, step_id, text=None, run=None, actor=None):
    """Change a step's text, or make it a command (run=True) or a written instruction (run=False)."""
    st = _review_step(conn, step_id)
    if text is None and run is None:
        raise RiverError(f"say what changes: river review step edit {step_id} --text \"...\" and/or --run / --do")
    if text is not None and not text.strip():
        raise RiverError("the step text cannot be empty; to remove it: river review step rm " + str(step_id))
    with tx(conn):
        conn.execute("UPDATE review_steps SET text=?, kind=? WHERE id=?",
                     (text.strip() if text is not None else st["text"],
                      st["kind"] if run is None else ("run" if run else "do"), st["id"]))
        _event(conn, None, actor, f"review step {st['pos']} of {st['project']} changed")
    return dict(_review_step(conn, st["id"]))


def review_step_remove(conn, step_id, actor=None):
    st = _review_step(conn, step_id)
    with tx(conn):
        conn.execute("DELETE FROM review_steps WHERE id=?", (st["id"],))
        _renumber_steps(conn, st["project_id"])
        _event(conn, None, actor, f"review step {st['pos']} removed from {st['project']}: {st['text']}")
    return review_steps(conn, st["project"])


def review_step_move(conn, step_id, to, actor=None):
    """Move a step to position to (1 is first) within its project."""
    st = _review_step(conn, step_id)
    with tx(conn):
        n = conn.execute("SELECT COUNT(*) FROM review_steps WHERE project_id=?", (st["project_id"],)).fetchone()[0]
        to = max(1, min(int(to), n))
        ids = [r["id"] for r in conn.execute("SELECT id FROM review_steps WHERE project_id=? AND id<>? ORDER BY pos, id",
                                             (st["project_id"], st["id"]))]
        ids.insert(to - 1, st["id"])
        for k, sid in enumerate(ids, 1):
            conn.execute("UPDATE review_steps SET pos=? WHERE id=?", (k, sid))
        _event(conn, None, actor, f"review step of {st['project']} moved from {st['pos']} to {to}")
    return review_steps(conn, st["project"])


def release_review_steps(conn, item_id):
    """The review steps a release review follows: of each project whose items it covers, by project rank."""
    rows = conn.execute("SELECT DISTINCT p.id, p.name, p.path, p.rank FROM deps d JOIN items i ON i.id=d.blocked_by "
                        "JOIN projects p ON p.id=i.project_id WHERE d.item_id=? AND i.kind<>'review' "
                        "ORDER BY p.rank, p.id", (int(item_id),)).fetchall()
    out = []
    for p in rows:
        steps = [dict(r) for r in conn.execute("SELECT id, pos, kind, text FROM review_steps WHERE project_id=? "
                                               "ORDER BY pos, id", (p["id"],))]
        if steps:
            out.append({"project": p["name"], "path": p["path"], "steps": steps})
    return out


def review_pass(conn, item_id, output=None, actor=None, cwd=None, runner=None, confirm=None):
    """Accept a release review.

    Every written step of the projects the release ships must be confirmed (confirm: "all", or the
    step ids), and every command step must exit 0 in its project folder; so must the item's review
    command (in cwd). Each step's result goes into the item's history."""
    it = _review_item(conn, item_id, actor)
    groups = release_review_steps(conn, item_id)
    todo = [(g, s) for g in groups for s in g["steps"] if s["kind"] == "do"]
    if isinstance(confirm, str):
        confirm = [confirm]
    confirm = [str(x).strip() for c in (confirm or []) for x in str(c).split(",") if str(x).strip()]
    if not all(x == "all" or x.isdigit() for x in confirm):
        raise RiverError("--confirm takes all, or the ids of the written steps you did (e.g. --confirm 3,5)")
    everything = "all" in confirm
    missing = [(g, s) for g, s in todo if not everything and str(s["id"]) not in confirm]
    if missing:
        raise RiverError("confirm each written review step after you did it:\n"
                         + "\n".join(f"  step {s['id']} ({g['project']} {s['pos']}): {s['text']}" for g, s in missing)
                         + f"\nThen: river review pass {item_id} --confirm all   (or --confirm "
                         + ",".join(str(s["id"]) for _, s in missing) + ")")
    import subprocess
    run = runner or (lambda c, d: subprocess.run(c, shell=True, cwd=d, capture_output=True, text=True, timeout=3600))

    def must_pass(cmd, where, what):
        r = run(cmd, where)
        if r.returncode != 0:
            tail = "\n".join(((r.stdout or "") + (r.stderr or "")).strip().splitlines()[-15:])
            raise RiverError(f"{what} failed (exit {r.returncode}): {cmd}\n{tail}\n"
                             f"Fix it, or send the release back: river review fail {item_id} \"<fix>\"")

    results = []
    for g in groups:
        where = g["path"] if g["path"] and os.path.isdir(g["path"]) else cwd
        for s in g["steps"]:
            if s["kind"] == "run":
                must_pass(s["text"], where, f"review step {s['id']} ({g['project']} {s['pos']})")
                results.append({"id": s["id"], "project": g["project"], "kind": "run", "text": s["text"], "result": "exit 0"})
            else:
                results.append({"id": s["id"], "project": g["project"], "kind": "do", "text": s["text"], "result": "confirmed"})
    cmd = (it["check"] or "").strip()
    ran = None
    if cmd:
        must_pass(cmd, cwd, "review command")
        ran = cmd
    with tx(conn):
        for r in results:
            _event(conn, it["id"], actor, f"review step {r['id']} ({r['project']}) {r['result']}: {r['text']}")
    summary = [f"{len(results)} review step(s) followed"] if results else []
    if ran:
        summary.append(f"{ran} exit 0")
    res = done(conn, item_id, output or "; ".join(["review passed"] + summary), actor)
    res["review_cmd"] = ran
    res["review_steps"] = results
    return res


def review_fail(conn, item_id, fixes, note=None, project=None, actor=None, ask=False):
    """Send a release back: add fix items that the review waits on, and release the review.

    The fixes go to the named project, else to the project of the first item the review covers.
    When they are done, the review is ready again for any reviewer. With ask, the release waits on the
    user first: one item for a person lists the proposed fixes; done adds the fixes still in its list
    (the person may edit it), drop adds none. Either way the review comes back afterwards."""
    it = _review_item(conn, item_id, actor)
    fixes = [f.strip() for f in fixes if f and f.strip()]
    if not fixes:
        raise RiverError(f"name at least one fix: river review fail {item_id} \"<what to fix>\" --note \"<why>\"")
    if project is None:
        r = conn.execute("SELECT p.name FROM deps d JOIN items i ON i.id=d.blocked_by JOIN projects p ON p.id=i.project_id "
                         "WHERE d.item_id=? AND i.kind<>'review' ORDER BY i.id LIMIT 1", (it["id"],)).fetchone()
        if r is None:
            raise RiverError("the review covers no items; name the project for the fixes: --project <name>")
        project = r["name"]
    if ask:
        body = "\n".join(f"- {t}" for t in fixes)
        h = item_add(conn, project, f"Release {it['target']} is blocked: approve {len(fixes)} proposed fix(es)",
                     priority=it["priority"], doer="human", actor=actor, notes=body,
                     context=(f"The review of release {it['target']} (#{it['id']}) found problems that block it"
                              + (f": {note}" if note else "") + ". The proposed fixes are the '- ' lines in the notes. "
                              "Keep, edit, or delete lines, then mark this done: river adds each remaining line as a fix "
                              "item the release waits on. Drop it to add no fixes; the review then comes back."))
        conn.execute("UPDATE items SET kind='fixes' WHERE id=?", (h["id"],))
        dep_add(conn, it["id"], [h["id"]], actor, mode="release")
        res = item_show(conn, it["id"])
        res["asked"] = {"id": h["id"], "title": h["title"], "fixes": fixes}
        return res
    added = [item_add(conn, project, t, priority=it["priority"], doer="ai", actor=actor,
                      context=f"Found in the review of release {it['target']} (#{it['id']})" + (f": {note}" if note else ""))
             for t in fixes]
    dep_add(conn, it["id"], [a["id"] for a in added], actor, mode="release")
    res = item_show(conn, it["id"])
    res["fixes"] = [{"id": a["id"], "title": a["title"], "project": project} for a in added]
    return res


def _review_claim(conn, actor, names, any_project=False, brief=None):
    """Claim a ready release review that covers items of these projects (or any, with any_project)."""
    ann = annotate(conn)
    ready = []
    for a in ann.values():
        if a["kind"] != "review" or not a["ready"] or a["reserved_for"] not in (None, actor):
            continue
        if any_project or any(ann[b]["project"] in names for b in a["waits_on"]):
            ready.append(a)
    for a in sorted(ready, key=lambda a: a["sort_key"]):
        try:
            return claim(conn, a["id"], actor)
        except RiverError as e:
            if brief is not None:
                brief["claim_refused"] = str(e)
    return None


CHECK_RESULTS = ("done", "partial", "open")
# The last event that says who holds an item: when it is a lease expiry, a session stopped mid-item.
LAST_HOLD_EVENT = ("SELECT at, change FROM events WHERE item_id=? AND (change LIKE 'claimed%' OR change LIKE "
                   "'lease expired%' OR change LIKE 'released%' OR change LIKE 'checked:%') ORDER BY id DESC LIMIT 1")


def check(conn, item_id, result, note=None, actor=None):
    """Record what a check of a suspect item found: done (close it), partial (note what is left), or open."""
    if result not in CHECK_RESULTS:
        raise RiverError(f"check result is one of {', '.join(CHECK_RESULTS)}")
    it = _item(conn, item_id)
    if it["status"] in CLOSED_STATES:
        raise RiverError(f"#{item_id} is already {it['status']}")
    note = (note or "").strip()
    if result == "done":
        if not note:
            raise RiverError(f"say where the work is (commits, files): river check {item_id} done --note \"...\"")
        return done(conn, item_id, f"found done in a check: {note}", actor, note=note, force=f"found done in a check: {note}")
    if result == "partial" and not note:
        raise RiverError(f"say what is done and what is left: river check {item_id} partial --note \"...\"")
    with tx(conn):
        if note:
            conn.execute("UPDATE items SET notes=? WHERE id=?",
                         ((it["notes"] + "\n" if it["notes"] else "") + f"[checked {result}] {note}", it["id"]))
        conn.execute("UPDATE items SET needs_check=0 WHERE id=?", (it["id"],))
        _event(conn, it["id"], actor, f"checked: {result}" + (f": {note}" if note else ""))
    return item_show(conn, item_id)


def _git_log(path, *args):
    """Commits as (short id, UTC time like river's, subject); empty when the folder is not a git repository."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", path, "log", "--format=%h%x09%cd%x09%s",
                            "--date=format-local:%Y-%m-%dT%H:%M:%SZ", *args],
                           capture_output=True, text=True, timeout=20, env={**os.environ, "TZ": "UTC"})
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    return [tuple(line.split("\t", 2)) for line in r.stdout.splitlines() if line.count("\t") >= 2]


def cleanup(conn, project=None, git=True):
    """Open items that may be done or stale although the queue says otherwise, with the evidence.

    Suspect: a lease ran out without done; a commit in the project folder names the item (#id);
    a person's item whose touched files changed after it was made; ready and never claimed for
    stale_after; a needs-you notice whose title is out of date. A check (river check) after the
    evidence clears the item."""
    ann = annotate(conn)
    t = now()
    items = [a for a in ann.values() if a["status"] == "open" and not a["project_archived"]
             and (project is None or a["project"] == project) and a["kind"] == "work"]
    if project is not None:
        _project(conn, project)
    ids = {a["id"] for a in items}
    last_check = {r["item_id"]: r["at"] for r in conn.execute(
        "SELECT item_id, MAX(at) at FROM events WHERE change LIKE 'checked:%' GROUP BY item_id")}
    out = {}

    def flag(a, kind, evidence):
        e = out.setdefault(a["id"], {"id": a["id"], "title": a["title"], "project": a["project"], "doer": a["doer"],
                                     "reasons": [], "evidence": []})
        if kind not in e["reasons"]:
            e["reasons"].append(kind)
        e["evidence"].append(evidence)

    for a in items:
        since = max(a["created_at"], last_check.get(a["id"], ""))
        r = conn.execute(LAST_HOLD_EVENT, (a["id"],)).fetchone()
        if a["needs_check"] or (r and r["change"].startswith("lease expired")):
            flag(a, "lease expired", f"{r['change']} at {r['at'][:16]}" if r and r["change"].startswith("lease")
                 else "a lease ran out without done")
        if a["ready"] and not a["claimed_at"]:
            ready_since = max([since] + [ann[b]["closed_at"] or "" for b in a["waits_on"]])
            took = conn.execute("SELECT 1 FROM events WHERE item_id=? AND change LIKE 'claimed%' AND at > ?",
                                (a["id"], ready_since)).fetchone()
            stale = parse_duration(setting(conn, "stale_after", item_id=a["id"]))
            if not took and parse_iso(ready_since) < t - stale:
                flag(a, "stale", f"ready since {ready_since[:10]} and nobody took it")
    for r in conn.execute("SELECT n.item_id, n.summary FROM needs_you n WHERE n.closed_at IS NULL AND n.kind='item'"):
        a = ann.get(r["item_id"])
        if a and a["id"] in ids and a["title"] not in r["summary"]:
            flag(a, "old notice", f"the needs-you notice says: {r['summary']}")
    if git:
        by_path: dict[str, list] = {}
        for a in items:
            p = _project(conn, a["project"])
            if p["path"] and Path(p["path"]).is_dir():
                by_path.setdefault(p["path"], []).append(a)
        for path, group in by_path.items():
            oldest = min(a["created_at"] for a in group)
            commits = _git_log(path, f"--since={oldest}", "-n", "2000")
            for a in group:
                since = max(a["created_at"], last_check.get(a["id"], ""))
                pat = re.compile(rf"#{a['id']}(?!\d)")
                hits = [c for c in commits if pat.search(c[2]) and c[1] >= since[:19]]
                for c in hits[:3]:
                    flag(a, "commit names it", f"commit {c[0]} {c[1][:10]}: {c[2][:80]}")
                if a["doer"] == "human" and a["touches"]:
                    changed = _git_log(path, f"--since={since}", "-n", "3", "--", *a["touches"])
                    for c in changed:
                        flag(a, "files changed", f"commit {c[0]} {c[1][:10]} changed its files: {c[2][:80]}")
    return sorted(out.values(), key=lambda e: ann[e["id"]]["sort_key"])


def drop(conn, item_id, actor=None, note=None):
    return _close(conn, item_id, "dropped", actor, note=note)


def _is_ai(conn, actor):
    r = conn.execute("SELECT kind FROM agents WHERE name=?", (actor,)).fetchone() if actor else None
    return bool(r) and r["kind"] == "ai"


_TAKEOVER_WORDS = {"took over": "took over", "done": "marked done", "dropped": "dropped"}


def _record_takeover(conn, it, kind, note, actor):
    """An agent took a person's item off their list: record it, tell the people, and close their open asks."""
    t = iso(now())
    conn.execute("UPDATE items SET takeover_by=?, takeover_kind=?, takeover_note=?, takeover_at=?, takeover_seen=0 "
                 "WHERE id=?", (actor, kind, note, t, it["id"]))
    _event(conn, it["id"], actor, f"{_TAKEOVER_WORDS[kind]} a person's item: {note}")
    humans = [r["name"] for r in conn.execute("SELECT name FROM agents WHERE kind='human' ORDER BY name")]
    for h in humans:
        _send(conn, "notice", actor, f"{actor} {_TAKEOVER_WORDS[kind]} #{it['id']} {it['title']} (it was yours): {note}. "
              f"Undo: river undo-takeover {it['id']}", to=h, item_id=it["id"])
    if humans:
        conn.execute(f"UPDATE messages SET state='read', read_at=COALESCE(read_at, ?) WHERE item_id=? "
                     f"AND kind IN ('question','alert') AND state='open' AND to_agent IN ({','.join('?' * len(humans))})",
                     [t, it["id"]] + humans)


def takeover(conn, item_id, note, actor=None):
    """An agent does a person's item itself: it becomes an agent item, claimed by the actor, and the people are told."""
    if not actor:
        raise RiverError("taking over needs an agent name: set RIVER_AGENT or pass --as <name>")
    if not (note or "").strip():
        raise RiverError(f"say how you will do it without the user: river takeover {item_id} --note \"...\"")
    with tx(conn):
        _sweep(conn)
        it = _item(conn, item_id)
        if it["doer"] != "human":
            raise RiverError(f"#{item_id} is not a person's item (doer {it['doer']}); claim it: river claim {item_id}")
        a = annotate(conn)[it["id"]]
        if not a["ready"]:
            raise RiverError(f"#{item_id} is not ready ({a['status']}"
                             + (f", waits on {', '.join('#' + str(b) for b in a['open_blockers'])}" if a["open_blockers"] else "")
                             + "); take it over when it is")
        conn.execute("UPDATE items SET doer='ai' WHERE id=?", (it["id"],))
        _record_takeover(conn, it, "took over", note.strip(), actor)
        _claim_row(conn, it["id"], actor)
    return item_show(conn, item_id)


def undo_takeover(conn, item_id, actor=None):
    """Give the item back to the people, open, as it was before an agent took it."""
    with tx(conn):
        it = _item(conn, item_id)
        if not it["takeover_by"]:
            raise RiverError(f"#{item_id} was not taken over by an agent")
        conn.execute("UPDATE items SET doer='human', status='open', assignee=NULL, claimed_at=NULL, lease_expires_at=NULL, "
                     "hold_expires_at=NULL, closed_at=NULL, takeover_by=NULL, takeover_kind=NULL, takeover_note=NULL, "
                     "takeover_at=NULL, takeover_seen=0 WHERE id=?", (it["id"],))
        _event(conn, it["id"], actor, f"undo: back to the people (was {_TAKEOVER_WORDS[it['takeover_kind']]} by {it['takeover_by']})")
        if it["takeover_by"] != actor:
            _send(conn, "notice", actor or "river", f"{actor or 'someone'} gave #{it['id']} {it['title']} back to the people; "
                  "stop work on it; it is no longer yours",
                  to=it["takeover_by"], item_id=it["id"])
    return item_show(conn, item_id)


def takeover_seen(conn, item_id, actor=None):
    with tx(conn):
        _item(conn, item_id)
        conn.execute("UPDATE items SET takeover_seen=1 WHERE id=?", (int(item_id),))
    return {"id": int(item_id), "seen": True}


def takeovers(conn, include_seen=False):
    sql = ("SELECT i.id, i.title, i.status, i.takeover_by, i.takeover_kind, i.takeover_note, i.takeover_at, p.name project "
           "FROM items i JOIN projects p ON p.id=i.project_id WHERE i.takeover_by IS NOT NULL")
    if not include_seen:
        sql += " AND i.takeover_seen=0"
    return [dict(r) for r in conn.execute(sql + " ORDER BY i.takeover_at DESC")]


def release(conn, item_id, note=None, actor=None):
    with tx(conn):
        it = _item(conn, item_id)
        if it["status"] not in ("in_progress", "held"):
            raise RiverError(f"item {item_id} is {it['status']}; only a claimed item can be released")
        if actor and it["assignee"] != actor:
            raise RiverError(f"item {item_id} is held by {it['assignee']}, not {actor}")
        _unhold(conn, it["id"], actor, "released" + (f": {note}" if note else ""))
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
    label = lambda x: "ready" if ann[x]["ready"] else ann[x]["status"]
    a["waits_on_detail"] = [{"id": b, "title": ann[b]["title"], "status": label(b),
                             "kind": "feeds" if b in a["fed_by"] else "blocks"} for b in a["waits_on"]]
    a["unblocks_detail"] = [{"id": d, "title": ann[d]["title"], "status": label(d)} for d in a["unblocks"]]
    a["fed_by_detail"] = [{"id": d, "title": ann[d]["title"], "status": ann[d]["status"], "output": ann[d]["output"]}
                          for d in a["fed_by"]]
    a["conflicts_detail"] = [{"id": d, "title": ann[d]["title"], "status": ann[d]["status"],
                              "assignee": ann[d]["assignee"]} for d in a["conflicts"]]
    a["events"] = [dict(r) for r in conn.execute(
        "SELECT at, actor, change FROM events WHERE item_id=? ORDER BY id DESC LIMIT 50", (iid,))]
    if a["kind"] == "review":
        a["reviews"] = [d for d in a["waits_on_detail"]]
        a["review_steps"] = release_review_steps(conn, iid)
    if a["kind"] == "deploy":
        r = conn.execute("SELECT owner FROM targets WHERE name=?", (a["target"],)).fetchone()
        a["target_owner"] = r["owner"] if r else None
        a["ships"] = a["waits_on_detail"]
    a["found_here"] = [{"id": r["id"], "title": ann[r["id"]]["title"], "status": label(r["id"])} for r in conn.execute(
        "SELECT id FROM items WHERE found_during=? ORDER BY id", (iid,))]
    a["message_count"] = conn.execute("SELECT COUNT(*) FROM messages WHERE item_id=?", (iid,)).fetchone()[0]
    return a


def item_list(conn, project=None, status=None, include_closed=False, goal=None, ref=None):
    """Items in order. With ref: the items linked to that tracker issue, closed ones too (an import checks it)."""
    ann = annotate(conn)
    rows = [a for a in ann.values() if not a["project_archived"]]
    if ref is not None:
        rows = [a for a in rows if any(r["ref"] == ref.strip() for r in a["refs"])]
        include_closed = True
    if goal is not None:
        _goal(conn, goal)
        rows = [a for a in rows if goal in a["goals"]]
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
            "ready": a["ready"], "blocked_reason": a["blocked_reason"], "blocked_text": a["blocked_text"], "lease_seconds_left": left,
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
    if to is not None and conn.execute("SELECT 1 FROM agents WHERE name=? AND kind='ai' AND platform IS NOT NULL",
                                       (to,)).fetchone():
        st = deliver_native(conn, to, _native_text(kind, actor, body.strip()))
        with tx(conn):
            conn.execute("UPDATE messages SET native_status=? WHERE id=?", (st, mid))
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
        return {"unread": 0, "alerts": 0, "questions": 0, "questions_waiting": 0, "nudge_after": ""}
    nudge = setting(conn, "question_nudge_after", agent=actor)
    old = iso(now() - parse_duration(nudge))
    r = conn.execute(
        f"SELECT SUM(m.read_at IS NULL) unread, SUM(m.read_at IS NULL AND m.kind='alert') alerts, "
        f"SUM(m.kind='question' AND m.state='open') questions, "
        f"SUM(m.kind='question' AND m.state='open' AND m.created_at <= ?) waiting "
        f"FROM messages m WHERE {_TO_ME} AND m.from_agent<>?",
        (old, actor, actor, actor)).fetchone()
    return {"unread": r["unread"] or 0, "alerts": r["alerts"] or 0, "questions": r["questions"] or 0,
            "questions_waiting": r["waiting"] or 0, "nudge_after": nudge}


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
    # Planner sessions change the plan and take no work, so they are not slots.
    ai_active = [a for a in agents if a["kind"] == "ai" and a["state"] == "active" and a.get("role") != "planner"]
    ai_busy = [a for a in ai_active if a["holds"]]
    ai_idle = [a for a in ai_active if not a["holds"]]
    # Waiting sessions (river wait) take new work by themselves within seconds, and end after wait_max.
    ai_waiting = [a for a in ai_idle if a.get("role") == "waiting"]
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
    stuck_idle = max(-spare - len(ai_waiting), 0)
    if spare < 0 and ai_waiting:
        advice.append({"kind": "waiting", "text": f"{len(ai_waiting)} session(s) wait for work: they take the next "
                       f"ready item by themselves, or one you push to them, and end after "
                       f"{setting(conn, 'wait_max')} without work."})
    if stuck_idle:
        advice.append({"kind": "too_many", "text": f"{len(ai_idle)} active agent session(s) hold nothing, "
                       f"but only {runnable} ready item(s) can run for them now. "
                       f"{stuck_idle} session(s) have no work; stop them or give them other work."})
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
        "agents_waiting": [a["name"] for a in ai_waiting],
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
        "due": [{"id": a["id"], "title": a["title"], "project": a["project"], "due_text": a["due_text"],
                 "due_state": a["due_state"]} for a in sorted(ann.values(), key=lambda a: a["due"] or "")
                if a["due"] and a["status"] in OPEN_STATES and not a["project_archived"]],
        "unsynced": unsynced(conn),
        "spare_slots": cap["spare_slots"],
        "excess_sessions": cap["excess_sessions"],
        "advice": cap["advice"],
    }


def _person(conn, person=None):
    if person:
        return person
    r = conn.execute("SELECT name FROM agents WHERE kind='human' ORDER BY registered_at, name LIMIT 1").fetchone()
    return r["name"] if r else "<your-name>"


def _item_prompt_section(conn, a, ann, person, n=None):
    p = conn.execute("SELECT name, path, notes FROM projects WHERE name=?", (a["project"],)).fetchone()
    blocks = [ann[d] for d in a["unblocks"] if ann[d]["status"] in OPEN_STATES]
    lines = [f"{'' if n is None else f'{n}. '}Item #{a['id']}: {a['title']}  (project {a['project']}, P{a['effective_priority']})"]
    if p["path"]:
        lines.append(f"   Folder: {p['path']}")
    if blocks:
        lines.append("   It blocks: " + "; ".join(f"#{b['id']} {b['title']} (P{b['effective_priority']})" for b in blocks[:3]))
    for label, text in (("Context", a["context"]), ("Notes", a["notes"])):
        if text.strip():
            lines.append(f"   {label}: {text.strip()}")
    cd = f"cd {p['path']} && " if p["path"] else ""
    lines.append(f"   Read it first: {cd}river --as {person} show {a['id']}")
    lines.append(f"   Record the result: river --as {person} done {a['id']} --output \"<what was decided or done>\"")
    return "\n".join(lines)


# How an agent puts a decision to a person in chat (river item #168: compressed tables and one-line
# recommendations made the person ask for details on every decision).
DECISION_FORMAT = """Put each decision to the person in this form, one decision at a time:

  Decision <n> of <total>: <short name>  (item #<id>)
  What you decide: one sentence, as a question, with the kind of answer (yes or no, pick A, B, or C, a number).
  Why it matters: what it changes, and what waits on it.
  Options: one line or more for each option: what happens if they pick it, what it costs, the risk,
    and whether they can change it later. Use the real names, amounts, and dates; no shorthand.
  My recommendation: the option, and why, in one or two sentences.
  Your answer: the exact words to reply, for example "A", "yes", or "$200 a month".

Then stop and wait for the answer before you show the next decision. Do not squeeze several decisions
into one table. If they ask what something means, explain it, then ask the same decision again."""

PROMPT_STEPS = """How to help:
1. Run the "read it first" command. Read the files or links it names.
2. Explain to {person} in plain words what is needed and why.
""" + "\n".join("   " + line if line else "" for line in DECISION_FORMAT.split("\n")) + """
3. Answer their questions. Help them do it: draft the text, check the setting, walk through the steps.
   Ask before anything that uses their accounts, money, or sends something in their name.
4. When it is decided or done, record it with the "record the result" command, in their words.
   If you can do the step yourself without them, say so; with their yes, take it over instead:
   river register <your-agent-name>, then river --as <your-agent-name> takeover <id> --note "<how>".
If the river command is not found, ask {person} where Biggest River is installed."""


def prompt_for(conn, item_id, person=None):
    """A paste-ready prompt that lets a fresh agent explain one human item and close it with the person."""
    ann = annotate(conn)
    iid = _item(conn, item_id)["id"]
    person = _person(conn, person)
    a = ann[iid]
    head = (f"You are helping {person} with one step in their work queue (Biggest River, the `river` command). "
            + ("It is marked for a person to do or decide." if a["doer"] == "human"
               else f"{person} took it to do themselves, with your help."))
    return "\n\n".join([head, _item_prompt_section(conn, a, ann, person), PROMPT_STEPS.format(person=person)])


def prompt_for_all(conn, person=None):
    """The whole needs-you list for a person as one prompt, most important first."""
    ann = annotate(conn)
    person = _person(conn, person)
    items = sorted((a for a in ann.values() if a["ready"] and a["doer"] == "human" and not a["project_archived"]),
                   key=lambda a: a["sort_key"])
    qs = [dict(r) for r in conn.execute(
        "SELECT id, from_agent, body, item_id FROM messages WHERE kind='question' AND state='open' AND to_agent=? ORDER BY id",
        (person,))]
    if not items and not qs:
        return f"Nothing in the Biggest River queue waits on {person} now."
    parts = [f"You are helping {person} work through everything that waits on them in their work queue "
             f"(Biggest River, the `river` command): {len(items)} item(s) and {len(qs)} question(s), most important "
             f"first. Take them one at a time: finish or park one before you start the next."]
    parts += [_item_prompt_section(conn, a, ann, person, n) for n, a in enumerate(items, 1)]
    for n, q in enumerate(qs, len(items) + 1):
        parts.append(f"{n}. Question #{q['id']} from {q['from_agent']}" + (f" about #{q['item_id']}" if q["item_id"] else "")
                     + f": {q['body']}\n   Answer it: river --as {person} answer {q['id']} \"<answer>\"")
    parts.append(PROMPT_STEPS.format(person=person))
    return "\n\n".join(parts)


def parse_launch_agents(value):
    """'Claude Code=claude go; Codex=codex "run river go"' -> [("Claude Code", "claude go"), ...]."""
    out = []
    for part in (value or "").split(";"):
        if not part.strip():
            continue
        label, sep, cmd = part.partition("=")
        if not sep or not label.strip() or not cmd.strip():
            raise RiverError(f"launch_agents entry {part.strip()!r} needs the form Label=command, for example "
                             f"'Claude Code=claude go; Codex=codex \"run river go in this folder and follow the briefing\"'")
        out.append((label.strip(), cmd.strip()))
    if not out:
        raise RiverError("launch_agents needs at least one Label=command entry")
    return out


def waiting_agent_for(conn, project, item_id=None):
    """An active session that waits for work in this project (river wait), longest waiting first; with
    item_id, only one whose model the item's limits allow."""
    for r in conn.execute("SELECT name, waiting_in FROM agents WHERE role='waiting' AND kind='ai' "
                          "ORDER BY waiting_since").fetchall():
        a = agent_status(conn, r["name"])
        if a["state"] == "active" and not a["holds"] and project in (r["waiting_in"] or "").split(","):
            if item_id is None or not _model_refusal(conn, _item(conn, item_id), r["name"]):
                return r["name"]
    return None


def covered_projects(conn, ann=None):
    """Projects that have an agent, with one of its sessions: a session that is not gone and holds an item
    there, waits for work there (river wait), or has an item there pushed to it (a Start just opened it)."""
    ann = ann if ann is not None else annotate(conn)
    live = {r["name"] for r in conn.execute("SELECT * FROM agents WHERE kind='ai'")
            if _agent_state(conn, r) not in ("gone", "stopped")}
    covered = {}
    for a in sorted(ann.values(), key=lambda a: a["id"]):
        if a["status"] in ("in_progress", "held") and a["assignee"] in live:
            covered.setdefault(a["project"], a["assignee"])
        elif a["status"] == "open" and a["reserved_until"] and a["reserved_for"] in live:
            covered.setdefault(a["project"], a["reserved_for"])
    for r in conn.execute("SELECT name, waiting_in FROM agents WHERE role='waiting' AND kind='ai' ORDER BY name"):
        if r["name"] in live:
            for p in (r["waiting_in"] or "").split(","):
                if p:
                    covered.setdefault(p, r["name"])
    return covered


def launch_target(conn, project=None, agent=None, item=None, model=None, effort=None, launch_in=None, spread=False):
    """Where and how a new agent session should start: the folder of the project that holds the most
    important ready item an agent can take (or of the project named, or of the one item named), and the
    command of the chosen launch_agents entry (the first when none is named). Refuses when nothing is ready there.

    spread (Start with no project or item named): first a project with ready agent work and no agent yet,
    the one whose top item is most important; when every such project has an agent, the top item."""
    ann = annotate(conn)
    pool = sorted((a for a in ann.values() if a["ready"] and a["doer"] != "human" and a["kind"] not in ("deploy", "review", "monitor")
                   and not a["reserved_for"] and not a["project_archived"]
                   and (project is None or a["project"] == project)), key=lambda a: a["sort_key"])
    if item is not None:
        top = next((a for a in pool if a["id"] == int(item)), None)
        if top is None:
            a = ann.get(int(item))
            raise RiverError(f"#{item} is not ready for an agent" + (
                "" if a is None else f" (status {a['status']}" + (f", reserved for {a['reserved_for']}" if a["reserved_for"] else "")
                + (", for a person" if a["doer"] == "human" else "") + (", waits on open items" if not a["ready"] else "") + ")"))
    elif not pool:
        raise RiverError("nothing is ready for an agent" + (f" in {project}" if project else "")
                         + "; a new session would have no work")
    else:
        top = pool[0]
    why = None
    if spread and item is None and project is None:
        covered = covered_projects(conn, ann)
        firsts = {}
        for a in pool:
            firsts.setdefault(a["project"], a)
        open_ = [a for p, a in firsts.items() if p not in covered]
        have = [p for p in firsts if p in covered]
        if open_:
            top = open_[0]
            why = f"first agent for project {top['project']}" + (
                f"; {', '.join(have)} already {'has' if len(have) == 1 else 'have'} one" if have else "")
        else:
            why = "every project with ready work has an agent, so the most important ready item"
    p = _project(conn, top["project"])
    if not p["path"]:
        raise RiverError(f"project {p['name']} has no folder, so river cannot start a session there: "
                         f"river project path {p['name']} <folder>")
    if model:
        ok, why = model_check(parse_ladder(setting(conn, "model_ladder")), model, top["min_model"], top["max_model"])
        if not ok:
            raise RiverError(f"#{top['id']} {why}; pick another model")
    return {"project": p["name"], "path": p["path"], "item": {"id": top["id"], "title": top["title"]},
            "ready": len(pool), "why": why, **_launch_agent_cmd(conn, p["id"], agent, model, effort),
            "launch_in": _launch_in(conn, p["id"], launch_in)}


def _start_next(conn):
    """What Start (work: top item) opens now, and why; None when nothing is ready."""
    try:
        t = launch_target(conn, spread=True)
    except RiverError:
        return None
    return {**t["item"], "project": t["project"], "why": t["why"]}


def _launch_in(conn, project_id, choice=None):
    if choice not in (None, "", "tab", "window"):
        raise RiverError("launch_in is tab or window")
    return choice or setting(conn, "launch_in", project_id=project_id)


def _launch_agent_cmd(conn, project_id, agent, model=None, effort=None):
    """The chosen launch_agents entry, with {model} and {effort} filled in. The session also gets RIVER_MODEL."""
    agents = parse_launch_agents(setting(conn, "launch_agents", project_id=project_id))
    pick = agents[0] if agent is None else next((a for a in agents if a[0] == agent), None)
    if pick is None:
        raise RiverError(f"no agent {agent!r} in launch_agents; known: {', '.join(a for a, _ in agents)}")
    model = _check_model_name(model) if model else None
    if effort and not re.match(r"^[a-z][a-z0-9_-]{0,31}$", effort):
        raise RiverError(f"effort {effort!r}: a level such as low, medium, high")
    return {"agent": pick[0], "command": fill_launch_command(pick[1], model, effort or None),
            "model": model, "effort": effort or None, "env": {"RIVER_MODEL": model} if model else {}}


# Which model family an agent CLI runs, by the program that starts it, and the effort levels its CLI takes.
AGENT_FAMILIES = {"claude": "claude", "codex": "openai"}
PLATFORM_EFFORTS = {"claude": ["low", "medium", "high", "xhigh", "max"],
                    "openai": ["minimal", "low", "medium", "high", "xhigh"]}
# One line per model in the launch dialog: when it fits.
MODEL_NOTES = {
    "sonnet": "routine, well specified work: monitors, checks, small fixes",
    "opus": "normal feature work that follows the code already there",
    "fable": "hard design, hard bugs, security, data that is costly to lose",
    "luna": "quick routine edits and checks",
    "terra": "routine work that needs some judgment",
    "sol": "normal feature work that follows the code already there",
    "astra": "the hardest problems: design, hard bugs, security",
}


def fill_launch_command(cmd, model, effort):
    """Put the model and effort into a launch command. Without a value the placeholder goes, and with it
    the flag just before it: '--model {model}' and '-c model_reasoning_effort={effort}' drop out whole."""
    for key, value in (("model", model), ("effort", effort)):
        ph = "{" + key + "}"
        if value:
            cmd = cmd.replace(ph, value)
            continue

        def drop(m):
            # A word that is a flag itself ('--effort={effort}') goes alone; the flag before it stays.
            return (m.group(1) or "") if m.group(2).startswith("-") else ""
        cmd = re.sub(r"(\s+-[\w-]+)?\s+(\S*" + re.escape(ph) + r"\S*)", drop, " " + cmd)[1:]
    return cmd


def launch_options(conn):
    """What the launch dialog offers for each launch_agents entry: its family, models, and effort levels."""
    ladder = parse_ladder(setting(conn, "model_ladder"))
    levels = _levels(setting(conn, "effort_levels"))
    out = []
    for label, cmd in parse_launch_agents(setting(conn, "launch_agents")):
        exe = Path(cmd.split()[0]).name.lower() if cmd.split() else ""
        fam = AGENT_FAMILIES.get(exe)
        fam = fam if fam in ladder else None
        out.append({"label": label, "family": fam,
                    "models": [{"name": m, "note": MODEL_NOTES.get(m, ""), "family": f}
                               for f, ms in ladder.items() if fam in (None, f) for m in ms],
                    "efforts": PLATFORM_EFFORTS.get(fam, levels),
                    "takes_model": "{model}" in cmd, "takes_effort": "{effort}" in cmd})
    return out


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
        "targets": targets_view(conn, ann),
        "launch_agents": [label for label, _ in parse_launch_agents(setting(conn, "launch_agents"))],
        "model_ladder": parse_ladder(setting(conn, "model_ladder")),
        "launch_options": launch_options(conn),
        "launch_in": setting(conn, "launch_in"),
        "start_next": _start_next(conn),
        "effort_levels": _levels(setting(conn, "effort_levels")),
        "settings": config_list(conn),
        "events": recent_events(conn),
        "takeovers": takeovers(conn),
        "needs_you": [dict(r) for r in conn.execute(
            "SELECT id, kind, item_id, message_id, human, summary, opened_at FROM needs_you "
            "WHERE closed_at IS NULL ORDER BY id DESC")],
    }


# ---------------------------------------------------------------- go

ROLES = ("deployer", "reviewer", "owner", "worker", "unblocker", "planner", "idle")


def _owned_goal(conn, actor, names):
    """The first open goal the actor owns, in these projects if any of them have one."""
    rows = conn.execute("SELECT g.* FROM goals g JOIN projects p ON p.id=g.project_id WHERE g.owner=? AND "
                        "g.status='open' ORDER BY p.rank, g.rank, g.id", (actor,)).fetchall()
    here = [g for g in rows if _project_name(conn, g["project_id"]) in names]
    return (here or rows or [None])[0]


def _goal_brief(conn, name, actor):
    """What a goal owner needs in the briefing: the outcome, the test, its items, and who holds its blockers."""
    ann = annotate(conn)
    g = _goal_view(conn, _goal(conn, name), ann)
    open_items = sorted((ann[i] for i in g["items_open"]), key=lambda a: a["sort_key"])
    blockers, seen = [], set()
    for a in open_items:
        for b in [a["id"]] + sorted(_prereq_closure(ann, a["id"])):
            x = ann[b]
            if b in seen or x["status"] not in ("in_progress", "held") or x["assignee"] in (None, actor):
                continue
            seen.add(b)
            other = [n for n in x["goals"] if n != name]
            owner = conn.execute("SELECT owner FROM goals WHERE name=?", (other[0],)).fetchone()["owner"] if other else None
            blockers.append({"id": b, "title": x["title"], "assignee": x["assignee"],
                             "goal": other[0] if other else None, "goal_owner": owner})
    return {"name": g["name"], "project": g["project"], "outcome": g["outcome"], "done_when": g["done_when"],
            "items_open": [{"id": a["id"], "title": a["title"], "status": a["status"], "ready": a["ready"],
                            "assignee": a["assignee"], "doer": a["doer"]} for a in open_items],
            "items_done": len(g["items_done"]), "blockers_held": blockers,
            "lease": _short(_goal_lease(conn, _goal(conn, name), actor))}


def go(conn, cwd, actor=None, project=None, role=None, session=None, focus=None, model=None):
    """One call for a fresh agent session; see _go. A session that gets a monitor item has role monitor,
    and its brief says which deploy it follows and whom to alert."""
    brief = _go(conn, cwd, actor, project, role, session, focus, model)
    it = brief.get("item")
    if it and it["kind"] == "monitor":
        brief["role"] = "monitor"
        brief["monitor"] = _monitor_brief(conn, it)
        _set_role_note(conn, brief["agent"], "monitor", it["id"])
    return brief


def _monitor_brief(conn, it):
    dep = _item(conn, it["found_during"]) if it["found_during"] else None
    deployer = dep and (dep["assignee"] or _done_by(conn, dep["id"]))
    return {"deploy": {"id": dep["id"], "title": dep["title"], "status": dep["status"]} if dep else None,
            "deployer": deployer, "target": it["target"], "person": _person(conn)}


def _go(conn, cwd, actor=None, project=None, role=None, session=None, focus=None, model=None):
    """One call for a fresh agent session: find the project, name the session, pick a role, and brief it.

    focus (RIVER_FOCUS, set when the page opens an agent): "help:<id>@<person>" briefs the session to do a
    person's item together with the person (the Copy prompt text); "needs:@<person>" the same for everything
    that waits on the person; "unblock:<id>" takes work that unblocks that item first."""
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
            q = queue_note()
            raise RiverError(
                (f"{q}. If this folder's work is in another queue, that is why: start the agent without RIVER_DB. "
                 if q else "")
                + f"no project is linked to {Path(cwd).resolve()} in this queue. Ask the user which project this "
                f"folder is, then: river go --project <name>. A project without a folder: river project path <name> . "
                f"A new project for this folder: river init. Do not move a project that is linked to another "
                f"folder. Projects: {listing or '(none)'}")
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
    if session:
        set_session(conn, actor, session)
    if model:
        set_agent_model(conn, actor, model)
    with tx(conn):
        conn.execute("UPDATE agents SET role=NULL WHERE name=? AND role='planner'", (actor,))

    ann = annotate(conn)
    descs = {p["name"]: p["notes"] for p in project_list(conn) if p["name"] in names}
    trackers = {p["name"]: p["tracker"] for p in project_list(conn) if p["name"] in names and p["tracker"]}
    in_area = [a for a in ann.values() if a["project"] in names]
    open_in_area = [a for a in in_area if a["status"] in OPEN_STATES]
    human_ready = sorted((a for a in in_area if a["ready"] and a["doer"] == "human"), key=lambda a: a["sort_key"])
    brief = {"agent": actor, "new_name": new_name, "projects": names, "descriptions": descs, "trackers": trackers,
             "human_waiting": [{"id": a["id"], "title": a["title"],
                                "blocks": [{"id": d, "title": ann[d]["title"], "priority": ann[d]["effective_priority"]}
                                           for d in a["unblocks"] if ann[d]["status"] in OPEN_STATES]}
                               for a in human_ready],
             "has_history": bool(history(conn, actor, limit=1)),
             "auto_continue": setting(conn, "auto_continue", agent=actor) == "on",
             "session": _agent(conn, actor)["session"],
             "human_wait_max": setting(conn, "human_wait_max", agent=actor),
             "messages": unread(conn, actor),
             "humans": [r["name"] for r in conn.execute("SELECT name FROM agents WHERE kind='human' ORDER BY name")]}

    brief["unsynced"] = unsynced(conn, actor)
    stop = stop_request(conn, actor)
    if stop:
        holds = [item_show(conn, r["id"]) for r in conn.execute(
            "SELECT id FROM items WHERE assignee=? AND status IN ('in_progress','held') ORDER BY id", (actor,))]
        ended = not holds and _finish_stop(conn, actor)
        brief.update(role="stopped", item=None, stop=stop, stop_holds=holds, ended=ended,
                     why=f"stop requested by {stop['stop_by']}: {stop['stop_reason']}")
        return brief
    brief["queue_instructions"] = queue_instructions(conn, actor)
    brief["model"] = agent_model(conn, actor)
    brief["model_skipped"] = skipped = []
    mine_goal = _owned_goal(conn, actor, names)
    if mine_goal is not None:
        brief["goal"] = _goal_brief(conn, mine_goal["name"], actor)

    # Resume: an item already held.
    held = conn.execute("SELECT id FROM items WHERE assignee=? AND status IN ('in_progress','held') "
                        "ORDER BY status='held', id", (actor,)).fetchall()
    if held and role in (None, "worker", "unblocker"):
        parent = _item(conn, held[0]["id"])
        if parent["status"] == "held":
            ann = annotate(conn)
            mine = sorted((ann[b] for b in _open_prereqs(conn, parent["id"])
                           if ann[b]["ready"] and ann[b]["reserved_for"] == actor), key=lambda a: a["sort_key"])
            if mine:
                try:
                    item = claim(conn, mine[0]["id"], actor)
                except RiverError as e:
                    brief["claim_refused"] = str(e)
                else:
                    brief.update(role="worker", item=item,
                                 why=f"you hold #{parent['id']}; #{item['id']} is a prerequisite reserved for you")
                    _set_role_note(conn, actor, "worker", item["id"])
                    return brief
        item = item_show(conn, held[0]["id"])
        if parent["status"] == "held" and _human_prereqs(conn, parent["id"]):
            brief["human_wait"] = {"on": [{"id": h, "title": _item(conn, h)["title"]}
                                          for h in _human_prereqs(conn, parent["id"])],
                                   "until": _item(conn, parent["id"])["hold_expires_at"]}
        r = {"deploy": "deployer", "review": "reviewer"}.get(item["kind"], "worker")
        if r == "deployer":
            brief.update(_deploy_brief(conn, item))
        brief.update(role=r, resumed=True, item=item,
                     why=f"you already hold #{item['id']}; finish or release it first")
        _set_role_note(conn, actor, r, item["id"])
        return brief

    if role in (None, "worker") and not focus:
        # The agent's own queue before the project queue: the first ready item in it.
        for a in _queue_ready(conn, actor):
            try:
                item = claim(conn, a["id"], actor)
            except RiverError as e:
                brief["claim_refused"] = str(e)
                continue
            brief.update(role="worker", item=item, why=f"#{item['id']} is the first ready item in your queue")
            _set_role_note(conn, actor, "worker", item["id"])
            return brief
        brief["queue_waiting"] = [e for e in queue_list(conn, actor)["entries"] if e.get("item")]

    def try_claim(**kw):
        try:
            got = next_item(conn, claim=True, actor=actor, skipped=skipped, **kw)
        except RiverError as e:
            brief["claim_refused"] = str(e)
            return None
        return got[0] if got else None

    kind, _, fid = (focus or "").partition(":")
    fid, _, person = fid.partition("@")
    # Deploy now on the Targets tab: the session deploys (owning the free target) or reviews the release.
    if role is None and kind in ("deploy", "review"):
        role = "deployer" if kind == "deploy" else "reviewer"
    if role is None and kind == "needs":
        person = _person(conn, person or None)
        brief.update(role="helper", item=None, help_prompt=prompt_for_all(conn, person),
                     why=f"the page opened this session to work through what waits on {person}, with them")
        _set_role_note(conn, actor, "helper", None)
        return brief
    if role is None and fid.isdigit() and int(fid) in annotate(conn):
        f = item_show(conn, int(fid))
        if f["status"] in OPEN_STATES and kind == "help" and (f["doer"] == "human" or (person and f["assignee"] == person)):
            person = _person(conn, person or None)
            brief.update(role="helper", item=None, help_prompt=prompt_for(conn, f["id"], person),
                         why=f"the page opened this session to do #{f['id']} together with {person}")
            _set_role_note(conn, actor, "helper", f["id"])
            return brief
        if f["status"] == "open" and kind == "monitor" and f["kind"] == "monitor":
            try:
                item = claim(conn, f["id"], actor)
            except RiverError as e:
                brief["focus_note"] = f"The page opened this session to monitor #{f['id']}, but: {e}"
            else:
                brief.update(role="monitor", item=item, why=f"river opened this session to follow deploy "
                                                            f"#{f['found_during']} of {f['target']}")
                return brief
        if f["status"] in OPEN_STATES and kind == "unblock":
            got = try_claim(unblocks=str(f["id"]))
            if got:
                brief.update(role="unblocker", item=got,
                             why=f"the page opened this session to unblock #{f['id']} {f['title']}")
                _set_role_note(conn, actor, "unblocker", got["id"])
                return brief
            brief["focus_note"] = (f"The page opened this session to unblock #{f['id']}, but nothing that blocks it "
                                   f"is ready for an agent now (river blockers {f['id']}).")
    if role in (None, "worker"):
        ann = annotate(conn)
        pushed = sorted((a for a in ann.values() if a["reserved_for"] == actor and a["reserved_until"] and a["ready"]),
                        key=lambda a: a["sort_key"])
        for a in pushed:
            try:
                item = claim(conn, a["id"], actor)
            except RiverError as e:
                brief["claim_refused"] = str(e)
                continue
            brief.update(role="worker", item=item, why=f"#{item['id']} was pushed to you by {a['reserved_by'] or 'someone'}")
            _set_role_note(conn, actor, "worker", item["id"])
            return brief
    if role in (None, "deployer"):
        got = _deploy_claim(conn, actor, names, take_free=(role == "deployer"), brief=brief)
        if got:
            brief.update(_deploy_brief(conn, got))
            brief.update(role="deployer", item=got,
                         why=f"you own target {got['target']} and #{got['id']} is ready to deploy")
            _set_role_note(conn, actor, "deployer", got["id"])
            return brief
        if role == "deployer":
            brief.update(role="idle", item=None, held_by_others=[],
                         why=brief.get("claim_refused") or "no deploy item is ready for the targets you own")
            _set_role_note(conn, actor, "idle", None)
            return brief
    if role in (None, "reviewer"):
        # A release waits on its review: reviews come before new work, so finished work goes out.
        got = _review_claim(conn, actor, names, any_project=(role == "reviewer"), brief=brief)
        if got:
            brief.update(role="reviewer", item=got,
                         why=f"release {got['target']} waits on this review, and everything it ships is done")
            _set_role_note(conn, actor, "reviewer", got["id"])
            return brief
        if role == "reviewer":
            brief.update(role="idle", item=None, held_by_others=[],
                         why=brief.get("claim_refused") or "no release review is ready")
            _set_role_note(conn, actor, "idle", None)
            return brief
    if role in (None, "owner", "worker"):
        g = _owned_goal(conn, actor, names)
        if g is None and role in (None, "owner"):
            # Agents own outcomes: take an open goal nobody owns. --role owner takes the highest-ranked one.
            # Plain go never forces a goal: it takes one only when the best ready work in the area serves it
            # (the item is tagged with the goal, or an open item of the goal waits on it); otherwise the
            # agent works as a worker below. After goal done, the owner continues with go --role owner.
            free = conn.execute(f"SELECT g.name FROM goals g JOIN projects p ON p.id=g.project_id WHERE g.status='open' "
                                f"AND g.owner IS NULL AND p.archived=0 AND p.name IN ({','.join('?' * len(names))}) "
                                f"ORDER BY p.rank, g.rank, g.id", names).fetchall()
            if role is None:
                try:
                    best = next_item(conn, area, None, False, actor, 1)
                except RiverError:
                    best = []
                serves = set()
                if best:
                    ann = annotate(conn)
                    serves = set(best[0].get("goals") or [])
                    for a in ann.values():
                        if a["goals"] and a["status"] in OPEN_STATES and best[0]["id"] in _prereq_closure(ann, a["id"]):
                            serves.update(a["goals"])
                free = [f for f in free if f["name"] in serves]
            for f in free:
                try:
                    goal_own(conn, f["name"], actor)
                except RiverError as e:
                    brief["claim_refused"] = str(e)
                    continue
                g = _goal(conn, f["name"])
                brief["took_goal"] = True
                break
        if g is not None:
            gb = brief["goal"] = _goal_brief(conn, g["name"], actor)
            ann = annotate(conn)
            tagged = sorted((a for a in ann.values() if g["name"] in a["goals"] and a["ready"] and a["doer"] != "human"
                             and a["kind"] not in ("deploy", "review") and a["reserved_for"] in (None, actor)), key=lambda a: a["sort_key"])
            for a in tagged:  # (1) the next ready item of the goal
                try:
                    item = claim(conn, a["id"], actor)
                except RiverError as e:
                    brief["claim_refused"] = str(e)
                    continue
                brief.update(role="owner", item=item, why=f"#{item['id']} is the next ready item for your goal {g['name']}")
                _set_role_note(conn, actor, "owner", item["id"])
                return brief
            for o in gb["items_open"]:  # (2) work outside the goal that unblocks it
                item = try_claim(unblocks=str(o["id"]))
                if item:
                    brief.update(role="owner", item=item,
                                 why=f"#{item['id']} unblocks #{o['id']} of your goal {g['name']}")
                    _set_role_note(conn, actor, "owner", item["id"])
                    return brief
            if not gb["items_open"]:  # (3) nothing open: judge the outcome
                brief.update(role="owner", item=None, goal_action="judge",
                             why=f"your goal {g['name']} has no open items")
                _set_role_note(conn, actor, "owner", None)
                return brief
            brief["goal_action"] = "wait"  # (4) its items wait on others: take other work meanwhile
        elif role == "owner":
            brief.update(role="idle", item=None, held_by_others=[], why="no goal here is free")
            _set_role_note(conn, actor, "idle", None)
            return brief
    if role in (None, "worker"):
        item = try_claim(project=area)
        if item:
            brief.update(role="worker", item=item, why=f"#{item['id']} is the most important ready item in {area}"
                         + ("; your goal waits on others meanwhile" if brief.get("goal_action") == "wait" else ""))
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

    if brief.get("goal_action") == "wait":
        brief.update(role="owner", item=None,
                     why=f"the open items of your goal {brief['goal']['name']} wait on other sessions or people")
        _set_role_note(conn, actor, "owner", None)
        return brief
    outside = [a for a in open_in_area if a["status"] == "open" and a["blocked_reason"]]
    held_by_others = [a for a in open_in_area if a["status"] in ("in_progress", "held")]
    needs_plan = not open_in_area or (not held_by_others and len(outside) == len([a for a in open_in_area if a["status"] == "open"]))
    if role == "planner" or (role is None and needs_plan):
        brief.update(role="planner", item=None,
                     open_items=[{"id": a["id"], "title": a["title"], "blocked_reason": a["blocked_reason"],
                                  "blocked_text": a["blocked_text"], "open_blockers": a["open_blockers"]} for a in open_in_area],
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


def _deploy_claim(conn, actor, names, take_free=False, brief=None):
    """Claim a ready deploy item on a target the actor owns; with take_free, first own a free target of these projects."""
    if take_free:
        targets = [r["target"] for r in conn.execute(
            f"SELECT DISTINCT target FROM projects WHERE target IS NOT NULL AND name IN ({','.join('?' * len(names))})",
            names)] if names else []
        for t in targets:
            try:
                target_own(conn, t, actor)
            except RiverError as e:
                if brief is not None:
                    brief["claim_refused"] = str(e)
    owned = [r["name"] for r in conn.execute("SELECT name FROM targets WHERE owner=? ORDER BY name", (actor,))]
    if not owned:
        return None
    ann = annotate(conn)
    ready = sorted((a for a in ann.values() if a["kind"] == "deploy" and a["target"] in owned and a["ready"]),
                   key=lambda a: a["sort_key"])
    for a in ready:
        try:
            return claim(conn, a["id"], actor)
        except RiverError as e:
            if brief is not None:
                brief["claim_refused"] = str(e)
    return None


def _deploy_brief(conn, item):
    t = dict(_target(conn, item["target"]))
    nxt = conn.execute("SELECT id FROM items WHERE kind='deploy' AND target=? AND status='open' AND id<>? ORDER BY id",
                       (t["name"], item["id"])).fetchall()
    mon = conn.execute("SELECT id, title, status, assignee FROM items WHERE kind='monitor' AND found_during=?",
                       (item["id"],)).fetchone()
    return {"target": {"name": t["name"], "description": t["description"], "owner": t["owner"], "monitor": t["monitor"]},
            "monitor_item": dict(mon) if mon else None,
            "ships": item["waits_on_detail"],
            "next_deploy": [item_show(conn, r["id"]) for r in nxt]}


def _set_role_note(conn, actor, role, item_id):
    note = f"role: {role}" + (f" on #{item_id}" if item_id else "")
    with tx(conn):
        # A role with work ends a wait; idle keeps the wait clock, so wait_max counts from the first wait.
        keep = role in ("idle", "waiting")
        conn.execute("UPDATE agents SET note=?, role=?, waiting_since=CASE WHEN ? THEN waiting_since END, "
                     "waiting_in=CASE WHEN ? THEN waiting_in END WHERE name=?", (note, role, keep, keep, actor))


def _work_for(conn, actor, names):
    """Why this agent has something to do now, or None: a push to it, ready work it can take, or messages."""
    pushed = conn.execute("SELECT id FROM items WHERE reserved_for=? AND reserved_until IS NOT NULL "
                          "AND status='open'", (actor,)).fetchone()
    if pushed:
        return f"#{pushed['id']} was pushed to you"
    note = conn.execute("SELECT kind, body FROM queue_entries WHERE agent=? AND kind<>'item' AND delivered_at IS NULL "
                        "ORDER BY kind<>'stop', pos LIMIT 1", (actor,)).fetchone()
    if note:
        return f"your queue has {'a stop request' if note['kind'] == 'stop' else 'an instruction'}: {note['body']}"
    q = _queue_ready(conn, actor)
    if q:
        return f"#{q[0]['id']} in your queue is ready: {q[0]['title']}"
    try:
        nxt = next_item(conn, ",".join(names) if names else None, None, False, actor, 1)
    except RiverError:
        nxt = []
    if nxt:
        return f"#{nxt[0]['id']} is ready: {nxt[0]['title']}"
    u = unread(conn, actor)
    if u["unread"] or u["questions"]:
        return "you have messages (river inbox)"
    return None


def wait(conn, cwd, actor, project=None, step=None, sleep=None, poll=3.0):
    """Block until this agent has work (a push, a ready item in its projects, a message), for at most wait_step.

    Returns result work (run go), again (run wait again), or end: no work came within wait_max since the
    first wait, so river released the agent's goals and unregistered it, and the session should stop."""
    import time
    sleep = sleep or time.sleep
    if not actor:
        raise RiverError("waiting needs an agent name: pass --as <name>")
    _agent(conn, actor)
    names = [n.strip() for n in project.split(",") if n.strip()] if project else projects_for_dir(conn, cwd)

    def stopped():
        st = stop_request(conn, actor)
        if st:
            return {"result": "stop", "agent": actor, "stop": st, "ended": _finish_stop(conn, actor)}
    st = stopped()
    if st:
        return st
    with tx(conn):
        held = conn.execute("SELECT id FROM items WHERE assignee=? AND status IN ('in_progress','held')",
                            (actor,)).fetchall()
        if held:
            raise RiverError(f"{actor} holds {', '.join('#' + str(r['id']) for r in held)}; finish or release it "
                             f"before you wait: river --as {actor} go")
        conn.execute("UPDATE agents SET waiting_since=COALESCE(waiting_since, ?), role='waiting', waiting_in=?, "
                     "note='waiting for work (river wait)' WHERE name=?", (iso(now()), ",".join(names), actor))
    since = parse_iso(_agent(conn, actor)["waiting_since"])
    limit = parse_duration(setting(conn, "wait_max", agent=actor))
    step = parse_duration(step or setting(conn, "wait_step", agent=actor))
    deadline = min(now() + step, since + limit)
    while True:
        st = stopped()
        if st:
            return st
        why = _work_for(conn, actor, names)
        if why:
            return {"result": "work", "why": why, "agent": actor}
        if now() >= deadline:
            break
        activity(conn, actor)  # a waiting session is active: it takes work within seconds
        sleep(poll)
    if now() < since + limit:
        return {"result": "again", "agent": actor, "left": _short(since + limit - now())}
    with tx(conn):
        for g in conn.execute("SELECT name FROM goals WHERE owner=?", (actor,)).fetchall():
            conn.execute("UPDATE goals SET owner=NULL, owner_expires_at=NULL WHERE name=?", (g["name"],))
            _event(conn, None, "river", f"goal {g['name']} released: {actor} ended after waiting")
    try:
        unregister(conn, actor, "river")
    except RiverError as e:  # it owns a deploy target: keep it registered, and say so
        return {"result": "end", "agent": actor, "waited": _short(limit), "kept": str(e)}
    return {"result": "end", "agent": actor, "waited": _short(limit)}


def plan(conn, cwd, actor=None, project=None):
    """Start or continue a planner session: the overview plus the questions a planner should raise with the user."""
    names = [n.strip() for n in project.split(",") if n.strip()] if project else projects_for_dir(conn, cwd)
    for n in names:
        _project(conn, n)
    new_name = False
    if not actor:
        import secrets
        actor = f"{names[0]}-plan-{secrets.token_hex(2)}" if names else f"planner-{secrets.token_hex(2)}"
        new_name = True
    if not conn.execute("SELECT 1 FROM agents WHERE name=?", (actor,)).fetchone():
        register(conn, actor, note="role: planner")
        new_name = True
    activity(conn, actor)
    held = conn.execute("SELECT id FROM items WHERE assignee=? AND status IN ('in_progress','held')", (actor,)).fetchall()
    if held:
        raise RiverError(f"{actor} holds {', '.join('#' + str(r['id']) for r in held)}; a planner holds no work. "
                         f"Finish or release it first, or plan from a new session: river plan (without --as)")
    _set_role_note(conn, actor, "planner", None)

    ann = annotate(conn)
    live = sorted((a for a in ann.values() if not a["project_archived"] and a["status"] in OPEN_STATES),
                  key=lambda a: a["sort_key"])
    focus = [a for a in live if not names or a["project"] in names]
    brief_item = lambda a: {"id": a["id"], "project": a["project"], "title": a["title"]}
    no_notes = [a for a in focus if not a["notes"].strip() and not a["context"].strip() and a["kind"] != "deploy"]
    stuck = [dict(brief_item(a), reason=a["blocked_reason"], blocked_text=a["blocked_text"],
                  blocked_until=a["blocked_until"], holds_up=a["unblocks_count"])
             for a in focus if a["blocked_reason"]]
    stuck.sort(key=lambda x: -x["holds_up"])
    return {
        "agent": actor, "new_name": new_name, "projects": names, "role": "planner",
        "trackers": {n: setting(conn, "tracker", project_id=_project(conn, n)["id"]) for n in names},
        "cwd": cwd, "folder_has_project": bool(names) or project is not None,
        "status": status(conn),
        "questions": {
            "projects_without_description": [p["name"] for p in project_list(conn)
                                             if not p["notes"].strip() and (not names or p["name"] in names)],
            "items_without_notes": [brief_item(a) for a in no_notes],
            "human_waiting": [brief_item(a) for a in focus if a["ready"] and a["doer"] == "human"],
            "stuck": stuck,
            "replan": [dict(brief_item(a), late_prereqs=a["late_prereqs"]) for a in focus if a["replan"]],
            "suspect": [s for s in cleanup(conn) if not names or s["project"] in names],
            "due": [dict(brief_item(a), due_text=a["due_text"], due_state=a["due_state"],
                         open_before=len(a["open_blockers"])) for a in sorted(
                        (a for a in focus if a["due"]), key=lambda a: a["due"])],
        },
    }
