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

export function archiveHypothesis(hypothesisId, archived = true) {
  return request(
    `/api/research/hypotheses/${encodeURIComponent(hypothesisId)}/archive?archived=${archived}`,
    { method: "PATCH" },
  );
}

export function archiveAllHypotheses(archived = true) {
  return request(
    `/api/research/hypotheses/archive-all?archived=${archived}`,
    { method: "PATCH" },
  );
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

// Start continuous autonomous discovery: chains batches until a double-pass is
// found, the safety cap is reached (20 domains / 50 hypotheses), or the run is
// stopped. Returns { job_id }; poll with getResearchJob for live progress.
export function runContinuousDiscovery() {
  return request("/api/research/discovery-continuous", { method: "POST" });
}

// Signal a running continuous discovery job to stop after its current batch.
// Returns immediately; the job finishes the in-flight batch before stopping.
export function stopContinuousDiscovery(jobId) {
  return request(
    `/api/research/discovery-continuous/${encodeURIComponent(jobId)}/stop`,
    { method: "POST" },
  );
}

// Generate (or fetch cached) the full auditable write-up for a hypothesis that
// passed BOTH discovery and confirmation. Triggers a single Opus 4.8 call the
// first time, so expect a few seconds. Returns { hypothesis_id, facts,
// report_markdown, generated_at, cached }.
export function generateHypothesisReport(hypothesisId, { refresh = false } = {}) {
  const qs = refresh ? "?refresh=true" : "";
  return request(
    `/api/research/hypotheses/${encodeURIComponent(hypothesisId)}/report${qs}`,
    { method: "POST" },
  );
}

// ── Saved reports ──────────────────────────────────────────────────────────
// Freeze a generated report as a permanent snapshot. Returns the stored row.
export function saveReport(payload) {
  return request("/api/reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function listSavedReports() {
  return request("/api/reports");
}

export function getSavedReport(reportId) {
  return request(`/api/reports/${encodeURIComponent(reportId)}`);
}

export function deleteSavedReport(reportId) {
  return request(`/api/reports/${encodeURIComponent(reportId)}`, { method: "DELETE" });
}
