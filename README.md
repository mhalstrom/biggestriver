# Biggest River

Website: [biggestriver.com](https://biggestriver.com)

**In short:** you put work items into projects and say which items wait on
which, and link each project to its folder. Open an agent in that folder and
say "go": it runs `river go`, which names the session, picks a role (worker,
unblocker, planner, or idle), claims an item, and prints a briefing that ends
with the command to run when the item is done. The web page shows who holds
what and how many more sessions the ready work could use.

![The board: parallel work, next up, projects, and agents](site/img/board-tour.gif)

![A river go briefing: role, item, context, rules, and the command to run when done](site/img/go-briefing.png)

A small work queue for people and AI agent sessions that work on several
projects at the same time.

- Items belong to projects and can wait on other items, also across projects.
- Goals (optional) name an outcome in a project with a "done when" test. One
  agent owns a goal, plans and takes its items, and declares it complete;
  `river go --role owner` then takes the next free goal.
- Importance comes from the dependency graph: an item inherits the priority of
  the most important open item that waits on it.
- An agent picks the area where it already has context (a project, the items
  near one it just did, or the items that unblock it) and claims the next
  ready item there. Claims are leases that expire, so a stopped agent never
  blocks the queue for long.
- A local web page shows the board, the graph, who is doing what, and how many
  more agent sessions the ready work could use right now.

One SQLite file, one command (`river`), one page. Python 3.10 or later, standard
library only. The page ships one vendored JavaScript library,
[Tabulator](https://tabulator.info) 6.5.3 (MIT license, in
`river/static/vendor/tabulator`), so its tables work offline; the graph loads
Mermaid from a CDN. Inspired by [Beads](https://github.com/steveyegge/beads).

## Install

```sh
git clone https://github.com/mhalstrom/biggestriver
cd biggestriver
./install.sh          # links `river` into ~/.local/bin and the Claude Code skills into ~/.claude/skills
```

`./install.sh --bin-dir DIR` picks another folder; `--no-skills` skips the
skills.

Or install the command with pipx (Python 3.10 or later, no other
dependencies):

```sh
pipx install git+https://github.com/mhalstrom/biggestriver
```

The package carries the agent guides (`river guide`, `river guide planner`);
`river skills install` links them into `~/.claude/skills` (`--copy` copies
them instead). The queue database is one file per user, `~/.biggestriver/river.db`; set
`RIVER_DB` to use another file. A clone that already has `data/river.db`
keeps using it until you run `river db move`, which copies it to the home
folder (stop agent sessions and `river serve` first). `river db path` shows
the file in use. If your agents run in a sandbox, allow them to write to
`~/.biggestriver` before you move it.


## Desktop app

`desktop/` holds an Electron app that shows the page in its own window: it
runs `river serve` on a free local port and stops it on quit. It needs Python
3.10+. See `desktop/README.md`.

## Set up a project

In each project folder:

```sh
river init --description "what this project covers and what context helps"
```

This creates the project (named after the folder), links it to the folder, and
adds a short block that tells agents to run `river go` when you say "go".
Every agent reads one file: `AGENTS.md` (Codex, OpenCode, and other agents)
holds the project rules and the block, and `CLAUDE.md` is the one line
`@AGENTS.md`, which Claude Code reads as an import. When only `CLAUDE.md` holds
your rules, `river init` asks whether to move them to `AGENTS.md`
(`river init --move` or `--no-move` answers in advance). When both files hold
their own rules, river adds the block to both and says that they differ.
Running `river init` again updates an older block. Then add work and start agents:

```sh
river add <project> "first item" --doer ai
# open Claude Code (or another agent) in the folder and say: go
river serve --open    # watch the board at http://127.0.0.1:8765
```

The first time you open the page, a setup guide shows what river found (people,
the block in each project folder, skills, agents for the Start button, phone
notifications), with a button to fix each step. "Don't show again" hides it;
the Settings tab opens it again.

Every button on the page that starts an agent (Start an agent, Open agent,
Dispatch, Deploy now, Claim next with an agent) has the same agent picker next
to it when the Start button offers more than one agent. The choice is shared.
Add Codex, Grok, OpenCode, or Gemini in the setup guide when the page finds them
on this computer. The Codex command gets `--add-dir <river data folder>`, so its
sandbox can write the queue.

To see it with sample data first: `./seed/example.sh` on an empty database.
The pictures in `site/img/` come from `seed/shoot.js` (demo data from `seed/screenshots.sh`):
`cd desktop && npm ci && npx electron ../seed/shoot.js`.

## Use it

```sh
river register alex --human --note "owner"       # once per person or agent session
export RIVER_AGENT=alex                          # or pass --as alex

river target add prod-web --description "rsync to the VPS, then restart nginx"
river project add website --path ~/code/shop --target prod-web --description "Storefront pages in web/; React"
river go                                         # in ~/code/shop: name, role, item, briefing
river add website "Build the checkout page" -p 0 --doer ai --after 3 4
river next                                       # most important ready item overall
river next --project website --claim             # take one from a project
river next --near 12 --claim                     # take one linked to item 12
river next --unblocks 12 --claim                 # take one that clears item 12's blockers
river done 12 --output "merged in abc123"
river goal add website checkout --outcome "customers can pay" --done-when "a test order succeeds"
river add "Payment form" --goal checkout         # tag an item with a goal (repeatable)
river goal own checkout                          # own it: its agent items are reserved for you (goal_lease 4h)
river goal done checkout --result "live since 2026-10-02"
river blockers 12                                # tree of what item 12 waits on
river plan                                       # planner session: overview, open questions; plans, takes no work
river status                                     # every project's counts, recent completions, who works on what
river log --since 7d                             # done items by day, with output and progress per project
river who                                        # who holds what
river capacity                                   # open slots and idle sessions
```

Every command takes `--json`. Errors name the rule that refused the command and
the next command to run.

### How the order works

An item is ready when it is open, has no outside blocker, everything it
waits on is done, and no item that edits the same files (a `conflicts` link,
added when `--touches` overlap) is in progress. Inside the area an agent chooses, ready items sort by:

1. Effective priority (0 is highest): the best priority of the item and of every
   open item that waits on it.
2. Project rank (`river project rank <name> <n>`).
3. How many open items it unblocks.
4. Manual order (`river move`).
5. Age.

### Settings

`river config get` lists them. Set a value globally or for one project, agent,
item kind (`--kind`), or item; the most specific value wins.

```sh
river config set lease_ttl 45m
river config set lease_ttl 7d --agent alex       # people keep claims longer
river config set max_leases 3 --agent alex
```

`max_leases` (1) limits how many items an agent holds. Items that serve its
own outcome count apart, against `goal_max_leases` (3): items of a goal it
owns, items such an item waits on, and the deploy items of a target it owns.

### Model and effort

An item can recommend a model and an effort level, and it can set hard limits:

```sh
river add "Rewrite the scheduler" --model fable --effort high --min-model opus
river edit 12 --max-model sonnet                 # a monitor: no strong model needed
river config set default_model sonnet --project monitors
river config set default_effort low --kind deploy
RIVER_MODEL=sonnet river go                      # or: river go --model sonnet
```

The recommendation never blocks. A session that declares its model
(`RIVER_MODEL` or `--model`) gets only items whose `--min-model` and
`--max-model` allow it; `go` and `next` say what they skipped and why.
`model_ladder` orders the models, weakest first, one list per family
(`claude: sonnet, opus, fable; openai: luna, terra, sol, astra`).
There is no order across families: a limit applies only to sessions of its
own family, so give one model per family to limit both (`--min-model opus,sol`).
`effort_levels` lists the effort levels, lowest first.

An item can need one agent type: `river add "Draw the logo" --agent codex`
(or `claude-code`). A session of another type skips it; river reads a
session's type from its CLI's environment (`CODEX_THREAD_ID`, `CLAUDECODE`,
or `RIVER_AGENT_TYPE` by hand), else from its model's family. Start,
Dispatch, and `river launch` start the item's type unless you pick an agent.
`agent_rules` sets the type from the item (`codex: *.css, *.svg, image, logo`:
a pattern with `*`, `?`, `/` or `.` matches a touched file, any other a word in
the title), and `default_agent` is the fallback per project or kind. The
manager's NO AGENT finding counts ready work per type. A person can still
push an item to a session of another type.

On the page, every button that starts an agent (Start, Dispatch, Open agent,
Claim next with an agent, Deploy now) opens one dialog: the agent, the model
(only the agent's family, inside the item's limits, the recommendation
preselected), the effort, the agent's launch options, the work, and a new tab
or window. The session gets `RIVER_MODEL`. Start and Dispatch (and
`river launch`) name the new session (`RIVER_AGENT`), reserve the item for it,
and set `RIVER_FOCUS=item:<id>`: the session's first `river go` claims that
item, or says in capitals why it cannot and gives other work. A session that
runs no river command within `connect_within` (5m) shows to the manager as
NOT CONNECTED (its agent did not start, or waits on a prompt in its
terminal), and its project counts as having no agent.

Claude Code and Codex start from launch profiles: a launch_agents entry
`Claude Code=@claude-code` or `Codex=@codex`, and river builds the command
from the platform's options. Each option is a setting, so a project can
differ (`river config set claude_remote_control off --project shop`):

| Setting | Flag | Default |
|---|---|---|
| `claude_remote_control` | `--remote-control` | on |
| `claude_permission_mode` | `--permission-mode` | Claude Code's own |
| `claude_args`, `codex_args` | more arguments before the prompt | none |
| `claude_prompt`, `codex_prompt` | the first prompt | `go`; `run river go in this folder and follow the briefing` |
| `codex_sandbox` | `--sandbox` | Codex's own |
| `codex_approval` | `--ask-for-approval` | Codex's own |
| `claude_model_ids`, `codex_model_ids` | the id the CLI gets for a ladder name | none; `luna=gpt-6-luna, terra=gpt-5.6-terra, sol=gpt-6.1-sol, astra=gpt-6-astra` |

The model and effort go in as `--model`/`--effort` (Claude Code) and
`-m`/`-c model_reasoning_effort=` (Codex); Codex also gets `--add-dir` for
the queue folder. The CLI gets the model's id from `<prefix>model_ids`
(`codex -m gpt-6-astra` for `astra`); a name with no entry goes as it is,
which suits Claude Code (`claude --model fable`). Codex ids change with each
OpenAI release: then change `codex_model_ids`. River keeps the ladder name
everywhere else (limits, `RIVER_MODEL`, the page). A Codex effort that the
model does not take (from Codex's model cache, `~/.codex/models_cache.json`)
is refused before the launch. Codex has no Remote Control flag for one session. An entry
can set an option for itself (`Plan=@claude-code permission_mode=plan`), the
launch dialog can change the toggles and modes for one start, and the setup
guide edits them. Any other command is custom: it takes the dialog's choice
through `{model}` and `{effort}`; with no choice, the flag before a
placeholder drops out. river turned an older Claude Code or Codex command
that a profile builds exactly into a profile once; `river config get
launch_agents` says what changed.
From the command line, `river launch [--project P | --item N] [--agent A]
[--model M] [--effort E] [--option NAME=VALUE] [--tab|--window] [--dry-run]` does the same as the
dialog (a manager session uses it); `--dry-run` prints the project, the item,
and the command without opening a terminal.
Start with the work left at "Next" spreads sessions: first the project with
ready agent work and no agent yet whose top item is most important, and only
when every such project has an agent, the top item. Start names the new
session and reserves its item for it, so two quick clicks go to two projects.

An agent that waits on a person's item (`river add "..." --doer human --blocks
<id> --keep`) holds its own item at most `human_wait_max` (30m; set it per
project). Then river releases the item, which still waits on the person,
tells the agent to take other work, and reminds the person.

## How agents learn it

The command teaches itself: `river` with no arguments prints a quick start,
`river guide` the full work loop, `river guide planner` how to plan, and
`river guide setup` the setup steps. `river go` prints a briefing with the
role, the item, the rules, and the command to run next. `river setup-agent`
prints the instructions block alone.

After each command, river prints one hint line with the likely next command
(for example, how to finish or release the item just claimed). Hints go to
stderr, never into `--json` output; `-q` or `RIVER_QUIET=1` turns them off.

An agent that cannot run shell commands can use river through MCP:
`river mcp` is a stdio MCP server with the tools `go`, `done`, `show`,
`goal`, `inbox`, `plan`, `manage`, and `river` (any command, as a list of
words). Each tool returns the same text as the command, and the server keeps
the agent name that `go`, `plan`, or `manage` gives.
For Claude Code: `claude mcp add river -- river mcp`.

### Claude desktop app

`river setup-agent --claude-desktop` adds river to the Claude desktop app's
MCP servers (`claude_desktop_config.json`; the other servers stay, and the
old file is kept as `.bak`). Quit and reopen the app. `--remove` takes river
out again.

A chat has no folder and no shell, so river runs it as a chat session
(`RIVER_CHAT=1`, or `--chat` on `go`, `plan`, and `manage`):

- Plan: "plan my next work with river" runs `plan` over every project.
- Manage: `manage` shows what needs attention; `launch` still opens a coding
  agent in a terminal on the same computer.
- What waits on you: `needs-you`, then `prompt --all` to go through it.
- Work: `go` looks in every project and takes only items that need no folder:
  an item with `--touches`, a `--check`, or a deploy or monitor stays for a
  coding agent. The result goes into the queue: a short one in
  `done --output`, a long one in the item's notes first.

A session whose folder belongs to a project is never a chat, even with
`RIVER_CHAT=1`: it works as usual.

### Over HTTP

`river serve` also answers MCP at `http://127.0.0.1:<port>/mcp` (Streamable
HTTP, JSON replies, one session per `initialize`). Each session is a chat with
no folder and gets its own agent name. It has no sign-in yet, so it answers
only requests made on this computer straight to river serve: a request with
a proxy or tunnel header (`X-Forwarded-For`, `CF-Connecting-IP`, ...), another
`Host`, or a foreign `Origin` gets 403. Sign-in and a tunnel for claude.ai and
the phone apps come later.

### ChatGPT desktop app

`river setup-agent --chatgpt-desktop` adds river to `~/.codex/config.toml`
(`[mcp_servers.river]`; the other tables stay, and the old file is kept as
`.bak`). The ChatGPT desktop app shares that file with the Codex CLI.
Restart the app. Its Work and Codex modes run on this computer and reach
river; plain Chat mode reaches only remote servers, so it does not. In a
chat, river works as it does for the Claude desktop app (above). Codex CLI
sessions see the river server too, and in a project folder they work as
usual. `--remove` takes river out again.

## Outside trackers

River works next to an issue tracker (GitHub Issues, Jira, Linear, and
others) without any tracker code. Your agents read and update the tracker
with their own tools; river keeps the links and reminds them.

1. Give your agents access to the tracker: `gh auth login` for GitHub, a Jira
   or Linear MCP server, or a command line tool.
2. Tell river once which tracker a project uses, in words an agent can act on:

   ```sh
   river project tracker shop "github acme/shop via gh"
   river project tracker api "jira API via the Jira MCP server"
   river project tracker web "linear team WEB via the Linear MCP server"
   ```

   `river init --tracker "..."` does the same when you set up a folder.
3. Import: `river plan` names the tracker and tells the planner to add each
   open issue that river does not have yet, with a link:
   `river add "Fix login" --ref github:acme/shop#12` (GitHub links get a URL
   by themselves), or `--ref jira:API-7 --ref-url https://acme.atlassian.net/browse/API-7`.
   `river list --ref <ref>` finds the items of an issue. River refuses a
   second open item with the same link in a project.
4. Write back: `river go` asks the agent to mark linked issues in progress.
   `river done` prints each link with the output to post as a comment, and
   asks the agent to close the issue. `river synced <id>` (or
   `river done --synced`) records that. Until then, the agent's next `go` and
   `river status` show the reminder, and the page marks the link
   "tracker not updated".

## Tests

```sh
python3 -m unittest discover -s tests -t .
```

## Status

Early. Working: projects, items, dependencies (loops refused), inherited
priority, areas for `next`, atomic claims with expiring leases, outside
blockers (with `--until`, so an item comes back by itself at that time),
blocker trees, the agent registry, capacity, settings, and the web page.

- Goals: outcomes with a done-when test and one owner; items carry goal
  tags (none, one, or several). `river go` gives an owner its goal's items,
  then what blocks them, then asks whether the goal is done; it gives an
  agent a free goal only when the best ready work serves it, and
  `river go --role owner` takes the next free goal. The page shows goal cards
  with owner and progress, and filters by goal.
- `river go` roles: owner, worker, unblocker, planner, deployer, idle. After
  `done`, an agent takes the next item at once (`auto_continue`). With no
  item, it runs `river wait`, which returns when work is pushed to it or gets
  ready, and ends the session after `wait_max` (30m) without work.
- Per-item context fields (context, files it touches, check command), so a
  new agent can start without searching.
- Keep or release a claimed item when a prerequisite appears, and a
  `replan` mark when too many appear.
- Offers of help for blocked agents: `river offer`, `give`, `split`.
- Push an item to an agent: `river push`, `accept`, `decline`.
- Agent queues: `river queue add <agent> <id> [--first|--before <id>]`,
  `river queue add <agent> --message "..."`, `river queue list|remove|move`.
  An agent's queue comes before the project queue, has no expiry and no
  accept, and keeps its items from other agents. Instructions show at the top
  of its `river go`. A person or a manager changes queues; when the session is
  gone or stops, its items go back to the main queue.
- Native delivery: a queue instruction, a stop, and `river send`/`alert`/
  `ask`/`note` to an agent also go out through the agent platform's own
  messaging, so a working agent sees them at once. The `native_message`
  setting lists each platform as `Label=ENV_VAR: command`: `river go` records
  the platform whose variable is set in the session, and delivery runs the
  command with `{address}` and `{message}`. Codex is built in (`codex queue
  --thread`). For Claude Code, `uds {address} {message}` writes to the
  session's inbox socket (`CLAUDE_CODE_MESSAGING_SOCKET`); it is not on by
  default, because its message format is not documented yet. `river queue
  list` and `send` show the delivery status.
- Stop an agent: `river stop <agent> --reason "..."` puts a stop request at
  the front of its queue. A waiting agent ends at once; a working one sees it
  on its next river command, commits, releases its item, and ends. `river who`
  shows it as stopped. Remove the stop entry to withdraw it (`river queue
  remove <agent> e<id>`).
- Manager: `river manage` starts an optional manager session (one at a time;
  `--takeover "<why>"` replaces the active one) in any agent CLI. It plans
  with the user and runs the other agents: `river launch`, agent queues,
  messages, `river stop`, launch settings, and `river target give`; it takes
  no items. Its briefing lists stuck agents, expired leases, agents that wait
  longer than `wait_too_long` (20m), projects with ready work and no agent,
  targets whose owner is away, and what waits on the user. `river manage
  --watch` wakes when something new appears (at least every `manage_every`,
  5m). Its actions show in the history as "(by manager <name>)". Rules:
  `river guide manager` (skills/river-manager).
- Emergency kill: `river go`, `register`, and `heartbeat` record the agent
  CLI's process (the first ancestor of the river command that is not a shell)
  and the host; `river who` shows them, and a session whose process on this
  host has ended counts as gone at once. `river stop <agent> --kill --reason
  "..."` ends that process (SIGTERM, then SIGKILL after 5 s; taskkill on
  Windows) only on the same host and only while the PID still runs the
  recorded command, then releases its items, goals, targets, and queue.
  Uncommitted work in its folder is lost. Use it only when a stop request
  does not work.
- Agents can take over or clear a person's item, with a notice and Undo.
  Each person's item has a copyable agent prompt (`river prompt`).
- Messages between agents: alerts, questions and answers, notes, river
  notices; `river inbox`, `river thread`, an unread count on every command.
- Needs you: one list of what waits on a person, most important first, with
  notifications by phone (ntfy), email (SMTP), macOS banner, and browser.
- Planning and shipping: `river plan`, deploy targets, `river ship`.
- Deploy monitoring: `river target monitor <target> "<what to watch, for how
  long>"`. When a deploy item is claimed, river adds a monitor item for that
  release (sonnet, low effort, at most sonnet, unless settings for
  `--kind monitor` say otherwise), and the running `river serve` opens a
  session for it (`RIVER_FOCUS=monitor:<id>`). The session watches, then
  finishes the item, or alerts the deployer and adds a person's item with the
  evidence and a proposed rollback. The Targets tab shows the monitor of each deploy.
- Review before release (`river config set review on`): each release gets one
  review item that waits on everything it ships, and the deploy waits on the
  review. `review_prompt` holds your review process (for example
  `/code-review` or `codex review`); `review_cmd`, when set, must exit 0.
  The reviewer runs `river review pass <id>`, or `river review fail <id>
  "<fix>" ...`, which adds fix items the review waits on. With `--ask`, the
  release waits on the user first: one item in Needs you lists the proposed
  fixes; the user edits the list and marks it done (river adds the fixes), or
  drops it (no fixes).
- Review steps per project: an ordered list the review of each release follows,
  for every project the release ships. `river review step add <project>
  "<instruction>"` adds a written step; `--run "<command>"` adds a command that
  must exit 0 in the project folder (`--at <n>` sets the position). Also
  `river review step list [<project>]`, `edit <id> --text ... --run|--do`,
  `move <id> <n>`, and `rm <id>`. The REVIEWER brief shows the steps, and
  `river review pass <id> --confirm all` (or the step ids) confirms the written
  steps, runs the commands, and records each result in the review's history.
  `review_prompt` and `review_cmd` still apply next to the steps.
- Overviews: `river status`, and `river log` with a Done tab on the page.
- Cleanup: `river cleanup` lists open items that may be done or stale (a
  lease ran out without done, a commit names the item, a person's files
  changed, nobody took it for `stale_after`, an old notice);
  `river check <id> done|partial|open` records what a check found.

Planned: a `pipx` package, tests on GitHub Actions, and a default database
location for installed use.

## License

Apache License 2.0. See [LICENSE](LICENSE).
