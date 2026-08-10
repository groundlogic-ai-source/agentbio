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

// Registry reset: permanently delete archived bisociation history plus the
// test-ledger rows of hypotheses left with no surviving entry. dryRun=true
// only counts. Deleted rows are backed up server-side (registry_reset_backup).
export function deleteArchivedRegistry(dryRun = true) {
  return request(`/internal/delete-archived?dry_run=${dryRun ? "true" : "false"}`, {
    method: "POST",
  });
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

// ── Audit mode: list triage + dossier workspace ─────────────────────────────
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

export function getAuditDossiers() {
  return request("/api/audit/dossiers");
}

export function getDossierClaims(hypothesisId) {
  return request(`/api/audit/dossiers/${encodeURIComponent(hypothesisId)}/claims`);
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
