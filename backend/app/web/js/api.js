export async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
  } catch {
    throw new Error("Can't reach the server. Check that it is running and that you are using its address.");
  }
  if (response.status === 204) return null;
  const text = await response.text().catch(() => "");
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch {}
  if (!response.ok) {
    const where = `${(options.method || "GET").toUpperCase()} ${path}`;
    throw new Error(
      (typeof body.detail === "object" && body.detail?.message) ||
        (typeof body.detail === "string" && body.detail) ||
        (response.status === 400 && text.trim() === "Invalid host header"
          ? "The server rejected this address (host not allowed). Add it to ALLOWED_HOSTS."
          : `Something went wrong (HTTP ${response.status}, ${where}). Check the server log for details.`)
    );
  }
  return body;
}

export function projectApi(projectId, suffix = "") {
  return `/api/projects/${projectId}${suffix}`;
}
