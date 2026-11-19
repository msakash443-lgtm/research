// Screening disagreement thread and independent ruling (X.227, server rules X.225). Pure: no DOM, no network.
// The server enforces every rule below; this only decides what to show and refuses obviously incomplete input early,
// so a person never sees a button that is certain to fail without being told why.

import { OTHER_EXCLUSION_REASON } from "./screenLogic.js";

const takenPart = (decisions, userId) => decisions.some((d) => String(d.decider) === String(userId));

// What the signed-in person may do on one disagreement.
// `decisions`: the reviewers' current decisions; `notes`: the thread (each with `author`, `current`);
// `ruled`: an arbiter has ruled; `canRule`: owner or supervisor (the server's approve roles).
// -> { canNote, ruling: { state, message } } with state one of
//    hidden | locked | decided_myself | took_part | needs_discussion | open
export function discussionAccess({ userId, decisions = [], notes = [], ruled = false, locked = false, canWrite = false, canRule = false }) {
  const canNote = canWrite && !locked && !ruled && takenPart(decisions, userId);
  const current = notes.filter((n) => n.current !== false);
  let ruling;
  if (!canRule) ruling = { state: "hidden", message: null };
  else if (locked) ruling = { state: "locked", message: "Screening is locked because G3 is approved." };
  else if (takenPart(decisions, userId)) ruling = { state: "decided_myself", message: "You decided this record yourself, so you can't rule on it." };
  else if (notes.some((n) => String(n.author) === String(userId))) ruling = { state: "took_part", message: "You took part in the discussion, so you can't rule on it." };
  else if (current.length === 0) ruling = { state: "needs_discussion", message: "The reviewers must discuss this disagreement first. The ruling opens once there is a discussion." };
  else ruling = { state: "open", message: null };
  return { canNote, ruling };
}

// Notes oldest first as lines of text; a note that predates a changed answer is marked, never hidden.
export function threadLines(notes, names) {
  return notes.map((n) => ({
    id: n.id,
    who: names.get(String(n.author)) || n.author,
    when: n.created_at,
    body: n.body,
    current: n.current !== false,
  }));
}

export function noteBody(text) {
  const body = String(text ?? "").trim();
  return body ? { ok: true, body } : { ok: false, message: "A note can't be empty." };
}

// The body for POST …/decisions/adjudicate: a written rationale always, and an exclusion also needs its criterion.
export function rulingRequest({ sourceId, stage, decision, reasonCode, rationale }) {
  const note = String(rationale ?? "").trim();
  if (!note) return { ok: false, message: "A ruling needs a written reason." };
  const code = reasonCode ? String(reasonCode) : null;
  if (decision === "exclude" && !code) return { ok: false, message: "An exclusion needs a reason code: choose the exclusion criterion that applies." };
  if (code === OTHER_EXCLUSION_REASON && decision !== "exclude") return { ok: false, message: "'Any other' can only be used when excluding." };
  return { ok: true, body: { source_id: sourceId, stage, decision, reason_code: code, note } };
}
