# Time tracker: the plug-in contract (version 1)

The rules every mod in the herdr-corgi set shares (announcing, requests, providers, the context hook,
versions) are in [CONVENTIONS.md](https://github.com/saefty/herdr-corgi/blob/main/CONVENTIONS.md). This page has
what is specific to this mod.

The tracker works alone. Without any plug-in, all of Herdr counts as work: the time Herdr is the frontmost app
and the time any agent is working, as long as you are at the keyboard. Everything here is optional and goes
through shared state, so load order doesn't matter and the tracker never reads another mod's files.

## 1. Contexts: `goto.context`

The tracker reads the same context hook as Go to and the chat bar. Its shape is defined in Go to's
[PROVIDERS.md, section 3](https://github.com/saefty/herdr-corgi-goto/blob/main/mods/goto/PROVIDERS.md#3-context-hook-gotocontext).
One hook serves all three mods. The tracker uses these fields:

- `spaces`: the context of the shown space, and of an agent's space;
- `terminals`: an agent's own context, which wins over its space's;
- `current`: the context shown now. While it doesn't count, Herdr being frontmost doesn't count either.

## 2. Which contexts count: `timetrack.contexts`

```js
const rule = { count: ["work"], ignore: ["private"] };
if (JSON.stringify(shared("timetrack.contexts")) !== JSON.stringify(rule)) setShared("timetrack.contexts", rule);
```

- No `goto.context` hook: every context counts.
- A context in `ignore` doesn't count.
- With a non-empty `count`, only those contexts count. A space the hook gives no context then doesn't count.
- A hook without `count` or `ignore`: every context counts.

A sample counts as work when you are not idle (`idle_seconds`) and one of these holds:

- a `work_apps` app is frontmost;
- Herdr is frontmost, the shown space's context counts, and `current` counts or is null;
- an agent whose context counts is working, unless Herdr is frontmost in a context that doesn't count.

## 3. Drawing your own chip

A mod that draws its own chip, instead of placing timetrack's, reports the facts itself. Report at least
every 60 s: facts older than 90 s mean Herdr isn't running. The report also starts the tracker.

```js
await exec("python3", [`${appHome()}/mods/timetrack/track.py`, "--facts", JSON.stringify({
  hook: true,                                  // false: every context counts
  count: ["work"], ignore: ["private"],         // as in 2.
  current: "work",                             // the context shown now, or null
  focused: { name: "bookshelf", context: "work" },               // the shown space, or null
  working: [{ name: "orbit-sync", context: "work" }],            // one entry per working agent: its space's name
})]);
```

The tracker decides what counts, so resolve contexts your own way (an agent's account, a label) and send
them as they are. Space names become the Projects column.

Read today's numbers from `<app home>/timetrack-data/status.json`: `at` (epoch seconds of the last
sample; older than 180 s means the tracker isn't running), `state` (`"tracking"`, `"paused"` or `"off"`),
`net` (seconds of work today) and `week` (seconds this week). Open the popup with
`openOverlay({ mod: "timetrack", kind: "popup", width: 340, height: 320 })`, or bind a key to
`mod.open {name: "timetrack"}`.
