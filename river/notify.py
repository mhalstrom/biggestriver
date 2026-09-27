"""Notification dispatcher: sends the needs-you outbox through channel adapters.

A channel adapter is a function make(conn) -> send(title, body, url); send
raises on failure. Register one with register_channel(name, make). The
dispatcher groups pending rows per channel, waits notify_batch_window after
the oldest one so events close together go out as one message, then marks
every row sent or failed (failed rows retry, see core.NOTIFY_MAX_ATTEMPTS).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import core
from .core import RiverError

ADAPTERS = {}
SERVE_PORT = {"port": None}  # set by river serve, so links point at the page that is running


def register_channel(name, make):
    ADAPTERS[name] = make


def _log_channel(conn):
    """Writes one JSON line per message next to the database. For tests and for checking the setup."""
    path = Path(core.db_path()).with_name("notifications.log")

    def send(title, body, url):
        with path.open("a") as f:
            f.write(json.dumps({"at": core.iso(core.now()), "title": title, "body": body, "url": url}) + "\n")
    return send


register_channel("log", _log_channel)


def page_url(conn, item_id=None):
    base = f"http://127.0.0.1:{SERVE_PORT['port'] or core.setting(conn, 'serve_port')}/"
    return base + (f"#item-{item_id}" if item_id else "")


def compose(conn, rows):
    """One message for a batch of outbox rows on one channel."""
    if len(rows) == 1:
        r = rows[0]
        return "River: needs you", r["summary"], page_url(conn, r["item_id"])
    lines = [f"- {r['summary']}" for r in rows[:10]]
    if len(rows) > 10:
        lines.append(f"- and {len(rows) - 10} more")
    return f"River: {len(rows)} things need you", "\n".join(lines), page_url(conn)


def _adapter(conn, channel):
    make = ADAPTERS.get(channel)
    if not make:
        raise RiverError(f"no adapter for channel {channel!r}; known: {', '.join(sorted(ADAPTERS)) or '(none)'}")
    return make(conn)


def run(conn, now_=False):
    """Send every channel batch that is due. Returns one result per channel batch it tried."""
    with core.tx(conn):
        core.sync_needs_you(conn)
    window = core.parse_duration(core.setting(conn, "notify_batch_window"))
    t = core.now()
    by_channel = {}
    for r in core.outbox(conn):
        by_channel.setdefault(r["channel"], []).append(r)
    results = []
    for channel, rows in sorted(by_channel.items()):
        oldest = min(core.parse_iso(r["created_at"]) for r in rows)
        if not now_ and t - oldest < window:
            results.append({"channel": channel, "rows": len(rows), "sent": False,
                            "waiting": core._short(window - (t - oldest))})
            continue
        title, body, url = compose(conn, rows)
        try:
            _adapter(conn, channel)(title, body, url)
        except Exception as e:  # any adapter failure is recorded and retried, never raised to the caller
            for r in rows:
                core.outbox_mark(conn, r["id"], False, f"{type(e).__name__}: {e}")
            results.append({"channel": channel, "rows": len(rows), "sent": False, "error": str(e)})
        else:
            for r in rows:
                core.outbox_mark(conn, r["id"], True)
            results.append({"channel": channel, "rows": len(rows), "sent": True, "title": title})
    return results


def test(conn, channel):
    """Send a test message on one channel now, outside the outbox."""
    try:
        _adapter(conn, channel)("River: test", "This is a test notification from river notify test.", page_url(conn))
    except RiverError:
        raise
    except Exception as e:
        return {"channel": channel, "ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"channel": channel, "ok": True}


def status(conn):
    configured = core._channels(core.setting(conn, "notify_channels"))
    names = sorted(set(configured) | {r[0] for r in conn.execute("SELECT DISTINCT channel FROM notifications")})
    out = []
    for ch in names:
        last_sent = conn.execute("SELECT MAX(sent_at) FROM notifications WHERE channel=? AND state='sent'", (ch,)).fetchone()[0]
        err = conn.execute("SELECT last_error, attempts FROM notifications WHERE channel=? AND state='failed' "
                           "ORDER BY id DESC LIMIT 1", (ch,)).fetchone()
        out.append({
            "channel": ch, "configured": ch in configured, "adapter": ch in ADAPTERS,
            "pending": sum(1 for r in core.outbox(conn, ch)),
            "last_sent": last_sent,
            "last_error": err["last_error"] if err else None,
        })
    return {"channels": out, "interval": core.setting(conn, "notify_interval"),
            "batch_window": core.setting(conn, "notify_batch_window")}


def loop(stop, interval_s=None):
    """Run the dispatcher until stop (a threading.Event) is set. For river serve and river notify run."""
    while not stop.is_set():
        wait = interval_s or 30
        conn = core.connect()
        try:
            wait = interval_s or core.parse_duration(core.setting(conn, "notify_interval")).total_seconds()
            if core._channels(core.setting(conn, "notify_channels")):
                run(conn)
        except Exception as e:  # keep the loop alive; the next pass retries
            print(f"river notify: {e}", flush=True)
        finally:
            conn.close()
        stop.wait(max(1.0, wait))

