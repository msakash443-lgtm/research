// The parts of the screening step that need no DOM, so `node --test` can check them (X.58). screen.js
// keeps the DOM wiring and calls these.

const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);
const KEY_ACTIONS = {
  i: "include",
  e: "exclude",
  m: "maybe",
  j: "next",
  k: "previous",
  u: "undo",
  a: "toggle-ai",
  o: "open",
  "?": "help",
};

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
