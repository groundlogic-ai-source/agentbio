// Thin wrapper over the AgentBio FastAPI backend. All paths are relative,
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

export function listRuns({ includeArchived = false } = {}) {
  const qs = includeArchived ? "?include_archived=true" : "";
  return request(`/api/runs${qs}`);
}

export function archiveCase(jobId) {
  return request(`/api/runs/${jobId}/archive`, { method: "PATCH" });
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

// ── Research hypothesis registry (Feature 3) ──────────────────────────────────

export function getResearchHypotheses() {
  return request("/api/research/hypotheses");
}

export function submitResearchHypothesis(hypothesisText) {
  return request("/api/research/hypotheses", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ hypothesis_text: hypothesisText }),
  });
}

export function getResearchJob(jobId) {
  return request(`/api/research/jobs/${jobId}`);
}

// Start a full autonomous discovery batch (two generators + lead review, no
// user-provided hypothesis). Returns { job_id }; poll with getResearchJob.
export function runDiscoveryBatch() {
  return request("/api/research/discovery-batch", { method: "POST" });
}
