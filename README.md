# Biggest River

**In short:** you put work items into projects and say which items wait on
which, and link each project to its folder. Open an agent in that folder and
say "go": it runs `river go`, which names the session, picks a role (worker,
unblocker, planner, or idle), claims an item, and prints a briefing that ends
with the command to run when the item is done. The web page shows who holds
what and how many more sessions the ready work could use.

A small work queue for people and AI agent sessions that work on several
projects at the same time.

- Items belong to projects and can wait on other items, also across projects.
- Importance comes from the dependency graph: an item inherits the priority of
  the most important open item that waits on it.
- An agent picks the area where it already has context (a project, the items
  near one it just did, or the items that unblock it) and claims the next
  ready item there. Claims are leases that expire, so a stopped agent never
  blocks the queue for long.
- A local web page shows the board, the graph, who is doing what, and how many
  more agent sessions the ready work could use right now.

One SQLite file, one command (`river`), one page. Python 3.10 or later, standard
library only. Inspired by [Beads](https://github.com/steveyegge/beads).

## Install

```sh
git clone https://github.com/mhalstrom/biggestriver
cd biggestriver
./install.sh          # links `river` into ~/.local/bin and the Claude Code skills into ~/.claude/skills
```

`./install.sh --bin-dir DIR` picks another folder; `--no-skills` skips the
skills. The queue database is `data/river.db` in the clone; set `RIVER_DB` to
use another file.

## Set up a project

In each project folder:

```sh
river init --description "what this project covers and what context helps"
```

This creates the project (named after the folder), links it to the folder, and
adds a short block to `CLAUDE.md` (and `AGENTS.md` if present) that tells
agents to run `river go` when you say "go". Then add work and start agents:

```sh
river add <project> "first item" --doer ai
# open Claude Code (or another agent) in the folder and say: go
river serve --open    # watch the board at http://127.0.0.1:8765
```

To see it with sample data first: `./seed/example.sh` on an empty database.

## Use it

```sh
river register alex --human --note "owner"       # once per person or agent session
export RIVER_AGENT=alex                          # or pass --as alex

river project add website --path ~/code/shop --description "Storefront pages in web/; React"
river go                                         # in ~/code/shop: name, role, item, briefing
river add website "Build the checkout page" -p 0 --doer ai --after 3 4
river next                                       # most important ready item overall
river next --project website --claim             # take one from a project
river next --near 12 --claim                     # take one linked to item 12
river next --unblocks 12 --claim                 # take one that clears item 12's blockers
river done 12 --output "merged in abc123"
river blockers 12                                # tree of what item 12 waits on
river who                                        # who holds what
river capacity                                   # open slots and idle sessions
```

Every command takes `--json`. Errors name the rule that refused the command and
the next command to run.

### How the order works

An item is ready when it is open, has no outside blocker, and everything it
waits on is done. Inside the area an agent chooses, ready items sort by:

1. Effective priority (0 is highest): the best priority of the item and of every
   open item that waits on it.
2. Project rank (`river project rank <name> <n>`).
3. How many open items it unblocks.
4. Manual order (`river move`).
5. Age.

### Settings

`river config get` lists them. Set a value globally or for one project, agent, or
item; the most specific value wins.

```sh
river config set lease_ttl 45m
river config set lease_ttl 7d --agent alex       # people keep claims longer
river config set max_leases 3 --agent alex
```

## How agents learn it

The command teaches itself: `river` with no arguments prints a quick start,
`river guide` the full work loop, `river guide planner` how to plan, and
`river guide setup` the setup steps. `river go` prints a briefing with the
role, the item, the rules, and the command to run next. `river setup-agent`
prints the instructions block alone.

After each command, river prints one hint line with the likely next command
(for example, how to finish or release the item just claimed). Hints go to
stderr, never into `--json` output; `-q` or `RIVER_QUIET=1` turns them off.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

## Status

Early. Working: projects, items, dependencies (loops refused), inherited
priority, areas for `next`, atomic claims with expiring leases, outside
blockers, blocker trees, the agent registry, capacity, settings, messages
between agents (alerts, questions and answers, notes, river notices; `river
inbox`, `river thread`, an unread count on every command), and the web page.

Planned: offers of help for blocked agents, keeping
or releasing a claimed item when new prerequisites appear, handing items to
another agent, and per-item context fields (files, check command) so a new
agent can start without searching.

## License

Apache License 2.0. See [LICENSE](LICENSE).
