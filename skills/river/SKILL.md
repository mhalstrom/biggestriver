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
   deploy item, which only the target owner takes. The owner's `river go`
   gives it the ready deploy item first (role DEPLOYER), with what it ships;
   `river go --role deployer` also takes a free target of the folder's projects.

## Adding work

One command, no ids copied by hand. The project comes from the item you name
with `--blocks` or `--found-during`, or from the folder you are in; name it
first only when neither applies (`river add <project> "<title>"`).

- Needs to happen before your item: `river add "<title>" --blocks <your-id> --keep|--release`
- Found while working, not needed for your item: `river add "<title>" --found-during <your-id>`
  (linked both ways in `river show`; it does not block anything)
- Give the next agent a start: `--context "why, where"`, `--touches <files>`,
  `--check "<command>"`, and `--doer human` for steps only a person can do.

## When you find other work

- Something this item needs first: `river add <project> "<title>" --blocks <your-id>`
  with one of:
  - `--keep`: it is small and you do it now. Your item becomes `held`, still
    yours; the new item is reserved for you; when it is done your item goes
    back to in progress. A hold lasts `hold_ttl` (2h), renewed by your
    commands. More than `keep_prereq_limit` (3) open prerequisites: river
    releases instead and marks the item `replan`.
    After `replan_threshold` (3) prerequisites are added to a claimed item,
    river also marks it `replan`, and `river plan` lists it for a planner.
  - `--release` (the default): it is large or better for someone else. Your
    item goes back to the queue, waiting on the new one. Run `river go` again.
  `river keep <id>` holds a released item again; `river release <id>` ends a hold.
- Something unrelated: `river add "<title>" --found-during <id>`.
  Do not do it inside your current item.
- Waiting on something outside the queue: `river blocked <id> --reason "<what>"`.
  Add `--until <time>` (`2h`, `2026-09-28T07:00`, `'mon 07:00 America/New_York'`)
  when you know when it ends: the item becomes ready by itself then.

## When you need the user

Anything only the user can do or decide (a decision, an approval, an account,
payment, or legal step) goes in the queue as a human item, not only in chat.
The queue is what notifies them (`river needs-you`, phone, mail), and it keeps
the step visible after the chat ends.

```
river add "Approve the refund policy draft" --doer human --blocks <your-id> \
  --context "Five decisions at the end of docs/legal/refund-draft.md; answer each yes/no"
```

- Say exactly what to decide or do and where the material is, so the user can
  act without asking you.
- Link it: `--blocks <id>` when your item waits on it (add `--release` and run
  `river go` again if you cannot continue), `--found-during <id>` otherwise.
- A short question that needs no item: `river send question --to <person> "..." --item <id>`.
  The go briefing lists the people by name.
- Then tell the user in chat too, with the item id.

The other way round: you can do a person's item yourself, or work around it.
Take it off their list, with the reason; they get one notice and can undo it:

- `river takeover <id> --note "<how you will do it>"`: it becomes your item.
- `river done <id> --note "<why it is no longer needed>"` or `river drop <id> --note "..."`.

`river claim` refuses a person's item for an agent, so the user is never
bypassed without a notice.

## Blocked on another agent's item

`river blockers <id>` shows who holds what. Then:

- Ready pieces nobody holds: `river next --unblocks <id> --claim`.
- The useful work is held: `river offer "I am blocked on this; I can take ..." --item <their-id>`.
  The holder answers with `river give <id> --to <you>` (the lease moves to you),
  `river split <id> "<smaller piece>" ...` (new prerequisites anyone can take;
  their item waits for them, still theirs), or
  `river decline <msg-id> --message --note "why"`.

## Pushed items

Someone can push an item to you (`river push <id> --to <you> --note "..."`):
you get an alert, the item is reserved for you for `reserve_ttl` (2h), and
`river go` and `river next` give it to you first. `river accept <id>` takes it;
`river decline <id> --note "why"` hands it back and tells the pusher. With no
answer the push expires and the item is open to everyone again.

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
