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
picks your role (owner, worker, unblocker, planner, deployer, idle), claims
an item when there is one, and ends with the command to run next. Pass `--as <your-name>` on
every later command. When the briefing asks, record your Claude Code session
name once (`river --as <your-name> session <name> --ref <ref>`; ListAgents
prints `This session is <name> [<ref>]`, and names can repeat, so keep the ref), so
people and agents can message your session directly. After `river done`, run `river --as <your-name> go` again
at once, in the same turn: do not stop to report between items. When go gives
you no item (role IDLE), run `river --as <your-name> wait` (give the shell
command a 10-minute limit). It prints WORK (run go), no work yet (run wait
again), or END: no work came within `wait_max` (30m), river unregistered you,
and you stop. Stop also when you need the user; then report everything you
finished. A person turns this off with `river config set auto_continue off`.

## Identity (by hand)

Register once, then name yourself on every command:

```
river register <session-name> --note "what you are working on"
export RIVER_AGENT=<session-name>        # or pass --as <session-name>
```

A person registers with `--human`.

Declare the model your session runs, once: `river --as <you> go --model <model>`
(or `RIVER_MODEL=<model>`). River then gives you only items whose
`--min-model`/`--max-model` limits allow your model, and says what it skipped.
An item's recommended model and effort (`model:` in the briefing) are advice:
work at that effort; a different model may still take it.

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
   Done is refused while an item it waits on is open (`river blockers <id>`); close it
   anyway only with a reason, `river done <id> --force "<why>"` (never a deploy item).
   When someone adds a prerequisite to an item you hold, you get an alert: stop and wait for it.
   The change must go out, and the project has a deploy target: add `--ship`
   (or run `river ship <id>` later). The item joins that target's next
   deploy item, which only the target owner takes. The owner's `river go`
   gives it the ready deploy item first (role DEPLOYER), with what it ships;
   `river go --role deployer` also takes a free target of the folder's projects.
   When the target has a monitor text (`river target monitor`), claiming the
   deploy adds a monitor item, and `river serve` opens a session for it
   (role MONITOR): it watches, then finishes, or alerts the deployer and adds
   a person's item with a proposed rollback. It never rolls back by itself.
   With the setting `review` on, the deploy item first waits on one review
   of the whole release. `river go` gives a ready review before new work
   (role REVIEWER; `--role reviewer` takes any). Follow the review process in
   its context and the review steps the brief lists for each project, then
   `river review pass <id> --confirm all --output "<what you checked>"`
   (confirms the written steps; runs the command steps and `review_cmd`, which
   must exit 0), or
   `river review fail <id> "<fix>" ... --note "<what you found>"`: river adds
   the fixes as items the review waits on, and the review comes back after them.
   With `--ask`, the user approves the list first: river adds one item for the
   user with the proposed fixes; done adds the fixes still in its list, drop
   adds none. Findings that do not block the release are ordinary items:
   `river add "<title>" --found-during <review-id>`, then pass.

## Owning a goal

A goal is an outcome in a project, with a test for "done when". One agent owns
a goal at a time and works toward the outcome: it plans the items the goal
needs, takes them, and clears what blocks them. Goals are optional; items
without a goal stay in the normal queue.

- `river go` never forces a goal on you. It gives you a free goal (role
  OWNER) only when the most important ready work in your area serves it: the
  item carries the goal's tag, or an open item of the goal waits on it.
  `river goal own <name>` takes a goal by hand, and `river go --role owner`
  takes the highest-ranked goal nobody owns. `river goal list` shows the
  open goals, their owners, and progress.
- The briefing shows the outcome, the done-when test, the goal's open items,
  and who holds what blocks them. Then go gives you, in order: a ready item of
  the goal; an item outside the goal that unblocks it; or, when the goal has
  no open items, the question whether done-when holds.
- Plan as you go: `river add "<title>"` tags the item with the goal you own, when the item is in that goal's project.
  Add `--no-goal` for a fix you find in passing that serves no goal, or
  `--goal <name>` (repeatable) to name the goals.
- Coordinate with other owners: `river note|ask|alert --goal <name> "<text>"`
  and `river offer "<text>" --goal <name>` reach the owner of that goal.
  You get a notice when another agent adds, claims, or finishes an item of
  your goal.
- When done-when holds: `river goal done <name> --result "<one line>"`.
  River refuses while tagged items are open; finish them, untag them
  (`river edit <id> --untag <name>`), or add `--drop-open`. Then run
  `river go --role owner` to take the next free goal, or `river go` for the
  most important ready work.
- Owning a goal claims its items: while you own it, its agent items are
  reserved for you (other agents skip them and can offer help), and your
  leases on them last `goal_lease` (4h) instead of `lease_ttl`. A person's
  items in the goal stay on the person's list. Each river command renews the
  claim; `river goal own <name> --lease 6h` picks another length.
- Your goal's items, the items they wait on, and the deploy items of a target
  you own count against `goal_max_leases` (3), apart from `max_leases` (1).
  So you can take an urgent prerequisite of your goal while you hold other work.
