import assert from "node:assert/strict";
import test from "node:test";
import { isTerminal, stepperProgress } from "./stages.js";

test("source-unavailable runs are terminal and do not advance the stepper", () => {
  assert.equal(isTerminal("source_unavailable"), true);
  assert.deepEqual(
    stepperProgress("source_unavailable", "chemist"),
    { completedThrough: 2, activeIndex: -1 },
  );
});

test("healthy no-candidate runs remain distinct terminal results", () => {
  assert.equal(isTerminal("no_eligible_candidate"), true);
  assert.equal(isTerminal("running"), false);
});

test("degraded-unscorable runs are terminal and leave no active step", () => {
  assert.equal(isTerminal("degraded_unscorable"), true);
  assert.deepEqual(
    stepperProgress("degraded_unscorable", "reviewer"),
    { completedThrough: 3, activeIndex: -1 },
  );
});