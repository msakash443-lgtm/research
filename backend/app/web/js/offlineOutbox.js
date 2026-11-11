const DB_NAME = "research-workspace";
const DB_VERSION = 2;
const STORE = "outbox";

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains("drafts")) {
        db.createObjectStore("drafts", { keyPath: "document_id" });
      }
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: "id", autoIncrement: true });
        store.createIndex("client_id", "client_id", { unique: true });
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function transaction(mode, action) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode);
    let result;
    try {
      result = action(tx.objectStore(STORE));
    } catch (error) {
      db.close();
      reject(error);
      return;
    }
    tx.oncomplete = () => { db.close(); resolve(result?.result); };
    tx.onerror = () => { db.close(); reject(tx.error); };
  }));
}

export async function enqueueCapture(payload, path = "/api/notes", method = "POST", clientId = payload.client_id) {
  const item = { path, method, client_id: clientId, payload, created_at: new Date().toISOString() };
  try {
    await transaction("readwrite", (store) => store.put(item));
    return true;
  } catch {
    return false;
  }
}

// An edit made offline to a saved note: replayed as PATCH against the revision it was made from, so a
// server-side change in the meantime surfaces as the 409 that flushOutbox hands to the conflict resolver.
// The PATCH body is exactly the API's schema (no client_id); the outbox key is kept separately.
export function noteEditItem(note, body) {
  return { clientId: `edit-${note.id}-${note.revision}`, payload: { body, base_revision: note.revision } };
}

export async function enqueueNoteEdit(note, body) {
  const { clientId, payload } = noteEditItem(note, body);
  // A second offline edit of the same revision replaces the first: the outbox keeps one entry per client id.
  for (const item of await listOutbox()) if (item.client_id === clientId) await removeOutbox(item.id);
  return enqueueCapture(payload, `/api/notes/${note.id}`, "PATCH", clientId);
}

// After a keep-mine/keep-both choice the retried edit is rebased onto the server's current revision.
export function rebaseEdit(payload, serverCopy) {
  return serverCopy?.revision ? { ...payload, base_revision: serverCopy.revision } : payload;
}

export async function listOutbox() {
  try {
    return (await transaction("readonly", (store) => store.getAll())) || [];
  } catch {
    return [];
  }
}

export async function pendingCount() {
  return (await listOutbox()).length;
}

export async function removeOutbox(id) {
  try {
    await transaction("readwrite", (store) => store.delete(id));
  } catch {
    // Offline storage is best-effort; a later flush can retry the item.
  }
}

export function resolveConflict(mine, theirs, choice) {
  if (choice === "mine") return { action: "retry", payload: mine };
  if (choice === "theirs") return { action: "drop", payload: theirs };
  if (choice === "both") return { action: "retry", payload: { ...mine, body: `${mine.body || ""}\n\n[Server copy]\n${theirs.body || ""}`.trim() } };
  throw new Error("Unknown conflict choice");
}

export async function flushOutbox(send, chooseConflict = () => "mine") {
  const items = await listOutbox();
  const result = { sent: 0, blocked: 0, failed: 0 };
  for (const item of items) {
    try {
      await send(item.path, item.payload, item.method || "POST");
      await removeOutbox(item.id);
      result.sent += 1;
    } catch (error) {
      if (error?.status !== 409) {
        result.failed += 1;
        continue;
      }
      const serverCopy = error.detail?.current || error.current;
      if (!serverCopy) {
        result.blocked += 1;
        continue;
      }
      const resolution = resolveConflict(item.payload, serverCopy, chooseConflict(item.payload, serverCopy));
      if (resolution.action === "drop") await removeOutbox(item.id);
      else {
        const payload = item.method === "PATCH" ? rebaseEdit(resolution.payload, serverCopy) : resolution.payload;
        await transaction("readwrite", (store) => store.put({ ...item, payload }));
      }
      result.blocked += 1;
    }
  }
  return result;
}

export function registerOfflineSync(onSync) {
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data?.type === "outbox-sync") onSync();
    });
  }
  window.addEventListener("online", onSync);
}
