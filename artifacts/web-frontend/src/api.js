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

// The engineering reference is markdown, not JSON — fetch it raw.
export async function getHowItWorks() {
  const res = await fetch("/api/how-it-works");
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return res.text();
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

export function caseReportPdfUrl(jobId) {
  return `/api/runs/${encodeURIComponent(jobId)}/report.pdf`;
}

export function getCost(jobId) {
  return request(`/api/runs/${jobId}/cost`);
}

export function flagshipPreflight(diseaseName, flagshipUseCase) {
  return request("/api/flagship/preflight", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      disease_name: diseaseName,
      ...(flagshipUseCase ? { flagship_use_case: flagshipUseCase } : {}),
    }),
  });
}

export function openCase(diseaseName, flagshipUseCase, hypothesisOnly = false) {
  const body = diseaseName ? { disease_name: diseaseName } : {};
  if (flagshipUseCase && diseaseName) {
    body.flagship_use_case = flagshipUseCase;
  }
  if (hypothesisOnly) {
    body.hypothesis_only = true;
  }
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

export function startBatch(n) {
  return request("/api/runs/batch", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ n }),
  });
}

export function getBatch(batchId) {
  return request(`/api/runs/batch/${encodeURIComponent(batchId)}`);
}

export const TERMINAL_STATUSES = new Set([
  "completed",
  "error",
  "no_eligible_candidate",
  "source_unavailable",
  "degraded_unscorable",
]);

// Fetch one frozen validation report (markdown) for in-app reading from the
// benchmark cards. Ids are allow-listed server-side; unknown ids 404.
export function getBenchmarkReport(reportId) {
  return request(`/api/research/benchmark-report/${encodeURIComponent(reportId)}`);
}

// ── Candidate audit (Part A) ──────────────────────────────────────────────

/**
 * Look up where a specific drug stands in AgentBio's reviewed-candidates pool
 * for a given disease.  Returns a structured result whose .status is one of:
 *   "found"         — drug is in the pool; full breakdown + narration included
 *   "absent"        — drug absent; target comparison + narration included
 *   "no_case"       — no completed case for this disease; start one first
 *   "no_candidates" — job exists but predates per-job persistence; re-run case
 */
export function auditDrug(diseaseName, drugName, jobId = null, claim = {}) {
  const body = { disease_name: diseaseName, drug_name: drugName };
  if (jobId) body.job_id = jobId;
  if (claim.route) body.claimed_route = claim.route;
  if (claim.dose) body.claimed_dose = claim.dose;
  if (claim.modality) body.claimed_modality = claim.modality;
  if (claim.context) body.claimed_context = claim.context;
  return request("/api/audit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ── Candidate pool and evidence cards ───────────────────────────────────────
export function getCandidatePool(params) {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  });
  return request(`/api/candidates?${search.toString()}`);
}

export function getCandidateEvidence({ disease_name, drug_name, job_id }) {
  const search = new URLSearchParams({ disease_name, drug_name });
  if (job_id) search.set("job_id", job_id);
  return request(`/api/candidates/evidence?${search.toString()}`);
}

export function getResearchBenchmarks() {
  return request("/api/research/benchmarks");
}

export function getBenchmarkStatus() {
  return request("/internal/benchmark-status");
}

// ── Audit mode: triage ───────────────────────────────────────────────────────
// Triage audits a caller-supplied drug list against one completed case's
// persisted pool. The run is persisted server-side and retrievable by run id.
export function triageCandidates(diseaseName, drugNames, jobId = null, claimContexts = {}) {
  const body = { disease_name: diseaseName, drug_names: drugNames };
  if (jobId) body.job_id = jobId;
  if (Object.keys(claimContexts).length > 0) body.claim_contexts = claimContexts;
  return request("/api/audit/triage", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function listTriageRuns() {
  return request("/api/audit/triage");
}

export function getTriageRun(runId) {
  return request(`/api/audit/triage/${encodeURIComponent(runId)}`);
}
