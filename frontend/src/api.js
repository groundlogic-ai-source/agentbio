// Thin wrapper over the Silver Bullet FastAPI backend. All paths are relative,
// so the same code works whether served by FastAPI (production) or behind the
// Vite dev proxy (development).

async function request(path, options) {
  const res = await fetch(path, options);
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body && body.detail) detail = body.detail;
    } catch {
      /* response had no JSON body */
    }
    throw new Error(detail);
  }
  return res.json();
}

export function listRuns() {
  return request("/api/runs");
}

export function getRun(jobId) {
  return request(`/api/runs/${jobId}`);
}

export function getCost(jobId) {
  return request(`/api/runs/${jobId}/cost`);
}

export function openCase(diseaseName) {
  const body = diseaseName ? { disease_name: diseaseName } : {};
  return request("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function resumeCase(jobId, action, notes) {
  return request(`/api/runs/${jobId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, notes }),
  });
}

export const TERMINAL_STATUSES = new Set(["completed", "error"]);
