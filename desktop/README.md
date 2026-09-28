# Biggest River desktop app

The dashboard in its own window. The app runs `river serve` on a free port on
127.0.0.1, shows the page, and stops the server when you quit.

It needs Python 3.10 or newer on the machine (it looks on PATH and in the
usual Homebrew and system places) and says so if Python is missing. It uses
the same queue as the `river` command (`RIVER_DB`, else
`~/.biggestriver/river.db`).

```
cd desktop
npm install
npm start      # run the app from this repo
npm test       # smoke test: starts river serve from the repo and stops it
```

Agents that the app starts run the `river` command. On a Mac with only the app,
the setup guide's "Let agents use river" step writes a small launcher to
`~/.local/bin/river` (it runs the app's own Python and river) and, when a new
terminal would not find it, adds `~/.local/bin` to PATH in `~/.zprofile`. A
`river` command you installed yourself (a clone or pip) is left alone.

Inside the app the page's git Update button is hidden: the server runs with
`RIVER_DESKTOP=1`, and the app gets its updates as a new release.
