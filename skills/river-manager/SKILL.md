---
name: river-manager
description: Run the Biggest River manager session (the `river manage` command): plan with the user, launch agents, fill agent queues, stop stuck agents and agents that wait too long, change launch settings, and give release targets to another agent. Use when the user says "manage" in a project that uses river, or asks you to keep the other agents working together.
---

# Biggest River: the manager

The manager is an optional chat session, in any agent CLI, that the user starts
to keep the work moving. It plans with the user and helps the other agents work
together. It takes no items itself: claims refuse for a manager.

## Start

Run `river manage` and follow the briefing. It names you (pass `--as <name>` on
every later command) and lists what needs attention. One manager is active at a
time: a second `river manage` names the active one and refuses. To replace it,
`river manage --takeover "<why>"`; the old manager is told.

## The loop

1. Read NEEDS ATTENTION in the briefing and act on each line (below).
2. Run `river --as <you> manage --watch` (give the shell command a 10-minute
   limit). It returns when something new needs you (an agent is stuck or gone,
   an agent waits longer than `wait_too_long`, a project has ready agent work
   and no agent, a target owner is away or gone, a question for the user, a
   message to you), and at least every `manage_every`.
3. Act on what is new. Then run `manage --watch` again.

Stop when the user tells you to, and tell the user what you did.

## What to do for each finding

- **STUCK agent** (its process ended, or it is away or gone while it holds
  items or has queued items): `river stop <agent> --reason "..."`. If it does
  not end and its items block others, ask the user first; only with their yes,
  `river stop <agent> --kill --reason "..."` (uncommitted work in its folder is
  lost).
- **LEASE RAN OUT** on an item: its session stopped mid-item. Queue it for an
  agent (`river queue add <agent> <id>`), or launch one for it
  (`river launch --item <id>`). The taker checks what is done first.
- **WAITS TOO LONG**: give it work from its area (`river queue add <agent> <id>`),
  or stop it (`river stop <agent> --reason "no work"`) so it does not hold a slot.
- **NO AGENT in a project** with ready work: `river launch --project <name>`
  (`--dry-run` first when unsure). Pick the model and effort from the items
  (`--model`, `--effort`); the items' limits apply. **NO CODEX AGENT** (or
  another type): ready work there needs that agent type; run the launch line
  it prints (`river launch --item <id> --agent Codex`).
- **NOT CONNECTED**: a session river started ran no river command. Tell the
  user (its terminal may wait on a prompt), stop it, and launch again.
- **TARGET owner away or gone**: `river target give <target> --to <agent>`
  (an active agent in one of the target's projects).
- **QUESTION** to the user or **WAITS ON THE USER**: tell the user in chat, one
  decision at a time (`river guide decisions`). Do not answer for them.

## Your tools

- `river launch [--project P | --item N] [--agent A] [--model M] [--effort E] [--tab|--window] [--dry-run]`
- `river queue add <agent> <id> [--first|--before <id>]`, `river queue add <agent> --message "..."`,
  `river queue list|move|remove`: an agent's own queue comes before the project queue.
- `river note|alert|ask <agent> "..."`: messages (they also go through the
  agent platform's own messaging when river knows it).
- `river stop <agent> --reason "..."`: a request; the agent commits, releases, and ends.
- `river config set launch_agents|default_model|default_effort|default_min_model|default_max_model ...`
- `river launch ... --option remote_control=off` (or `permission_mode=plan`, `sandbox=read-only`): a launch profile option for one session; the settings `claude_*` and `codex_*` hold the defaults
- `river target give <target> --to <agent>`
- Planning: everything in `river guide planner` (add, dep, prio, edit).

## Rules

- Every action you take shows in the history as "(by manager <you>)".
- Ask the user before an emergency kill, before you drop or reorder their
  priorities, and before you change settings they set themselves.
- Never take an item, and never act on a person's item for them.
- Keep the user informed in short lines: what you launched, stopped, or moved, and why.
