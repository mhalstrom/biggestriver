---
name: river-planner
description: Split work into Biggest River queue items with dependencies and priorities (the `river` command). Use when you plan a project into tasks, load a checklist into the queue, re-rank projects, or fix priorities and dependencies.
---

# Biggest River: planning work into the queue

## Projects

```
river project add <name> [--rank N] --description "..."   # lower-case name
river project describe <name> "..."
river project show <name>
river project rank <name> <N>                          # 1 = most important overall
river project list
```

Write the description for an agent that must decide whether it fits: what the
project covers, where it lives (repository, directories), and what knowledge
helps. Agents read `river project list` to pick an area.

## Items

```
river add <project> "<title>" [-p 0-4] [--doer any|ai|human] [--after <id> ...] [--notes "..."]
river dep <id> --on <id> ...        # <id> waits on the others
river undep <id> --on <id> ...
river prio <id> <0-4>               # 0 is most important
river move <id> --before|--after <id>   # manual order inside a project
river edit <id> [--title] [--notes] [--doer] [--project]
river blocked <id> --reason "..." / river unblock <id>
river drop <id> / river reopen <id>
```

## How the order works

An item is ready when it is open, has no outside blocker, and everything it
waits on is done or dropped. Ready items sort by:

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
- Put the facts an agent needs in `--notes`: files and line ranges, commands,
  decisions already made, and how to know it is done. Another agent should be
  able to start without searching.
- Mark who can do it: `--doer human` for account, legal, and payment steps;
  `--doer ai` for code and text work; `any` otherwise.
- Record every "needs first" as a dependency. The tool refuses loops.

## Settings

`river config get` lists every setting. `river config set <key> <value>
[--project P | --agent A | --item N]`. The most specific value wins: item,
agent, project, global, default. For example, a person's lease:
`river config set lease_ttl 7d --agent alex`.
