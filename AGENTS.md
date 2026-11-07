# Herdr Lazyagent

This repository packages a native mod for the new Herdr desktop app.
Source is under `mods/herdr-lazyagent/`. Herdr's installed
`~/.config/herdr-app/AGENTS.md` and generated type files describe its API.

- Keep the approved UI behavior and use Herdr theme colors.
- Keep public assets free of personal sessions, credentials and machine data.
- The mod runs on the app's local computer, independent of remote spaces.
- Retain cache reuse, provider backoff and the 22-second query budget.
- `bridge.py` is vendored MIT code; preserve its license and attribution.
- Run `herdr-app check mods/herdr-lazyagent` and the focused Python tests.
  Also inspect the opened dialog after UI changes.
- Do not commit generated Herdr type files, caches or usage snapshots.
- Never generate em dashes. Use concise lowercase Conventional Commits
  without Co-Authored-By trailers.
