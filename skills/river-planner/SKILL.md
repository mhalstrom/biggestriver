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
3. **Make each outcome a goal when an agent should own it.** A goal is an
   outcome with a done-when test (`river goal add`, below). Its owner plans
   and adjusts the items, so the items you add under it are first steps, not
   a full plan. Small or one-off work needs no goal.
4. **Split the outcome into items you can check.** Each item is one result that
   one agent or person finishes, with a `--check` command or a plain test ("the
   page loads at /pricing"). A step only the user can do (accounts, payments,
   legal, a decision) is its own `--doer human` item. For a person who is not
   in river (a client, a colleague), still use `--doer human`, and say in
   `--context` who does it and that the user records the result with `river done`.
5. **Show the plan before you write it.** A short numbered list with the
   waits-on links. Change it until the user agrees, then add it.
6. **Set priority on the outcome only** (`river prio <id> 0`). Its prerequisites
   inherit it. A new project gets the lowest rank, so equal priorities in older
   projects go first. When the new outcome must go before them, ask the user,
   then run `river project rank <name> 1`.
7. **Make a new project** only for a separate area with its own folder or
   goal (`river project add <name> --path <dir> --description "..."`).
   Otherwise add to the existing project. When `river plan` says the folder
   has no project, the projects it lists are other work: leave them alone
   unless the user names them.
8. **Report progress** from the queue, not from memory: `river status`,
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
river target monitor <target> "<what to watch, for how long>"   # a session follows each deploy
river target give <target> --to <agent>                # or: river target release <target>
```

A target has at most one owner. Ownership lasts `owner_ttl` (default 8h) and
every command by the owner renews it; when it expires the target is free and
the old owner gets a notice. `river target own` names the current owner when
it refuses. When that owner is away or gone (`away_after`, default 1h),
`river target own <target> --takeover "<why>"` moves the target at once and
tells the old owner why. A person can give any target:
`river target give <target> --to <agent>`.

`river ship <id>` (or `river done <id> --ship`) puts an item in its target's
open deploy item, in the project `deploy-<target>`. One open deploy item per
target collects requests from every project on it; once the owner claims it,
the next request starts a new one. The deploy item waits on what it ships and
takes the best priority among them.

Write the description for an agent that must decide whether it fits: what the
project covers, where it lives (repository, directories), and what knowledge
helps. Agents read `river project list` to pick an area.

### Cleanup

`river plan` lists items that may be done or stale (from `river cleanup`).
Check each one, or ask the user about a person's item, and record the result
with `river check <id> done|partial|open --note "..."`. `stale_after` (14d)
sets when an untaken ready item counts as stale.

### Outside trackers

When the user says the project uses an issue tracker (Jira, GitHub Issues,
Linear...), record it once, in words an agent can act on:

```
river project tracker <name> "github owner/repo via gh"
river project tracker <name> "jira PROJ via the Jira MCP server"
```

`river plan` then names the tracker. Import its open issues before you plan
new work:

1. Read the open issues with the tool the tracker line names (gh, a Jira or
   Linear MCP server, a command the user set up).
2. Skip each issue river has already: `river list --ref <tracker>:<key>`.
   River also refuses a second open item with the same link in a project.
3. Add the others with the link and what the issue says:
   `river add "<title>" --ref github:owner/repo#12 --context "..."`, or
   `--ref jira:PROJ-123 --ref-url <link>` (GitHub links get a URL by themselves).
4. Keep the tracker's priority (`-p 0..4`) and order, and record what must
   come first (`river dep <id> --on <id>`).

Ask the user before you import a large backlog or issues assigned to other
people. River has no tracker code: you read and write the tracker with your
own tools.

## Goals

A goal is an outcome in a project with a test for "done". One agent owns a
goal at a time; it creates and takes the items that reach it and tags them.
Goals are optional: an item can have no goal, one, or several. Goal progress
counts only the items tagged with it.

Write the outcome as what is true at the end, and done-when as a test someone
can check ("a real card payment succeeds"). Rank a project's goals so free
owners take the most important first. A business goal and the technical goals
under it can share items: tag an item with both.

```
river goal add <project> <name> --outcome "..." --done-when "..." [--rank N]
river goal list [--project P] [--all]    # open goals in order, with owner and progress
river goal show <name>                    # the goal and its items
river goal rank <name> <N>                # 1 = first among the project's goals
river goal edit <name> [--outcome] [--done-when] [--rename]
river goal own <name> / river goal release <name> / river goal give <name> --to <agent>
river goal done <name> --result "<one line>" [--drop-open]   # refused while its items are open
river goal reopen <name>
river add "<title>" --goal <name>         # repeatable; default: the goal you own in that project
river add "<title>" --no-goal             # no tag, even when you own a goal
river edit <id> --goal <name> / --untag <name>
river list --goal <name>
```

## Items

```
river add <project> "<title>" [-p 0-4] [--doer any|ai|human] [--after <id> ...] [--feeds <id> ...] [--notes "..."]
          [--context "..."] [--touches <file> ...] [--check "<command>"]
          [--model <m>] [--effort <level>] [--min-model <m>] [--max-model <m>]
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
          [--model|--effort|--min-model|--max-model <value>|none]
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

## Model and effort

Each item can recommend a model (`--model`) and an effort level (`--effort`:
one of the `effort_levels` setting, `low, medium, high, xhigh, max`). The
recommendation says which agent to start; it never keeps a session off the item.
Pick the weakest model that does the item well:

- A weaker model (sonnet, luna) and low effort: routine, well specified work.
  Monitors, checks, renames, text changes, a fix whose cause is known.
- A middle model (opus, terra or sol) and medium or high effort: normal
  feature work that follows code already there.
- The strongest model (fable, astra) and high effort or more: design with
  few examples to follow, a hard bug, security, or data that is costly to lose.

Hard limits keep sessions off an item: `--min-model opus` (weaker sessions skip
it) and `--max-model sonnet` (do not spend a strong model on it). Set a limit
only when a wrong model is costly; the recommendation is enough otherwise.
The `model_ladder` setting orders the models, weakest first, one list per
family: `claude: sonnet, opus, fable; openai: luna, terra, sol, astra`. There
is no order across families, so a limit applies only to sessions of its
family. Name one model per family to limit both: `--min-model opus,sol`.

Defaults come from settings: `default_model`, `default_effort`,
`default_min_model`, `default_max_model`, per project (`--project`) or per
item kind (`--kind deploy`). An item's own value wins. A session declares its
model with `RIVER_MODEL=<model>` or `river go --model <model>`.

## Settings

`river config get` lists every setting. `river config set <key> <value>
[--project P | --agent A | --item N | --kind K]`. The most specific value wins: item,
agent, item kind, project, global, default. For example, a person's lease:
`river config set lease_ttl 7d --agent alex`.
