// The parts of the screening step that need no DOM, so `node --test` can check them (X.58). screen.js
// keeps the DOM wiring and calls these.

const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);
const KEY_ACTIONS = {
  i: "include",
  e: "exclude",
  m: "maybe",
  j: "previous",
  k: "next",
  u: "undo",
  a: "toggle-ai",
  o: "open",
  "?": "help",
};

export const OTHER_EXCLUSION_REASON = "E_OTHER";
export const SCREENING_STAGES = Object.freeze([
  { value: "title_abstract", label: "Title & abstract" },
  { value: "full_text", label: "Full text" },
]);

export function screeningStageLabel(stage) {
  return SCREENING_STAGES.find((option) => option.value === stage)?.label ?? "Title & abstract";
}

// DOI link, or null. Only ever an https://doi.org/ address, whatever the stored DOI contains.
export function safeLink(doi) {
  return doi ? `https://doi.org/${encodeURIComponent(doi).replaceAll("%2F", "/")}` : null;
}

export function authorsText(authors) {
  if (!Array.isArray(authors) || authors.length === 0) return "Authors not recorded";
  const names = authors.map((a) => (typeof a === "string" ? a : a?.name || "")).filter(Boolean);
  return names.length > 6 ? `${names.slice(0, 6).join(", ")} and ${names.length - 6} more` : names.join(", ");
}

export function countsText(counts, total) {
  const parts = [`To screen: ${total}`, `Included: ${counts.include ?? 0}`, `Excluded: ${counts.exclude ?? 0}`, `Maybe: ${counts.maybe ?? 0}`];
  if (counts.ai_auto_excluded != null) parts.push(`AI-excluded (counted): ${counts.ai_auto_excluded}`);
  return parts.join(" · ");
}

// The queue counts after one record got `decision`: that decision's count goes up, "unscreened" goes down
// (never below zero). The input object is not changed.
export function afterDecision(counts, decision) {
  return { ...counts, [decision]: (counts[decision] ?? 0) + 1, unscreened: Math.max(0, (counts.unscreened ?? 1) - 1) };
}

// Which action a key press means, or null. Nothing fires with Ctrl/Meta/Alt held, while typing in a form
// field, or while a dialog is open (the dialog has its own keys).
export function keyAction({ key, ctrlKey = false, metaKey = false, altKey = false, targetTag = "", dialogOpen = false }) {
  if (ctrlKey || metaKey || altKey || dialogOpen || TYPING_TAGS.has(targetTag)) return null;
  return KEY_ACTIONS[String(key).toLowerCase()] ?? null;
}

// Sends one record at a time and stops at the first failure, so a run never skips past a rejected record.
// Resolves to the ids that went through and the first failure's message (null when all went through).
export async function runBulk(ids, send) {
  const done = [];
  for (const id of ids) {
    try {
      await send(id);
    } catch (error) {
      return { done, failure: error.message };
    }
    done.push(id);
  }
  return { done, failure: null };
}

// Loads every page of the waiting list (M2.3.4). `fetchPage(offset)` returns one queue response; the pages are
// joined in order and the last response's fields (counts, pending) describe the whole list.
// A record that shows up on two pages (the list changed between requests) is kept once, from its first page.
// The offset counts what the server returned, duplicates included, so the next page starts where the server's list does.
export async function fetchEntireQueue(fetchPage, pageSize = 200) {
  const first = await fetchPage(0);
  const items = [];
  const seen = new Set();
  let served = 0;
  const take = (page) => {
    served += page.items.length;
    for (const item of page.items) {
      if (seen.has(item.source_id)) continue;
      seen.add(item.source_id);
      items.push(item);
    }
  };
  take(first);
  let page = first;
  while (page.items.length === pageSize) {
    page = await fetchPage(served);
    if (page.items.length === 0) break;
    take(page);
  }
  return { ...first, items };
}

// Reason keys for the exclusion dialog (M2.3.4: reasons 10 and up). Typing "1" then "0" picks reason 10. Returns
// { index } to pick now (0-based), { wait: true } when a longer number could still follow, or {} when the typed
// digits name no reason.
export function typedReason(digits, count) {
  const n = Number.parseInt(digits, 10);
  if (!(n >= 1 && n <= count)) return {};
  if (n * 10 <= count) return { wait: true };
  return { index: n - 1 };
}

// "High confidence (90%)" next to a suggestion; empty when the server gave no level.
export function confidenceText(level, confidence) {
  if (!level) return "";
  const percent = typeof confidence === "number" ? ` (${Math.round(confidence * 100)}%)` : "";
  return `${level[0].toUpperCase()}${level.slice(1)} confidence${percent}`;
}

// X.196: with peer-reviewed-only on, preprint-flagged records are listed apart from the rest. Returns groups of
// positions into `items` (positions, so the reviewer's current index still points at the same record). With the
// setting off there is one untitled group and nothing moves.
export function groupByPreprint(items, peerReviewedOnly) {
  const all = items.map((_, i) => i);
  if (!peerReviewedOnly) return [{ title: null, indices: all }];
  const preprint = all.filter((i) => items[i].preprint);
  const rest = all.filter((i) => !items[i].preprint);
  const groups = [];
  if (rest.length) groups.push({ title: null, indices: rest });
  if (preprint.length) groups.push({ title: "Preprints: not peer reviewed, not citable in synthesis", indices: preprint });
  return groups;
}

// What the Screen step shows when this person's queue is not a list of records to decide (X.219).
// "active": records to screen · "unloaded": the server counts waiting records that did not load ·
// "completed": nothing waiting, and there is work behind it (decided, or other reviewers still screening) ·
// "empty": nothing at this stage at all. Only a view rule: it never touches a decision.
export function completionMode({ items, pending, counts }) {
  if (items.length > 0) return "active";
  if (pending > 0) return "unloaded";
  const total = Object.values(counts || {}).reduce((sum, n) => sum + (Number(n) || 0), 0);
  return total > 0 ? "completed" : "empty";
}

// The heading, sentence and one primary route for a non-active mode. `primary.action` is "stage" (open `stage`)
// or "approvals"; null when there is nothing to do here.
export function completionView(mode, stage, { locked = false, othersPending = false } = {}) {
  if (mode === "unloaded") return { title: "Some records could not be loaded", text: null, primary: null };
  if (mode === "empty") {
    return {
      title: "No records at this stage yet",
      text: stage === "full_text"
        ? "Records appear here once they pass title/abstract screening."
        : "Add sources by searching or importing them, then screen them here.",
      primary: null,
    };
  }
  const wait = othersPending ? " Other reviewers are still screening or a conflict is open; see the disagreement panel below." : "";
  if (stage === "title_abstract") {
    return {
      title: "Title and abstract screening is complete for you",
      text: `Every record at this stage has your decision.${wait}`,
      primary: { label: "Continue to full-text screening", action: "stage", stage: "full_text" },
    };
  }
  return {
    title: "Full-text screening is complete for you",
    text: locked
      ? `G3 is approved. Reopen it on Approvals to change a decision.${wait}`
      : `Every record at this stage has your decision. Review the included set, then approve G3 on Approvals.${wait}`,
    primary: { label: locked ? "Open Approvals" : "Review and approve G3", action: "approvals" },
  };
}
