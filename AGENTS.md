# Working on this repo with a coding agent

This file is the same in every Corgi repo. It is for coding agents and for people who drive one. Read
[CONTRIBUTING.md](CONTRIBUTING.md) first; this file adds how to build, test and show a change.

## Know Herdr first

Read `~/.config/herdr-app/AGENTS.md`. Herdr writes it on every start, and it is the reference for layouts, mods,
`herdr-app do`, `herdr-app check` and `herdr-app shot`. Then read `RECIPE.md` in this repo, especially "Watch out".

## Never work in the person's own Herdr

Test in a second window with its own home and its own core:

```sh
H="$PWD/demo/home"     # or the home the repo's demo/setup.sh prints
cd "$H" && env -i HOME="$HOME" USER="$USER" PATH="$PATH" SHELL=/bin/zsh LANG=en_US.UTF-8 TERM=xterm-256color \
  HERDR_APP_HOME="$H" HC_SESSION=corgi-test nohup herdr-app >/dev/null 2>&1 &
```

- `HC_SESSION` gives the window its own core, so the person's spaces and agents never show up. Use one name per home.
- `env -i` keeps your own agent's environment out of the agents in the test window.
- Every `herdr-app` call carries the same `HERDR_APP_HOME`, every `hc` call the same `HC_SESSION`. A call without
  them talks to the person's window.
- If this repo has `demo/setup.sh`, run it first. It builds the home and a repo with invented data.
- Bring your code in by copying the changed files to `<home>/mods/<name>/`, never to `~/.config/herdr-app/mods/`.
  `HERDR_APP_HOME="$H" herdr-app status` shows in `errors` whether the mod loaded.
- Set `"dryRun": true` in the mod's config in that home. Nothing may post, push or open an editor while you test.
- Commands that copy text write to the person's real clipboard, also from a test window. Test the function that
  builds the text instead.
- Model calls cost money. Start them only when the person agreed; otherwise work from the fixtures and cached data.
- No `pkill`, no simulated mouse or keyboard. Stop only your window: `HERDR_APP_HOME="$H" herdr-app do app.quit`,
  then `HC_SESSION=corgi-test hc server stop`.

## The loop

1. Write or change a test first. Tests sit next to the code as `test_*.mjs` and `test_*.py`; fixtures are in
   `demo/fixtures/`.
2. Change the code.
3. `python3 .github/scripts/check.py`: tests, syntax check and leak scan, exactly as CI runs them.
4. `herdr-app check mods/<name>`: loads the mod once and reports what's wrong. It writes `gpui-kit.d.ts` and
   `jsconfig.json` into that folder; they are ignored by git and must never be committed.
5. Try it in the test window. Drive it with the mod's own commands, `herdr-app do <mod>.<command>`. If no command
   reaches a state, add one that does exactly what the click does.
6. Before you call it done: cached data without your new fields still loads, and the screen you changed is not
   fuller, slower to read or at odds with what sits next to it.

In a long file, find a place by grepping for the text the UI shows, and follow the data all the way before you pick
the smallest change.

## Things that look like bugs but aren't

- A test window that is covered or minimized draws no frames. `render()` runs at the next `herdr-app shot`. Take a
  throwaway shot after each state change, and two in a row for a toast.
- In `init()`, `layout()` has no tree yet. Check whether the mod is hidden in the first `render()`.
- After many reloads, restart the test window before you trust a memory or speed measurement.
- A render that takes more than about 50 ms is cut off, and every program start costs about a third of a second.

## User-facing text

English, plain full sentences, no slogans.

## Screenshots for a pull request

1. Bring the test window into the state you want to show, with the demo data, using `herdr-app do`.
2. Take a throwaway shot, then the real one: `HERDR_APP_HOME="$H" herdr-app shot before.png`.
3. Make the change, reload, and take `after.png` the same way.
4. Look at every image before you use it. It must show invented data only: no real repo, path, name or session.
5. If the change alters a screenshot the README shows, replace that file in `docs/screenshots/` in the same pull
   request. Other images go into the pull request description, not into the repo.

## The pull request

- Fill `.github/pull_request_template.md`: what and why, how you tried it, screenshots, the checklist. With the
  GitHub CLI: `gh pr create --title "feat(scope): …" --body-file <filled template>`.
- `gh` cannot attach images. Ask the person to drag `before.png` and `after.png` into the description, or link the
  files you changed under `docs/screenshots/`.
- One topic per pull request, Conventional Commits, and leave the version and `CHANGELOG.md` alone.
