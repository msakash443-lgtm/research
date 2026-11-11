// Local draft of the section being written (X.31.4). The editor writes it to IndexedDB before every network save, so a
// crash or a closed tab loses at most the last couple of seconds. It is per browser: the server never reads it, and it
// is unencrypted on the device. Every call is wrapped: if storage is unavailable the editor still saves to the server
// and says so, instead of failing.
const DB_NAME = "research-workspace";
const STORE = "drafts";

function openDb() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: "document_id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function run(mode, action) {
  return openDb().then((db) => new Promise((resolve, reject) => {
    const transaction = db.transaction(STORE, mode);
    const request = action(transaction.objectStore(STORE));
    transaction.oncomplete = () => {
      db.close();
      resolve(request ? request.result : undefined);
    };
    transaction.onerror = () => {
      db.close();
      reject(transaction.error);
    };
  }));
}

// Returns true when the draft is on disk.
export async function saveDraft(draft) {
  try {
    await run("readwrite", (store) => store.put(draft));
    return true;
  } catch {
    return false;
  }
}

export async function loadDraft(documentId) {
  try {
    return (await run("readonly", (store) => store.get(documentId))) || null;
  } catch {
    return null;
  }
}

export async function clearDraft(documentId) {
  try {
    await run("readwrite", (store) => store.delete(documentId));
  } catch {
    // Nothing to do: a stale draft is caught on the next open by comparing it with the saved version.
  }
}
