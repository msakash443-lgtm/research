const KEY = "ra.memoryCollapsed";

// Reading window.localStorage itself can throw when site data is blocked, and app.js reads
// this at module top level, so every access is guarded.
export function readMemoryCollapsed(globalObject = globalThis) {
  try {
    return globalObject.localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}

export function writeMemoryCollapsed(collapsed, globalObject = globalThis) {
  try {
    globalObject.localStorage.setItem(KEY, collapsed ? "1" : "0");
  } catch {
    // A blocked browser store should not stop the panel from toggling.
  }
}
