# MaximizePM desktop app

The dashboard in its own window. The app runs `river serve` on a free port on
127.0.0.1, shows the page, and stops the server when you quit.

The released app carries its own Python (CPython 3.13 from
python-build-standalone, one build for Apple silicon and one for Intel), so it
needs nothing installed. Run from this repo (`npm start`), it uses Python 3.10
or newer from the machine (PATH, then the usual Homebrew and system places)
and says so if Python is missing. It uses
the same queue as the `maxpm` command (`RIVER_DB`, else
`~/.biggestriver/river.db`).

```
cd desktop
npm install
npm start      # run the app from this repo
npm test       # smoke test: starts river serve from the repo and stops it
```

Agents that the app starts run the `river` command (the same command as
`maxpm`). On a Mac with only the app, the setup guide's "Let agents use river"
step writes a small launcher to `~/.local/bin/river` and the same one to
`~/.local/bin/maxpm` (each runs the app's own Python and MaximizePM) and, when a
new terminal would not find them, adds `~/.local/bin` to PATH in `~/.zprofile`.
A `river` or `maxpm` command you installed yourself (a clone or pip) is left
alone.

Inside the app the page's git Update button is hidden: the server runs with
`RIVER_DESKTOP=1`, and the app gets its updates as a new release.

## Release

`npm run dist` builds `out/MaximizePM-<version>-arm64.dmg` and `-x64.dmg`
with electron-builder, and `npm run dist:win` builds the Windows installer
`out/MaximizePM-<version>-x64-setup.exe`. The app carries a copy of `bin/`
and `river/`, and `scripts/fetch-python.js` (run first by both; pinned release
and sha256) puts the Python for each system and chip type in
`build/python/<os>-<arch>/` (`mac-arm64`, `mac-x64`, `win-x64`). Pushing a
version tag (`git tag v0.1.0 && git push origin v0.1.0`) runs
`.github/workflows/desktop-release.yml`, which builds the `.dmg` files on macOS
and the installer on Windows, checks that each app starts river from its own
copy, and attaches all three to the GitHub Release for that tag, with
`release-notes.md` as its notes. The app is not signed with a Developer ID or
notarized yet, so macOS says it "could not verify" the app, and Windows
SmartScreen warns. `release-notes.md` tells people how to open it: Open Anyway
in System Settings > Privacy & Security on a Mac (or the `xattr` command, which
also stops the slow check before each start), More info and Run anyway on
Windows. Windows on Arm runs the x64 app.
