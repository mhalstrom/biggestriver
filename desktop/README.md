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

Inside the app the page's git Update button is hidden: the server runs with
`RIVER_DESKTOP=1`, and the app gets its updates as a new release.
