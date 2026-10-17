const KEY = "research-ai-origin-colors";

export function readOriginColors(storage, projectId) {
  try {
    const stored = storage?.getItem(`${KEY}:${projectId}`);
    return stored === null || stored === undefined ? true : stored !== "off";
  } catch {
    return true;
  }
}

export function writeOriginColors(storage, projectId, enabled) {
  try {
    storage?.setItem(`${KEY}:${projectId}`, enabled ? "on" : "off");
  } catch {
    // A blocked browser store should not prevent writing.
  }
}