- Stop owning: `river goal release <name>`, or `river goal give <name> --to <agent>`.
  After `goal_lease` without a command the goal is free again, its items are
  open to every agent, and you get a notice.

## Adding work

One command, no ids copied by hand. The project comes from the item you name
with `--blocks` or `--found-during`, or from the folder you are in; name it
first only when neither applies (`river add <project> "<title>"`).

- Needs to happen before your item: `river add "<title>" --blocks <your-id> --keep|--release`
- Found while working, not needed for your item: `river add "<title>" --found-during <your-id>`
  (linked both ways in `river show`; it does not block anything)
- Give the next agent a start: `--context "why, where"`, `--touches <files>`,
  `--check "<command>"`, and `--doer human` for steps only a person can do.
- The work comes from an outside tracker issue: link it with
  `--ref <tracker>:<key>` (for example `github:owner/repo#12`, `jira:PROJ-123`).
  The go briefing names the project's tracker (`river project tracker`); when
  the user names a tracker for the first time, record it there.
  When you claim an item with links, mark the issues in progress there. When
  it is done, post the output as a comment, close the issue, and record it:
  `river synced <id>` (or `river done <id> --output "..." --synced` when you
  did it first). Until then, `go` and `status` remind you.

## Items that may be done already

When a session stops mid-item, its lease runs out and the item goes back to
the queue, but the work may be partly or fully done. The go briefing then
says CHECK FIRST: read `river show <id>`, `git log --grep '#<id>'`, and the
touched files before you start. `river cleanup` lists every suspect item
(expired leases, commits that name an item, stale items, old notices).
Record what you found:

- `river check <id> done --note "<commits or files>"`: closes it.
- `river check <id> partial --note "<what is left>"`: the note goes on the item.
- `river check <id> open`: not started; it leaves the cleanup list.

Name the item in commit messages (`Fix the header (#12)`), so a check finds it.

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
- With `--keep` you hold your item while you wait for the answer, but at most
  `human_wait_max` (30m). Then river releases your item (it still waits on the
  person's item), reminds the person, and tells you to take other work:
  run `river go`. When the person finishes, the item is ready for whoever runs go.

When you put a decision to the user in chat, use one form, one decision at a
time (`river guide decisions` prints it):

```
Decision <n> of <total>: <short name>  (item #<id>)
What you decide: one sentence, as a question, with the kind of answer.
Why it matters: what it changes, and what waits on it.
Options: for each, what happens, what it costs, the risk, and whether it can change later.
My recommendation: the option, and why, in one or two sentences.
Your answer: the exact words to reply, for example "A", "yes", or "$200 a month".
```

Then stop and wait for the answer. Do not squeeze several decisions into one
table, and use the real names, amounts, and dates instead of shorthand.

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

## Your queue

A person or a manager can give you your own queue (`river queue add <you> <id>`,
`river queue add <you> --message "..."`). It comes before everything else:

- Instructions from your queue show at the top of `river go` under FROM YOUR
  QUEUE. Act on them in order, then remove each one:
  `river --as <you> queue remove <you> e<id>`.
- `river go` and `river next` give you the first ready item in your queue
  before the project queue, from any project; items that are not ready wait.
  Other agents cannot take your queued items.
- `river --as <you> queue list` shows it. `river wait` wakes you at once when
  it gets an entry.
- When your session is gone or stops, your queued items go back to the main queue.
- A message that starts with `[river instruction from ...]`, `[river stop
  request from ...]`, or `[river alert from ...]` came from river through your
  platform's own messaging. Treat it like the same entry in `river go` or
  `river inbox`: run `river --as <you> go` or `inbox` to read it in full.

## When river says STOP

A person or a manager can ask you to stop (`river stop <you> --reason "..."`).
Every river command then prints STOP REQUESTED first, `river wait` returns
STOP, and claims are refused. Then:

1. Commit the work that is finished.
2. `river done <id> --output "<commit>"` when the item is finished; else
   `river release <id> --note "<what is done, what is left>"`, or hand it on
   with `river give <id> --to <agent>`.
3. Run `river --as <you> go` once more: with nothing held it ends the session
   (river releases your goals and targets). Stop, and tell the user why.

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
- Shortcuts: `river alert <agent> "<text>" --item <id>` (work they probably need),
  `river ask <agent> "<text>"`, `river note <agent> "<text>"`. Instead of an agent:
  `--holder-of <id>` (whoever holds that item), or for `ask`, `--file <path>`
  (every agent whose held items touch it). `river note "<text>"` alone sets your status.
- An alert to you: `river accept <msg-id> --message` claims its item now, or,
  while you hold other work, keeps it reserved for you until after that.
  `river decline <msg-id> --message --note "why"` says no. The sender hears either way.
- When an item you hold waits on another one and that one is done, river sends you a notice.
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
  `river who --file <path>`: who holds an item that touches that file or directory
  (from each item's `--touches`); check it before you edit a shared file.
- `river blockers <id>`: the tree of open work an item waits on, with holders.
- `river capacity`: ready work versus active sessions.
- `river list --project <name>`: open items in order.

Add `--json` to any command for machine-readable output. Errors name the rule
and the next command to run.
