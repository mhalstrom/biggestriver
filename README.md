# Biggest River

**In short:** you put work items into projects and say which items wait on
which. Each person or agent session registers a name, picks the area it knows,
and runs `river next --claim` to take the most important item that is ready
there. When it finishes, it runs `river done`. The web page shows who holds
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

## Try it

```sh
git clone https://github.com/mhalstrom/biggestriver
cd biggestriver
./seed/example.sh              # a small example on a fresh database
./bin/river serve --open       # http://127.0.0.1:8765
```

Put `bin/river` on your `PATH` (for example `ln -s "$PWD/bin/river" ~/.local/bin/river`).
The database is `data/river.db`; set `RIVER_DB` to use another file.

## Use it

```sh
river register alex --human --note "owner"       # once per person or agent session
export RIVER_AGENT=alex                          # or pass --as alex

river project add website
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

## Set up your agents

Agents learn river from the command itself. Run `river` with no arguments for
the quick start, `river guide` for the full work loop, and `river guide setup`
for these steps.

1. Put `bin/river` on `PATH`.
2. Add a short block to the instructions file your agent reads (`CLAUDE.md`
   for Claude Code, `AGENTS.md` for Codex and others):

   ```sh
   river setup-agent --append CLAUDE.md     # or: river setup-agent  (prints the block)
   ```

   The block tells the agent to run `river guide` once and then work from the
   queue.
3. Give each agent session its own name: it runs `river register <name>` and
   sets `RIVER_AGENT`.
4. Optional, Claude Code: install the skills so they load when needed.

   ```sh
   ln -s "$PWD/skills/river" ~/.claude/skills/river
   ln -s "$PWD/skills/river-planner" ~/.claude/skills/river-planner
   ```

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
blockers, blocker trees, the agent registry, capacity, settings, and the web
page.

Planned: messages between agents (alerts, questions, offers of help), keeping
or releasing a claimed item when new prerequisites appear, handing items to
another agent, and per-item context fields (files, check command) so a new
agent can start without searching.

## License

Apache License 2.0. See [LICENSE](LICENSE).
