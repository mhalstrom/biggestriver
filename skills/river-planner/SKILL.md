---
name: river-planner
description: Plan work with the user into Biggest River queue items with dependencies and priorities (the `river` command). Use when the user says "plan" in a project that uses river, when you plan a project into tasks, load a checklist into the queue, re-rank projects, or fix priorities and dependencies.
---

# Biggest River: planning work into the queue

## The planning conversation

When the user says "plan", run `river plan` in the project folder. It names
you, makes you a planner (you change the plan; claims refuse), and prints the
overview and the open questions. Then talk with the user before you add
anything:

1. **Ask for the outcome first.** "What must be true when this is done, and by
   when?" One or two sentences. Do not add items until you have it. Put the
   date on the outcome item: `--due 2026-10-15` (end of that day) or
   `--due "fri 17:00 America/New_York"`. Its prerequisites show the date too.
   A due date does not change the order; river warns each person when it is
   `due_warn_before` (3d) away and again when it passes.
2. **Ask only what changes the plan.** Scope, deadline, who does which steps
   (agents or the user), what already exists, and what must not change. Take
   the open questions from the briefing that bear on this outcome; skip the
   rest.
3. **Split the outcome into items you can check.** Each item is one result that
   one agent or person finishes, with a `--check` command or a plain test ("the
   page loads at /pricing"). A step only the user can do (accounts, payments,
   legal, a decision) is its own `--doer human` item. For a person who is not
   in river (a client, a colleague), still use `--doer human`, and say in
   `--context` who does it and that the user records the result with `river done`.
4. **Show the plan before you write it.** A short numbered list with the
   waits-on links. Change it until the user agrees, then add it.
5. **Set priority on the outcome only** (`river prio <id> 0`). Its prerequisites
   inherit it. A new project gets the lowest rank, so equal priorities in older
   projects go first. When the new outcome must go before them, ask the user,
   then run `river project rank <name> 1`.
6. **Make a new project** only for a separate area with its own folder or
   goal (`river project add <name> --path <dir> --description "..."`).
   Otherwise add to the existing project. When `river plan` says the folder
   has no project, the projects it lists are other work: leave them alone
   unless the user names them.
7. **Report progress** from the queue, not from memory: `river status`,
   `river log --since 7d`, `river blockers <id>`.

End by saying what is ready now, what waits on the user, and that `river go`
in an agent session starts the work.

## Projects

```
river project add <name> [--rank N] --description "..."   # lower-case name
river project describe <name> "..."
river project show <name>
river project rank <name> <N>                          # 1 = most important overall
river project list
river target add <name> --description "how it deploys"  # where projects ship to
river project target <name> <target>                   # each project has at most one
river target show <target>                             # its projects and owner
river target own <target>                              # one owner per target runs its deploys
river target give <target> --to <agent>                # or: river target release <target>
```

A target has at most one owner. Ownership lasts `owner_ttl` (default 8h) and
every command by the owner renews it; when it expires the target is free and
the old owner gets a notice. `river target own` names the current owner when
it refuses.

`river ship <id>` (or `river done <id> --ship`) puts an item in its target's
open deploy item, in the project `deploy-<target>`. One open deploy item per
target collects requests from every project on it; once the owner claims it,
the next request starts a new one. The deploy item waits on what it ships and
takes the best priority among them.

Write the description for an agent that must decide whether it fits: what the
project covers, where it lives (repository, directories), and what knowledge
helps. Agents read `river project list` to pick an area.

## Items

```
river add <project> "<title>" [-p 0-4] [--doer any|ai|human] [--after <id> ...] [--feeds <id> ...] [--notes "..."]
          [--context "..."] [--touches <file> ...] [--check "<command>"]
river add [project] --from plan.md [--dry-run]   # one item per line of an outline: a line waits on
                                    # the lines indented under it; 'P0' at the start and '(human)' or
                                    # '(ai)' at the end set priority and doer; '[x]' lines are skipped
river dep <id> --on <id> ...        # <id> waits on the others
river dep <id> --on <id> --kind feeds      # waits, then reads their output in its context
                                           # (changes an existing --after link to feeds; show marks it)
river dep <id> --on <id> --kind conflicts  # no order, never in progress together
river undep <id> --on <id> ...
river prio <id> <0-4>               # 0 is most important
river move <id> --before|--after <id>   # manual order inside a project
river edit <id> [--title] [--notes] [--doer] [--project] [--context] [--touches ...] [--check] [--due <date>|none]
river blocked <id> --reason "..." [--until "mon 07:00 America/New_York"] / river unblock <id>
river drop <id> / river reopen <id>
river replanned <id> [--note "..."]   # clear the replan mark after you split or re-scope the item
```

## How the order works

An item is ready when it is open, has no outside blocker, everything it
waits on is done or dropped, and no item it conflicts with is in progress.
River adds a conflict by itself when two open items' `--touches` overlap (the
same file, or a directory and a file in it); change the touches and river
removes it. Use `feeds` when the later item needs a name, path, or signature
that the earlier item creates: the earlier item's `--output` shows in the
later item's briefing. Ready items sort by:

1. Effective priority: the best priority of the item and of every open item
   that waits on it, directly or indirectly. A P3 task that blocks a P0 task
   is P0.
2. Project rank.
3. How many open items it unblocks.
4. Manual order (`river move`).
5. Age.

So set a high priority only on the outcome you care about (for example, "submit
Stripe activation" P0); its prerequisites inherit it. Do not raise every step.

## Writing good items

- One outcome per item that one agent or person can finish and check.
- Give an agent what it needs to start without searching. `--context`: why the
  item exists, where to look, decisions already made. `--touches`: the files
  it changes. `--check`: the command that shows it works. `river next` and
  `river go` print these three in one block. Use `--notes` for anything else.
- Mark who can do it: `--doer human` for account, legal, and payment steps;
  `--doer ai` for code and text work; `any` otherwise.
- Record every "needs first" as a dependency. The tool refuses loops.

## Settings

`river config get` lists every setting. `river config set <key> <value>
[--project P | --agent A | --item N]`. The most specific value wins: item,
agent, project, global, default. For example, a person's lease:
`river config set lease_ttl 7d --agent alex`.
