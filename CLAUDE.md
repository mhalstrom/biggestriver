## Work queue

This project uses Biggest River (`river`) to track work and who is doing it.
When the user says "go" (or asks you to take work from the queue), run
`river go` in this folder and follow the briefing it prints: it names you,
gives you a role and an item, and says what to run when you finish.

## Working on Biggest River itself

- Run the tests before each commit: `python3 -m unittest discover -s tests -t .`
- Commit with explicit file paths, then push to `main` right away. The public
  repository must always match the latest finished item.
- The repository is public. Keep names, paths, and plans from other projects
  out of it; local queue data lives in `data/` and `private/`, which git ignores.
- When you finish a queue item, put the commit id in `river done <id> --output`.
