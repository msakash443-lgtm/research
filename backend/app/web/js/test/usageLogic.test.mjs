// Run with: node --test backend/app/web/js/test   (no packages; pytest does not run these)
import test from "node:test";
import assert from "node:assert/strict";
import { usageSummary } from "../usageLogic.js";

const base = { tokens_used: 100, token_budget: null, tokens_remaining: null, percent_used: null, budget_status: "no_limit" };

test("with no limit it shows tokens used and no budget lines or warning", () => {
  assert.deepEqual(usageSummary(base), { rows: [["Tokens used", "100"], ["Budget", "No limit"]], notice: null });
});

test("with a budget it adds what is left and the share used", () => {
  const { rows, notice } = usageSummary({ ...base, token_budget: 1000, tokens_remaining: 900, percent_used: 10, budget_status: "ok" });
  assert.deepEqual(rows, [["Tokens used", "100"], ["Budget", "1000 tokens"], ["Remaining", "900 tokens"], ["Used", "10%"]]);
  assert.equal(notice, null);
});

test("near the budget it warns that calls will stop, with the numbers", () => {
  const { notice } = usageSummary({ ...base, tokens_used: 830, token_budget: 1000, tokens_remaining: 170, percent_used: 83, budget_status: "near" });
  assert.equal(notice.tone, "near");
  assert.match(notice.text, /83% used, 170 tokens left/);
});

test("over the budget it says plainly that model calls are stopped", () => {
  const { notice, rows } = usageSummary({ ...base, tokens_used: 1200, token_budget: 1000, tokens_remaining: 0, percent_used: 120, budget_status: "over" });
  assert.equal(notice.tone, "over");
  assert.match(notice.text, /No model calls will be made/);
  assert.deepEqual(rows[2], ["Remaining", "0 tokens"]);
});
