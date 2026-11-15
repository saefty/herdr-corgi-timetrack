import test from "node:test";
import assert from "node:assert/strict";
import { samplerAlive, windowTitle } from "./chip.js";

test("a fresh status means the sampler runs; a stale or missing one does not", () => {
  const now = 1_700_000_000_000;
  assert.equal(samplerAlive({ at: now / 1000 - 30 }, now), true);
  assert.equal(samplerAlive({ at: now / 1000 - 91 }, now), false);
  assert.equal(samplerAlive(null, now), false);
  assert.equal(samplerAlive({ state: "paused" }, now), false);
});

test("the window title names the tracked project, and is Herdr's own otherwise", () => {
  const now = 1_700_000_000_000, at = now / 1000 - 20;
  assert.equal(windowTitle({ at, state: "tracking", title: "bookshelf" }, now), "bookshelf - Herdr");
  assert.equal(windowTitle({ at, state: "tracking", title: null }, now), null); // the setting is off, or no project
  assert.equal(windowTitle({ at, state: "paused", title: "bookshelf" }, now), null);
  assert.equal(windowTitle({ at: at - 200, state: "tracking", title: "bookshelf" }, now), null); // the tracker isn't running
  assert.equal(windowTitle(null, now), null);
});
