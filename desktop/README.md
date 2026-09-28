# Biggest River desktop app

The dashboard in its own window. The app runs `river serve` on a free port on
127.0.0.1, shows the page, and stops the server when you quit.

The released app carries its own Python (CPython 3.13 from
python-build-standalone, one build for Apple silicon and one for Intel), so it
needs nothing installed. Run from this repo (`npm start`), it uses Python 3.10
or newer from the machine (PATH, then the usual Homebrew and system places)
and says so if Python is missing. It uses
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

## Release

`npm run dist` builds `out/Biggest-River-<version>-arm64.dmg` and `-x64.dmg`
with electron-builder; the app carries a copy of `bin/` and `river/`, and
`scripts/fetch-python.js` (run first by `npm run dist`; pinned release and
sha256) puts the Python for each chip type in `build/python/<arch>/`. Pushing a
version tag (`git tag v0.1.0 && git push origin v0.1.0`) runs
`.github/workflows/desktop-release.yml`, which builds both on macOS, checks
that the app starts river from its own copy, and attaches the `.dmg` files to
the GitHub Release for that tag. The app is not signed or notarized yet, so
the first time people open it once, then click Open Anyway in System Settings >
Privacy & Security (macOS 15 and later), or right-click it and choose Open
(macOS 14 and older).
