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

## When you find other work

- Something this item needs first: `river add <project> "<title>" --doer ai|human|any`
  then `river dep <your-id> --on <new-id>`. Release your item if you will not
  do the new one now.
- Something unrelated: `river add <project> "<title>" --notes "found while doing #<id>"`.
  Do not do it inside your current item.
- Waiting on something outside the queue: `river blocked <id> --reason "<what>"`.

## Looking around

- `river who`: every agent and person, and what each one holds.
- `river blockers <id>`: the tree of open work an item waits on, with holders.
- `river capacity`: ready work versus active sessions.
- `river list --project <name>`: open items in order.

Add `--json` to any command for machine-readable output. Errors name the rule
and the next command to run.
