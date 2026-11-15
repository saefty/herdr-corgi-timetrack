import test from "node:test";
import assert from "node:assert/strict";
import { samplerAlive } from "./chip.js";

test("a fresh status means the sampler runs; a stale or missing one does not", () => {
  const now = 1_700_000_000_000;
  assert.equal(samplerAlive({ at: now / 1000 - 30 }, now), true);
  assert.equal(samplerAlive({ at: now / 1000 - 91 }, now), false);
  assert.equal(samplerAlive(null, now), false);
  assert.equal(samplerAlive({ state: "paused" }, now), false);
});
