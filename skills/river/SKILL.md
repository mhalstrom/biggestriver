---
name: river
description: Take, do, and hand back work items from the Biggest River queue (the `river` command). Use when the user says "go" in a project that uses river, tells you to work from the queue, or when you finish, release, or find new work.
---

# Biggest River: working from the queue

The queue holds projects, items, and the dependencies between them. You pick
the area where you already hold context; inside it, `river next` returns the
most important item that is ready (nothing it waits on is open).

## Fastest start

Run `river go` in the project folder and follow the briefing. It names you,
picks your role (worker, unblocker, planner, idle), claims an item when there
is one, and ends with the command to run next. Pass `--as <your-name>` on
every later command. After `river done`, run `river --as <your-name> go` again.

## Identity (by hand)

Register once, then name yourself on every command:

```
river register <session-name> --note "what you are working on"
export RIVER_AGENT=<session-name>        # or pass --as <session-name>
```

A person registers with `--human`.

## Work loop

1. Choose your area and take an item. `river project list` shows what each
   project covers; `river project show <name>` shows who works there and what
   is ready.
   - `river next --project <name> --claim`: the project you know
   - `river next --mine --claim`: next to what you claimed or finished before
     (linked items first, then the same projects)
   - `river next --near <id> --claim`: items linked to one you just worked on, closest first
   - `river next --unblocks <id> --claim`: something that unblocks your blocked item
   - `river next --claim`: anything, most important first
   Leave out `--claim` to look first. Add `-n 5` to see five.
2. `river show <id>` for the notes, what it waits on, and what it unblocks.
3. Do the work. Every `river` command renews your lease. If you work a long
   time without one, run `river heartbeat` (default lease 30 minutes).
4. Finish: `river done <id> --output "<one line: what changed, commit id>"`.
   The reply lists items that became ready.
   Cannot finish: `river release <id> --note "<why>"`.
   The change must go out, and the project has a deploy target: add `--ship`
   (or run `river ship <id>` later). The item joins that target's next
   deploy item, which only the target owner takes.

## When you find other work

- Something this item needs first: `river add <project> "<title>" --blocks <your-id>`
  with one of:
  - `--keep`: it is small and you do it now. Your item becomes `held`, still
    yours; the new item is reserved for you; when it is done your item goes
    back to in progress. A hold lasts `hold_ttl` (2h), renewed by your
    commands. More than `keep_prereq_limit` (3) open prerequisites: river
    releases instead and marks the item `replan`.
  - `--release` (the default): it is large or better for someone else. Your
    item goes back to the queue, waiting on the new one. Run `river go` again.
  `river keep <id>` holds a released item again; `river release <id>` ends a hold.
- Something unrelated: `river add <project> "<title>" --notes "found while doing #<id>"`.
  Do not do it inside your current item.
- Waiting on something outside the queue: `river blocked <id> --reason "<what>"`.

## Messages

Every command ends with a line such as
`[you: holds #12 20m left; inbox: 2 unread, 1 question to answer (river --as you inbox)]`.
When you see it, read your inbox before you continue.

- `river inbox`: unread messages and questions that wait for your answer.
  Add `--all` for read ones, `--peek` to leave them unread.
- `river answer <msg-id> "<text>"`: answer a question. The asker gets it.
- `river send question|alert|note "<text>" --to <agent>`: to one agent.
  Use `--item <id>` instead of `--to` to reach whoever holds that item
  (or the next holder, if nobody holds it). `--reply <msg-id>` replies to
  the sender of that message.
- `river thread <msg-id>`: the whole conversation; `river thread --item <id>`:
  every message about an item.

An alert means stop and read now. A notice comes from river itself, for
example when your lease expired.

## Looking around

- `river who`: every agent and person, and what each one holds.
- `river blockers <id>`: the tree of open work an item waits on, with holders.
- `river capacity`: ready work versus active sessions.
- `river list --project <name>`: open items in order.

Add `--json` to any command for machine-readable output. Errors name the rule
and the next command to run.
