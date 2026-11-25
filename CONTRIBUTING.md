# Contributing

Thanks for helping. This file is the same in every Corgi repo.

## Before you start

- For anything bigger than a fix, open an issue first and say what you want to change. That saves you work if the
  idea doesn't fit.
- One topic per pull request. A new feature and an unrelated fix go in two pull requests.
- Read `RECIPE.md` in this repo, especially "Watch out". It lists the limits a mod runs into in Herdr.
- If your change touches how mods talk to each other, read
  [CONVENTIONS.md](https://github.com/saefty/herdr-corgi/blob/main/CONVENTIONS.md).

## Try it without touching your own Herdr

- Run a second Herdr window on its own home: `HERDR_APP_HOME="$PWD/demo/home"`. If this repo has a `demo/` folder,
  its README section "Demo" builds that home with invented data.
- Turn on dry runs. Nothing should post, push or open your editor while you test: `"dryRun": true` in the mod's
  config, or an `editor.dry-run` file where the README says so.
- Take screenshots from the demo, never from real repos, work projects or your own sessions.

## Tests

`python3 .github/scripts/check.py` runs everything CI runs: the unit tests, a syntax check, and a scan for personal
paths, tokens and e-mail addresses.

- Tests run offline and without a model. Use fixtures for anything a model, GitHub or another host would return.
- A new behavior comes with a test. A bug fix comes with a test that fails without the fix.
- Test what a person sees or what another mod reads, not private helpers.
- Use only the Python standard library and Node's built-ins. No npm packages, no pip installs.

## Code

- No new dependency without a reason. If the mod needs a new program on the machine, add it to the requirements in
  the README in the same pull request, with the install command for macOS and Linux.
- Run programs with an argument list, never through a shell string. Text from a pull request, a branch name, a file
  path or a model answer must never reach a shell.
- Nothing posts, pushes or spends money on its own. Each of those waits for a click.
- Every program start costs Herdr about a third of a second, and drawing must stay fast. Batch calls and cache what
  you can.
- Changing a shared name, action or file format that other mods use is a breaking change. See "Versions and changes"
  in CONVENTIONS.md.

## Pull requests

- Title and commits follow [Conventional Commits](https://www.conventionalcommits.org/): `feat(setup): list glab next
  to gh`.
- Say what changes for someone using the mod and why.
- Add screenshots of the app for anything you can see: the new part, or before and after for a change.
- Leave the version, `corgi.json` and `CHANGELOG.md` to the maintainer; they change with the release.

## Security

Mods run with your user's permissions. Report a security problem through GitHub's private vulnerability reporting on
this repo, not in a public issue.
