// Shapes the per-project observability view (X.7.3) from GET /api/projects/{id}/observability. Pure: no DOM, no network.
// Counts and names only; the server never sends error text, so none is shown here.

const DASH = "—";

function grouped(n) {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

export function formatSeconds(seconds) {
  if (seconds === null || seconds === undefined) return DASH;
  if (seconds < 60) return `${seconds} s`;
  const whole = Math.round(seconds);
  return `${Math.floor(whole / 60)} min ${whole % 60} s`;
}

export function stepRows(data) {
  const head = ["Step", "Total", "Done", "Failed", "Queued", "Running", "Paused", "Blocked", "Retried"];
  const rows = data.steps.map((s) => [
    s.type, String(s.total), String(s.completed), String(s.failed),
    String(s.queued), String(s.running), String(s.paused), String(s.blocked), String(s.retried),
  ]);
  return { head, rows };
}

export function durationRows(data) {
  const head = ["Step", "Timed", "Average", "Longest"];
  const rows = data.step_durations.map((d) => [d.type, String(d.timed), formatSeconds(d.avg_seconds), formatSeconds(d.max_seconds)]);
  return { head, rows };
}

export function callRows(data) {
  const head = ["Purpose", "Model", "Calls", "Tokens", "Cost"];
  const rows = data.model_calls.map((c) => [
    c.purpose, c.model, String(c.calls), grouped(c.tokens), c.cost_usd === null ? DASH : money(c.cost_usd),
  ]);
  return { head, rows };
}

function money(usd) {
  return `$${usd >= 0.01 ? usd.toFixed(2) : usd.toFixed(4)}`;
}

// The four totals in the approved wireframe, in the same order.
export function tiles(data) {
  const cost = data.calls_unpriced_total > 0 ? `${data.calls_unpriced_total} calls unpriced, not counted` : "every call is priced";
  return [
    { label: "Steps run", value: String(data.steps_total), sub: `${completed(data)} completed · ${data.steps_failed} failed` },
    { label: "Steps retried", value: String(data.steps_retried), sub: "ran more than once before ending" },
    { label: "Model calls", value: String(data.model_calls_total), sub: `${grouped(data.tokens_total)} tokens (where reported)` },
    { label: "Cost (lower bound)", value: money(data.cost_usd_total), sub: cost },
  ];
}

function completed(data) {
  return data.steps.reduce((sum, s) => sum + s.completed, 0);
}

// Newest failed steps: type, when they ended for good, and attempts. The server sends no error text.
export function failureRows(data) {
  const head = ["Step", "Failed at", "Attempts"];
  const rows = data.failures.map((f) => [f.type, f.finished_at ? f.finished_at.replace("T", " ").slice(0, 16) + " UTC" : DASH, String(f.attempts)]);
  return { head, rows };
}

// Remember which run the Ask step should scroll to. Session storage can be blocked (even reading the global can throw),
// so a failure is swallowed and reported as false: Open still navigates, it just does not scroll.
export function rememberRun(getStorage, key, runId) {
  try {
    getStorage().setItem(key, runId);
    return true;
  } catch {
    return false;
  }
}
