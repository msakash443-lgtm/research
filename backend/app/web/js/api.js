export async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      (typeof body.detail === "object" && body.detail?.message) ||
        (typeof body.detail === "string" && body.detail) ||
        "Something went wrong."
    );
  }
  return body;
}

export function projectApi(projectId, suffix = "") {
  return `/api/projects/${projectId}${suffix}`;
}
