// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { callRows, durationRows, failureRows, formatSeconds, openFailedRun, rememberRun, stepRows, tiles } from "../observabilityLogic.js";

const data = {
  steps: [{ type: "research_run", total: 38, completed: 31, failed: 4, queued: 1, running: 1, paused: 0, blocked: 1, retried: 9 }],
  step_durations: [
    { type: "research_run", timed: 31, avg_seconds: 72, max_seconds: 400 },
    { type: "source_retrieval", timed: 0, avg_seconds: null, max_seconds: null },
  ],
  steps_total: 38, steps_failed: 4, steps_retried: 9,
  model_calls: [
    { purpose: "research_run", model: "claude-sonnet-5-5", calls: 96, tokens: 580000, calls_without_token_counts: 0, cost_usd: 2.81, calls_unpriced: 0 },
    { purpose: "research_run", model: "local-model", calls: 40, tokens: 120000, calls_without_token_counts: 0, cost_usd: null, calls_unpriced: 40 },
  ],
  model_calls_total: 136, tokens_total: 700000, cost_usd_total: 2.81, calls_unpriced_total: 40,
};

test("step table has one column per outcome, queued, running and paused apart", () => {
  const { head, rows } = stepRows(data);
  assert.deepEqual(head, ["Step", "Total", "Done", "Failed", "Queued", "Running", "Paused", "Blocked", "Retried"]);
  assert.deepEqual(rows[0], ["research_run", "38", "31", "4", "1", "1", "0", "1", "9"]);
});

test("durations show the average and longest time, and say when nothing was timed", () => {
  const { rows } = durationRows(data);
  assert.deepEqual(rows[0], ["research_run", "31", "1 min 12 s", "6 min 40 s"]);
  assert.deepEqual(rows[1], ["source_retrieval", "0", "—", "—"]);
});

test("seconds are read as plain times", () => {
  assert.equal(formatSeconds(4.2), "4.2 s");
  assert.equal(formatSeconds(72), "1 min 12 s");
  assert.equal(formatSeconds(400), "6 min 40 s");
  assert.equal(formatSeconds(null), "—");
});

test("model calls show tokens with separators, and an unpriced model shows a dash", () => {
  const { rows } = callRows(data);
  assert.deepEqual(rows[0], ["research_run", "claude-sonnet-5-5", "96", "580,000", "$2.81"]);
  assert.deepEqual(rows[1], ["research_run", "local-model", "40", "120,000", "—"]);
});

test("the four totals say what is counted and how many calls are left out of the cost", () => {
  const [steps, retried, calls, cost] = tiles(data);
  assert.equal(steps.value, "38");
  assert.equal(steps.sub, "31 completed · 4 failed");
  assert.equal(retried.value, "9");
  assert.equal(calls.value, "136");
  assert.equal(cost.value, "$2.81");
  assert.equal(cost.sub, "40 calls unpriced, not counted");
});

test("with every call priced the cost tile says nothing is left out", () => {
  const priced = { ...data, calls_unpriced_total: 0 };
  assert.equal(tiles(priced)[3].sub, "every call is priced");
});

test("failure rows show type, end time and attempts for each newest failure, with a dash when it never finished", () => {
  const rows = failureRows({
    failures: [
      { id: "a", type: "research_run", finished_at: "2026-10-21T14:03:55+00:00", attempts: 3 },
      { id: "b", type: "prescreen", finished_at: null, attempts: 1 },
    ],
  });
  assert.deepEqual(rows.head, ["Step", "Failed at", "Attempts"]);
  assert.deepEqual(rows.rows, [["research_run", "2026-10-21 14:03 UTC", "3"], ["prescreen", "—", "1"]]);
});

test("remembering the run to open stores it, and a blocked session store does not throw", () => {
  const saved = {};
  assert.equal(rememberRun(() => ({ setItem: (k, v) => { saved[k] = v; } }), "key", "run-1"), true);
  assert.deepEqual(saved, { key: "run-1" });
  const blocked = () => { throw new Error("SecurityError"); };
  assert.equal(rememberRun(blocked, "key", "run-1"), false);
  assert.equal(rememberRun(() => ({ setItem: blocked }), "key", "run-1"), false);
});

test("Open remembers the run and still navigates to Ask when the session store throws", () => {
  const visited = [];
  openFailedRun(() => { throw new Error("SecurityError"); }, "key", "run-1", (step) => visited.push(step));
  assert.deepEqual(visited, ["ask"]);
  const saved = {};
  openFailedRun(() => ({ setItem: (k, v) => { saved[k] = v; } }), "key", "run-1", (step) => visited.push(step));
  assert.deepEqual(saved, { key: "run-1" });
  assert.deepEqual(visited, ["ask", "ask"]);
});
