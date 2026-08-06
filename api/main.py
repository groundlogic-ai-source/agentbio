"""
AgentBio — Stage 4 FastAPI backend.

Exposes the existing Stage 1-3 LangGraph pipeline over HTTP. This layer ONLY
imports from the pipeline (main_graph.build_graph) and reuses resume_review's
resume_run() — it never reimplements pipeline or resume logic.

Single-user hobby project: no auth, no accounts, no external task queue. Each run
executes on a plain Python background thread and reports real per-node progress
into jobs.db.

Run:
    uvicorn api.main:app --host 0.0.0.0 --port $PORT
"""

import hashlib
import json
import os
import subprocess
import sys
import threading
from typing import Any, Optional

import sweep_manager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

import api.guardrails as _guardrails

from main_graph import build_graph
from resume_review import resume_run

from api import jobs_db
from api import research_db
from api import saved_reports_db
from api import triage_db
from api import triage as _triage
from api import dossier as _dossier
from api import audit as _audit

# Node names emitted by graph.stream(...) map 1:1 onto current_stage values.
_PIPELINE_NODES = {
    "target_selection",
    "biologist",
    "chemist",
    "reviewer",
    "structure_validation",
    "writer",
}

app = FastAPI(title="AgentBio API", version="1.0.0")

# Permissive CORS so a local frontend (Stage 5) can call this during development.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

jobs_db.init_db()
jobs_db.reap_orphaned_running_jobs()
research_db.init_db()
saved_reports_db.init_db()
triage_db.init_db()


@app.on_event("startup")
def _auto_start_sweep() -> None:
    """
    On every server startup, launch the Stage 1 sweep in the background if
    top_candidates.json is missing. Uses sweep_manager so the same process
    reference is shared with main_graph — only one sweep ever runs at a time.
    """
    pid = sweep_manager.ensure_running()
    if pid is not None:
        print(f"[startup] Stage 1 sweep auto-started (pid={pid}); "
              f"top_candidates.json missing")


# --------------------------------------------------------------------------- #
# Request/response models
# --------------------------------------------------------------------------- #
class RunRequest(BaseModel):
    disease_name: Optional[str] = None


class ResumeRequest(BaseModel):
    action: str  # "approve" or "reject"
    notes: Optional[str] = None


class BatchRequest(BaseModel):
    n: int = 3  # number of blank-mode cases to run sequentially (clamped to 1-10)


class AuditRequest(BaseModel):
    disease_name: str
    drug_name: str
    job_id: Optional[str] = None  # hint an existing job to bypass DB search


class TriageRequest(BaseModel):
    disease_name: str
    drug_names: list[str]
    job_id: Optional[str] = None  # hint an existing job to bypass DB search


# In-memory batch progress registry.  Survives for the lifetime of the server
# process; not persisted across restarts (job records survive in jobs.db).
_batch_progress: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- #
# Background graph execution
# --------------------------------------------------------------------------- #
def _sum_structure_cost(structure_results: dict[str, Any]) -> float:
    total = 0.0
    for entry in (structure_results or {}).values():
        complex_ = (entry or {}).get("complex") or {}
        cost = complex_.get("estimated_cost_usd")
        if isinstance(cost, (int, float)):
            total += float(cost)
    return total


def _run_graph(job_id: str, thread_id: str) -> None:
    """
    Drive the LangGraph pipeline on a background thread, updating jobs.db after
    each node completes so current_stage reflects real progress (not a single
    flip from running -> done).
    """
    try:
        jobs_db.update_job_status(job_id, status="running")
        graph = build_graph()
        config = {"configurable": {"thread_id": thread_id}}

        # If the case was opened with a disease name, run manual mode (look it up
        # and score it directly); otherwise pass nothing and let Stage 1 auto-pick
        # the highest-ranked pair not yet explored.
        job = jobs_db.get_job(job_id)
        requested = (job or {}).get("disease_name")
        # Live API jobs are always repurposing-only: the pool is restricted to
        # approved drugs (existing human safety profile), never research-grade
        # tool compounds. The CLI path keeps the mixed pool (see chemist_node).
        initial_state: dict[str, Any] = {"job_id": job_id, "repurposing_only": True}
        if requested:
            initial_state["requested_disease"] = requested

        for chunk in graph.stream(initial_state, config=config, stream_mode="updates"):
            if "__interrupt__" in chunk:
                # human_review reached: pipeline paused for the reviewer.
                jobs_db.update_job_status(
                    job_id, status="awaiting_review",
                    current_stage="awaiting_review")
                return

            for node, value in chunk.items():
                if node not in _PIPELINE_NODES:
                    continue
                fields: dict[str, Any] = {"status": "running",
                                          "current_stage": node}
                value = value if isinstance(value, dict) else {}

                # Record the disease the graph actually selected: in manual mode
                # the canonical matched name; in blank mode the auto-explored pair.
                if node == "target_selection":
                    target = value.get("target") or {}
                    if target.get("disease_name"):
                        fields["disease_name"] = target["disease_name"]

                if node == "chemist":
                    chem = value.get("chemist_output") or {}
                    fields["repurposing_only"] = int(
                        bool(chem.get("repurposing_only")))

                if node == "structure_validation":
                    fields["total_cost_usd"] = _sum_structure_cost(
                        value.get("structure_results") or {})

                if node == "writer":
                    reports = value.get("reports") or []
                    if reports and reports[0].get("path"):
                        fields["report_path"] = reports[0]["path"]
                    # Persist reviewed candidates per-job so the audit endpoint
                    # can serve any historical case, not just the most recent run.
                    _audit.save_job_candidates(job_id)

                jobs_db.update_job_status(job_id, **fields)

        # The first pass always interrupts at human_review; reaching here means
        # the graph finished without pausing (already-resumed thread, etc.).
        jobs_db.update_job_status(job_id, status="completed", current_stage="done")
    except Exception as exc:  # noqa: BLE001 - surface any failure to the client
        jobs_db.update_job_status(
            job_id, status="error", error_message=str(exc))


def _read_report(path: Optional[str]) -> Optional[str]:
    if path and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read()
    return None


# --------------------------------------------------------------------------- #
# Health / root paths (Replit deployment liveness probes hit these)
# --------------------------------------------------------------------------- #
@app.get("/api/")
@app.get("/api")
def api_health() -> dict:
    return {"status": "ok"}


@app.get("/internal/")
@app.get("/internal")
def internal_health() -> dict:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# API endpoints
# --------------------------------------------------------------------------- #
@app.get("/api/limits")
def get_limits() -> dict:
    """
    Document the current cost-safety guardrails in force on this API.

    Returns both the configured limits and today's usage so they can be
    referenced without digging into the source.  See api/guardrails.py for
    the full design rationale, environment-variable overrides, and alert
    delivery options.
    """
    return _guardrails.limits_summary(jobs_db.count_jobs_today)


@app.post("/api/runs")
def start_run(request: Request, req: RunRequest) -> dict[str, str]:
    """
    Start a pipeline run on a background thread and return its job_id immediately.

    Two modes:
    - disease_name provided → manual mode: scores that disease's targets directly
      (same formulas as the sweep). DiseaseNotInUniverse → job error, no fallback.
    - disease_name omitted  → blank mode: auto-picks the highest-ranked pair not
      yet explored, walking down the ranked list across repeated blank runs.
    In both modes the job's disease_name is overwritten with the canonical name
    chosen/resolved by Stage 1 once target selection completes.

    Cost-safety guardrails (both enforced before the job is created):
    - Per-IP rate limit: at most RATE_LIMIT_PER_HOUR (default 3) new cases
      per IP per rolling 60-minute window → HTTP 429 + Retry-After.
    - Global daily cap: at most DAILY_RUN_CAP (default 50) new cases per UTC
      day across all IPs → HTTP 503 + Retry-After.
    See GET /api/limits for current usage and full documentation.
    """
    _guardrails.check_ip_rate_limit(request)
    _guardrails.check_daily_cap(jobs_db.count_jobs_today)

    job = jobs_db.create_job(disease_name=req.disease_name)
    thread = threading.Thread(
        target=_run_graph,
        args=(job["job_id"], job["thread_id"]),
        daemon=True,
    )
    thread.start()
    return {"job_id": job["job_id"]}


@app.get("/api/runs")
def get_runs(include_archived: bool = False) -> list[dict[str, Any]]:
    return jobs_db.list_jobs(include_archived=include_archived)


@app.post("/api/runs/batch")
def start_batch(request: Request, req: BatchRequest) -> dict[str, Any]:
    """
    Queue N blank-mode auto-explore cases and run them sequentially on a
    background thread.  Returns immediately with batch_id + all pre-created
    job_ids so the UI can poll individual job progress via GET /api/runs/{job_id}.

    Cases run in order; each case waits for the previous one to finish (including
    the human-review pause) before the next case's graph starts.  This keeps
    API call load predictable and avoids the race condition where two cases
    simultaneously claim the same auto-picked target.

    Rate limiting:
      - IP rate limit and daily cap are checked once for the full batch (not N
        times), so a batch of 5 counts as 1 request against the IP limit.
      - The N new jobs ARE counted against today's daily usage (each creates one
        jobs.db row, so the daily cap is honoured across restarts).

    Clamped: n is clamped server-side to [1, 10] regardless of the request value.
    """
    n = max(1, min(10, req.n))
    _guardrails.check_ip_rate_limit(request)
    _guardrails.check_daily_cap(jobs_db.count_jobs_today)

    # Pre-create all N jobs so their IDs are known before the background thread
    # starts — the caller can begin polling immediately.
    job_rows = [jobs_db.create_job(disease_name=None) for _ in range(n)]
    job_ids  = [j["job_id"]  for j in job_rows]
    thread_ids = [j["thread_id"] for j in job_rows]

    # Derive a stable batch_id from the first job_id (both are SHA-256 hex).
    batch_id = hashlib.sha256(job_ids[0].encode()).hexdigest()[:20]
    _batch_progress[batch_id] = {
        "batch_id": batch_id,
        "n": n,
        "job_ids": job_ids,
        "completed": 0,
        "status": "running",
    }

    def _run_batch() -> None:
        for job_id, thread_id in zip(job_ids, thread_ids):
            try:
                _run_graph(job_id, thread_id)
            except Exception as exc:
                print(f"[batch] job {job_id} failed with {type(exc).__name__}: {exc}")
            _batch_progress[batch_id]["completed"] += 1
        _batch_progress[batch_id]["status"] = "done"
        print(f"[batch] {batch_id} complete: {n} case(s) explored")

    threading.Thread(target=_run_batch, daemon=True).start()
    return _batch_progress[batch_id]


@app.get("/api/runs/batch/{batch_id}")
def get_batch(batch_id: str) -> dict[str, Any]:
    """Poll batch progress.  Returns {batch_id, n, job_ids, completed, status}."""
    progress = _batch_progress.get(batch_id)
    if progress is None:
        raise HTTPException(status_code=404, detail="batch not found")
    return progress


@app.patch("/api/runs/{job_id}/archive")
def archive_run(job_id: str) -> dict[str, Any]:
    """
    Soft-archive a case. The record, report, and explored_targets rows are
    preserved — archiving only hides the case from the default list view.
    """
    job = jobs_db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    updated = jobs_db.archive_job(job_id)
    return updated


@app.get("/api/runs/{job_id}")
def get_run(job_id: str) -> dict[str, Any]:
    job = jobs_db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] in ("awaiting_review", "completed"):
        job["report"] = _read_report(job.get("report_path"))
    return job


@app.post("/api/runs/{job_id}/resume")
def resume(job_id: str, req: ResumeRequest) -> dict[str, Any]:
    """
    Resume a paused run by calling the SAME resume_run() the CLI uses.
    action is "approve" or "reject"; both complete the graph (END).
    """
    job = jobs_db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")

    action = (req.action or "").lower()
    if action not in ("approve", "reject"):
        raise HTTPException(
            status_code=400, detail="action must be 'approve' or 'reject'")
    if job["status"] != "awaiting_review":
        raise HTTPException(
            status_code=409,
            detail=f"job is '{job['status']}', not awaiting_review")

    try:
        review = resume_run(job["thread_id"], action, req.notes or "")
    except Exception as exc:  # noqa: BLE001
        jobs_db.update_job_status(
            job_id, status="error", error_message=str(exc))
        raise HTTPException(status_code=500, detail=str(exc))

    updated = jobs_db.update_job_status(
        job_id, status="completed", current_stage="done",
        decision=action, review_notes=req.notes or "")
    return {"job_id": job_id, "action": action, "review": review, "job": updated}


@app.get("/api/runs/{job_id}/cost")
def get_cost(job_id: str) -> dict[str, Any]:
    job = jobs_db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job_id, "total_cost_usd": job["total_cost_usd"]}


# --------------------------------------------------------------------------- #
# Candidate audit endpoint
# --------------------------------------------------------------------------- #

@app.post("/api/audit")
def audit_drug(req: AuditRequest) -> dict[str, Any]:
    """
    Look up where a specific drug stands in AgentBio's reviewed-candidates pool
    for a given disease.

    If a completed or awaiting-review case already exists for the disease, reuses
    its already-computed pool — does NOT re-run the pipeline.  If no case exists,
    returns {"status": "no_case"} so the client can submit a new run via
    POST /api/runs.

    The drug name is resolved via the existing ChEMBL best-match function
    (salt-form / synonym handling).  Cap disclosures use the same fields as the
    case dossier writer, so they cannot drift out of sync.
    """
    if not req.disease_name.strip():
        raise HTTPException(status_code=400, detail="disease_name is required")
    if not req.drug_name.strip():
        raise HTTPException(status_code=400, detail="drug_name is required")

    return _audit.run_audit(
        req.disease_name.strip(),
        req.drug_name.strip(),
        job_id_hint=req.job_id,
    )


@app.post("/api/audit/triage")
def triage_candidate_list(req: TriageRequest) -> dict[str, Any]:
    """
    Adversarially audit a caller-supplied candidate list (up to 25 drugs)
    against the persisted reviewed-candidates pool of one completed case.

    Reuses run_audit per drug with narration disabled — no pipeline re-run, no
    extra LLM calls, deterministic verdicts. The run is persisted to Postgres
    and retrievable by run id (GET /api/audit/triage/{run_id}).
    """
    if not req.disease_name.strip():
        raise HTTPException(status_code=400, detail="disease_name is required")
    if not req.drug_names:
        raise HTTPException(status_code=400, detail="drug_names must not be empty")
    if len(req.drug_names) > _triage.MAX_TRIAGE_DRUGS:
        raise HTTPException(
            status_code=400,
            detail=f"triage lists are capped at {_triage.MAX_TRIAGE_DRUGS} drugs "
                   f"per run; got {len(req.drug_names)}",
        )
    return _triage.run_triage(
        req.disease_name.strip(), req.drug_names, job_id_hint=req.job_id,
    )


@app.get("/api/audit/triage/{run_id}")
def get_triage_run(run_id: str) -> dict[str, Any]:
    """Retrieve a persisted triage run by id (the audit trail)."""
    row = triage_db.get_triage_run(run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"triage run {run_id!r} not found")
    return row


@app.get("/api/audit/triage")
def list_triage_runs() -> list[dict[str, Any]]:
    """Recent triage runs (summary fields only)."""
    return triage_db.list_triage_runs()


@app.get("/api/audit/dossiers")
def list_audit_dossiers() -> list[dict[str, Any]]:
    """Saved hypothesis reports with their current read-time audit status.

    Status is recomputed from the registry on every request (never served
    from the frozen snapshot), so a dossier whose claims stop confirming
    flips status here even though the saved narrative stays frozen.
    """
    _ensure_research_modules()
    return _dossier.list_dossiers(
        _RESEARCH_MODULES["R"],
        _RESEARCH_MODULES["HR"],
    )


@app.get("/api/audit/dossiers/{hypothesis_id}/claims")
def get_dossier_claims(hypothesis_id: str) -> dict[str, Any]:
    """Claim ledger for one dossier: framings, effect sizes, confirmation,
    confound checks, provenance, reviewer tags, and the facts fingerprint
    matching the report-cache re-gating scheme."""
    _ensure_research_modules()
    saved_report = next(
        (
            report
            for report in saved_reports_db.list_reports()
            if report.get("hypothesis_id") == hypothesis_id
        ),
        None,
    )
    ledger = _dossier.dossier_claims(
        hypothesis_id,
        _RESEARCH_MODULES["R"],
        _RESEARCH_MODULES["HR"],
        saved_report,
    )
    if ledger is None:
        raise HTTPException(
            status_code=404, detail=f"hypothesis_id {hypothesis_id!r} not found"
        )
    return ledger


@app.get("/api/candidates")
def get_candidate_pool(
    disease_name: str,
    job_id: Optional[str] = None,
    query: str = "",
    safety: Optional[str] = None,
    evidence: Optional[str] = None,
    xlogp: Optional[str] = None,
    modality: Optional[str] = None,
    sort: str = "rank",
    order: str = "asc",
    page: int = 1,
    page_size: int = 25,
) -> dict[str, Any]:
    """Paginated reviewed candidates for a completed case; no pipeline rerun."""
    if not disease_name.strip():
        raise HTTPException(status_code=400, detail="disease_name is required")
    return _audit.candidate_pool(
        disease_name.strip(), job_id_hint=job_id, query=query, safety=safety,
        evidence=evidence, xlogp=xlogp, modality=modality, sort=sort, order=order,
        page=page, page_size=page_size,
    )


@app.get("/api/candidates/evidence")
def get_candidate_evidence(
    disease_name: str,
    drug_name: str,
    job_id: Optional[str] = None,
) -> dict[str, Any]:
    """Normalized per-source evidence for a candidate in a completed case."""
    if not disease_name.strip() or not drug_name.strip():
        raise HTTPException(
            status_code=400, detail="disease_name and drug_name are required"
        )
    return _audit.candidate_evidence(
        disease_name.strip(), drug_name.strip(), job_id_hint=job_id
    )


_VALIDATION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "validation"
)


def _load_validation_artifact(filename: str) -> Optional[dict[str, Any]]:
    path = os.path.join(_VALIDATION_DIR, filename)
    try:
        with open(path, encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _benchmark_summary(artifact: dict[str, Any], label: str) -> dict[str, Any]:
    cases = artifact.get("cases") or []
    total = len(cases)
    ranked = [c for c in cases if isinstance(c, dict) and c.get("rank") is not None]
    top10 = sum(1 for c in ranked if c.get("rank", 10**9) <= 10)
    top25 = sum(1 for c in ranked if c.get("rank", 10**9) <= 25)
    misses: dict[str, int] = {}
    fixture_rows: list[dict[str, Any]] = []
    for row in cases:
        if not isinstance(row, dict):
            continue
        reason = row.get("miss_reason") or row.get("reason") or (
            "recovered" if row.get("rank") is not None else "unclassified"
        )
        if row.get("rank") is None:
            misses[str(reason)] = misses.get(str(reason), 0) + 1
        fixture_rows.append({
            "disease": row.get("disease_name") or row.get("disease"),
            "drug": row.get("drug_name") or row.get("drug"),
            "rank": row.get("rank"),
            "target": row.get("target_symbol") or row.get("selected_target"),
            "outcome": "Top 10" if row.get("rank") is not None and row["rank"] <= 10
                else ("Top 25" if row.get("rank") is not None and row["rank"] <= 25
                      else str(reason)),
        })
    return {
        "label": label, "generated_at": artifact.get("generated_at"),
        "total_cases": total, "ranked_cases": len(ranked),
        "top10": top10, "top25": top25,
        "top10_rate": (top10 / total) if total else None,
        "top25_rate": (top25 / total) if total else None,
        "miss_reasons": misses, "fixtures": fixture_rows,
        "limitations": (
            "Retrospective engineering evidence only. These artifacts predate the "
            "planned frozen post-upgrade pilot and must not be interpreted as "
            "prospective discovery accuracy."
        ),
    }


def _audit_trap_summary(artifact: dict[str, Any]) -> dict[str, Any]:
    """Summary card for the audit trap benchmark — a different shape from the
    rediscovery artifacts (detection metrics, not Top-N ranks)."""
    m = artifact.get("metrics") or {}
    return {
        "kind": "audit_traps",
        "label": "Audit trap benchmark",
        "generated_at": artifact.get("generated_at"),
        "verdict": artifact.get("verdict"),
        "traps_total": m.get("traps_total"),
        "traps_caught": m.get("traps_caught"),
        "trap_recall": m.get("trap_recall"),
        "controls_total": m.get("controls_total"),
        "controls_false_flagged": m.get("controls_false_flagged"),
        "control_false_flag_rate": m.get("control_false_flag_rate"),
        "precision": m.get("precision"),
        "thresholds": m.get("thresholds") or {},
        "traps": artifact.get("traps") or [],
        "controls": artifact.get("controls") or [],
        "limitations": artifact.get("limitations"),
    }


@app.get("/api/research/benchmarks")
def get_research_benchmarks() -> dict[str, Any]:
    """Expose existing validation artifacts with their provenance and limits."""
    artifacts = [
        ("Engineering acceptance", "engineering_acceptance_results.json"),
        ("Small-molecule retrospective", "repodb_results_smallmol.json"),
        ("Top-K retrospective", "repodb_results_topk.json"),
    ]
    summaries = [
        _benchmark_summary(artifact, label)
        for label, filename in artifacts
        if (artifact := _load_validation_artifact(filename)) is not None
    ]
    trap_artifact = _load_validation_artifact("audit_trap_results.json")
    if trap_artifact is not None:
        summaries.append(_audit_trap_summary(trap_artifact))
    return {
        "benchmarks": summaries,
        "pilot_status": "not_run",
        "pilot_note": (
            "No fresh upgraded pilot has been frozen or run. The displayed figures "
            "are historical validation artifacts and are separated from future "
            "post-upgrade results."
        ),
    }


# --------------------------------------------------------------------------- #
# Structure file download (CIF files saved by boltz_api at prediction time)
# --------------------------------------------------------------------------- #
_STRUCTURES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "output", "structures",
)


@app.get("/api/structures/{filename}")
def get_structure(filename: str) -> FileResponse:
    """Serve a locally-cached Boltz CIF structure file."""
    # Restrict to simple filenames — no path traversal.
    if "/" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    path = os.path.join(_STRUCTURES_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="structure file not found")
    return FileResponse(
        path,
        media_type="chemical/x-cif",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --------------------------------------------------------------------------- #
# Internal sweep trigger (development / admin only — no auth)
# --------------------------------------------------------------------------- #

@app.post("/internal/run-sweep")
def trigger_sweep() -> dict:
    """
    Start the Stage 1 sweep as a background process. Delegates to sweep_manager
    so the same subprocess is shared with the graph — only one sweep runs at a time.
    """
    pid = sweep_manager.ensure_running()
    if pid is None:
        return {"status": "not_needed", "reason": "top_candidates.json already exists"}
    st = sweep_manager.status()
    if st["status"] == "running":
        return {"status": "already_running", "pid": pid, "log": sweep_manager.SWEEP_LOG}
    return {"status": "started", "pid": pid, "log": sweep_manager.SWEEP_LOG}


@app.get("/internal/sweep-status")
def sweep_status() -> dict:
    """Return running / ok / error / not_started for the sweep subprocess."""
    return sweep_manager.status()


# --------------------------------------------------------------------------- #
# Dataset enrichment (MEGA 2 — PubChem + ChEMBL free-API batch)
# --------------------------------------------------------------------------- #

_ENRICH_LOG = "/tmp/enrich_log.txt"
_enrich_proc: subprocess.Popen | None = None


@app.post("/internal/run-enrichment")
def trigger_enrichment(concurrency: int = 4) -> dict:
    """
    Start data_prep/enrich_dataset.py as a background subprocess.
    Only one enrichment run at a time; returns immediately.
    """
    global _enrich_proc
    if _enrich_proc is not None and _enrich_proc.poll() is None:
        return {"status": "already_running", "pid": _enrich_proc.pid, "log": _ENRICH_LOG}

    script = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "data_prep", "enrich_dataset.py"
    )
    _enrich_proc = subprocess.Popen(
        [sys.executable, script, "--concurrency", str(concurrency)],
        stdout=open(_ENRICH_LOG, "w"),
        stderr=subprocess.STDOUT,
        cwd=os.path.dirname(os.path.abspath(__file__)),
    )
    return {"status": "started", "pid": _enrich_proc.pid, "log": _ENRICH_LOG}


@app.get("/internal/enrichment-status")
def enrichment_status() -> dict:
    """Return running / done / error / not_started for the enrichment subprocess."""
    global _enrich_proc
    if _enrich_proc is None:
        return {"status": "not_started"}
    rc = _enrich_proc.poll()
    if rc is None:
        tail = ""
        try:
            with open(_ENRICH_LOG) as f:
                lines = f.read().splitlines()
                tail = "\n".join(lines[-5:]) if lines else ""
        except OSError:
            pass
        return {"status": "running", "pid": _enrich_proc.pid, "tail": tail}
    if rc == 0:
        return {"status": "done", "returncode": rc, "log": _ENRICH_LOG}
    return {"status": "error", "returncode": rc, "log": _ENRICH_LOG}


# --------------------------------------------------------------------------- #
# Benchmark v2 24/7 supervisor (Reserved VM; read-only progress endpoints)
# --------------------------------------------------------------------------- #

_BENCH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "validation")
_BENCH_LOG = os.path.join(_BENCH_DIR, "prod_benchmark.log")
_BENCH_CONTROL_JSON = os.path.join(_BENCH_DIR, "v2_source_ablation_results.json")
_BENCH_RESULTS_JSON = os.path.join(_BENCH_DIR, "benchmark_results_v2.json")
_BENCH_CASE_LIST = os.path.join(_BENCH_DIR, "benchmark_case_list_v2.json")


def _bench_file_meta(path: str) -> dict:
    import time as _time
    if not os.path.exists(path):
        return {"exists": False}
    st = os.stat(path)
    return {
        "exists": True,
        "mtime_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(st.st_mtime)),
        "bytes": st.st_size,
    }


@app.get("/internal/benchmark-status")
def benchmark_status() -> dict:
    """Read-only progress of the 24/7 v2 benchmark chain (control -> screen -> run)."""
    status: dict[str, Any] = {"supervisor_log": _BENCH_LOG}
    try:
        with open(_BENCH_CONTROL_JSON) as f:
            control = json.load(f)
        rows = control.get("rows", [])
        status["control"] = {
            "rows_completed": len(rows),
            "rows_expected": 52,
            "snapshots": len(control.get("target_snapshots", [])),
            "snapshots_expected": 13,
            "hits": sum(1 for r in rows if r.get("generated")),
            **_bench_file_meta(_BENCH_CONTROL_JSON),
        }
    except (OSError, json.JSONDecodeError):
        status["control"] = {"exists": False}
    status["case_list"] = _bench_file_meta(_BENCH_CASE_LIST)
    try:
        with open(_BENCH_RESULTS_JSON) as f:
            results = json.load(f)
        cases = results.get("cases", results.get("results", []))
        status["benchmark"] = {
            "cases_completed": len(cases) if isinstance(cases, list) else None,
            **_bench_file_meta(_BENCH_RESULTS_JSON),
        }
    except (OSError, json.JSONDecodeError):
        status["benchmark"] = {"exists": False}
    tail: list[str] = []
    try:
        with open(_BENCH_LOG) as f:
            tail = [ln for ln in f.read().splitlines() if "MorganGenerator" not in ln][-15:]
    except OSError:
        pass
    status["log_tail"] = tail
    return status


@app.get("/internal/benchmark-results")
def benchmark_results() -> JSONResponse:
    """Return the newest benchmark artifact so progress can be pulled back to dev.

    The Reserved VM disk is wiped on restart/redeploy; pull this BEFORE any
    republish so the next snapshot resumes from the latest checkpoint.
    """
    for path, label in (
        (_BENCH_RESULTS_JSON, "benchmark_v2"),
        (_BENCH_CONTROL_JSON, "source_ablation_control"),
    ):
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return JSONResponse({"artifact": label, "data": json.load(f)})
            except (OSError, json.JSONDecodeError) as exc:
                raise HTTPException(status_code=500, detail=f"unreadable artifact {label}: {exc}")
    raise HTTPException(status_code=404, detail="no benchmark artifacts yet")


@app.post("/internal/clear-registry")
def clear_registry() -> dict:
    """Delete ALL rows from bisociation_history, hypothesis_log, and research_jobs.
    Used once after deploy to wipe seed data from the production database."""
    import psycopg2
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=500, detail="DATABASE_URL not set")
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bisociation_history")
            bh = cur.rowcount
            cur.execute("DELETE FROM hypothesis_log")
            hl = cur.rowcount
            cur.execute("DELETE FROM research_jobs")
            rj = cur.rowcount
        conn.commit()
    return {"deleted": {"bisociation_history": bh, "hypothesis_log": hl, "research_jobs": rj}}


@app.post("/internal/delete-archived")
def delete_archived_registry(dry_run: bool = True) -> dict:
    """
    Permanently delete ARCHIVED bisociation_history rows, plus the hypothesis_log
    rows of hypotheses left with no surviving history row.

    One protective exception: rows that passed BOTH discovery and confirmation
    (confirmation_pass = true) are never deleted, even when archived — a
    double-gate pass is a confirmed finding (e.g. the oncology penalty the
    product actively discloses), not resettable noise. Everything else archived
    is pre-debug history the owner asked to remove.

    Scope note: the confirmation FDR family lives on history rows
    (confirmation_raw_p) and the discovery family on log rows, so both shrink
    together — remaining hypotheses keep every test that belongs to them.
    Deleted rows are first copied into registry_reset_backup (full JSONB
    payload) so the reset stays recoverable and auditable. Deleting tests can
    only shrink the BH families, so no surviving pass can be demoted by this
    reset — but it must still be disclosed (see registry_reset_backup).

    dry_run=true (the default) only counts what would be deleted.
    """
    import psycopg2
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=500, detail="DATABASE_URL not set")
    _DEL = "archived = TRUE AND confirmation_pass IS NOT TRUE"
    _SURVIVES = "(archived = FALSE OR confirmation_pass = TRUE)"
    _LOG_ORPHAN = (
        "hypothesis_id NOT IN (SELECT DISTINCT hypothesis_id FROM bisociation_history)"
    )
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM bisociation_history WHERE {_DEL}")
            n_hist = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM hypothesis_log hl WHERE NOT EXISTS ("
                "  SELECT 1 FROM bisociation_history bh"
                f"  WHERE bh.hypothesis_id = hl.hypothesis_id AND {_SURVIVES})"
            )
            n_log = cur.fetchone()[0]
            if dry_run:
                return {
                    "dry_run": True,
                    "would_delete": {
                        "bisociation_history": n_hist,
                        "hypothesis_log": n_log,
                    },
                }
            cur.execute(
                "INSERT INTO registry_reset_backup (source_table, row_key, payload) "
                "SELECT 'bisociation_history', "
                "  hypothesis_id || '/' || outcome_framing || '#' || id, to_jsonb(t) "
                f"FROM bisociation_history t WHERE {_DEL}"
            )
            cur.execute(f"DELETE FROM bisociation_history WHERE {_DEL}")
            bh = cur.rowcount
            cur.execute(
                "INSERT INTO registry_reset_backup (source_table, row_key, payload) "
                "SELECT 'hypothesis_log', test_id, to_jsonb(t) "
                "FROM hypothesis_log t WHERE " + _LOG_ORPHAN
            )
            cur.execute("DELETE FROM hypothesis_log WHERE " + _LOG_ORPHAN)
            hl = cur.rowcount
        conn.commit()
    return {
        "dry_run": False,
        "deleted": {"bisociation_history": bh, "hypothesis_log": hl},
        "backed_up_to": "registry_reset_backup",
    }


# --------------------------------------------------------------------------- #
# Research hypothesis registry (Feature 3)
# --------------------------------------------------------------------------- #

import sys as _sys
_DATA_PREP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data_prep")
if _DATA_PREP not in _sys.path:
    _sys.path.insert(0, _DATA_PREP)

import json as _json
import datetime as _dt
import pandas as _pd

# Lazy holders — populated on first background job execution to avoid import-
# time failures when data_prep modules aren't needed (e.g. health probes).
_RESEARCH_MODULES: dict = {}


def _ensure_research_modules() -> None:
    if _RESEARCH_MODULES:
        return
    import hypothesis_registry as _R
    import features as _F
    import stats_tests as _S
    import llm_clients as _L
    import hypothesis_report as _HR
    # One-time, idempotent seed of the historical registry into an empty
    # Postgres store (no-op once populated). Replaces the old CSV back-fill.
    _R.seed_if_empty()
    _RESEARCH_MODULES.update({"R": _R, "F": _F, "S": _S, "L": _L, "HR": _HR})


# In-memory cache of generated write-ups keyed by hypothesis_id. Generation is a
# single (expensive) Opus call, so we memoise it; ?refresh=true forces a rebuild.
_REPORT_CACHE: dict = {}


_LABELED_CSV = os.path.join(_DATA_PREP, "output", "labeled_dataset.csv")

_PARSE_PROMPT_TPL = """You are the LEAD reviewer. A researcher submitted a hypothesis for testing.
Parse it into the feature DSL, or return NEEDS_ENRICHMENT / DISCARDED.

Hypothesis: "{hyp}"

Dataset columns: drug_name, ind_name, prior_repurposing_count, established_product, phase, status.

DSL ops (only these):
  {{"op":"prc_raw"}}                                        continuous
  {{"op":"prc_threshold","params":{{"k":N}}}}               binary
  {{"op":"established"}}                                   binary
  {{"op":"ind_keyword","params":{{"keywords":[...]}}}}      binary
  {{"op":"drug_keyword","params":{{"keywords":[...]}}}}     binary

Boolean composition (use these for MULTI-PART CONDITIONAL hypotheses of the form
"X fails under Y when Z but not when F" — they stay a single binary column):
  {{"op":"all_of","params":{{"terms":[<binary op>, ...]}}}}  binary (logical AND, 2-4 terms)
  {{"op":"any_of","params":{{"terms":[<binary op>, ...]}}}}  binary (logical OR, 2-4 terms)
  {{"op":"not_op","params":{{"term":<binary op>}}}}          binary (logical NOT)

Interaction ops are NOT available in this single-hypothesis tester — express a
conditional claim as an all_of / not_op subgroup instead.

DISCARD if: trivially redundant or built on prior_repurposing_count (label-confounded).

Return ONLY a JSON object:
{{
  "hypothesis_text": "<cleaned reformulation>",
  "mechanistic_justification": "<1-sentence causal argument>",
  "feature_spec": {{"op":"...", "params":{{...}}}} | null,
  "predictor_kind": "binary"|"continuous"|null,
  "tag": "READY"|"NEEDS_ENRICHMENT"|"DISCARDED",
  "needs_or_reason": "<if not READY: explanation>"
}}"""


def _run_research_job(job_id: str, hypothesis_text: str) -> None:
    """
    Background thread: parse → test on discovery split → append to SAME
    cumulative hypothesis log (FDR over everything) → update research_job.
    """
    try:
        research_db.update_job(job_id, status="running")
        _ensure_research_modules()
        _R = _RESEARCH_MODULES["R"]
        _F = _RESEARCH_MODULES["F"]
        _S = _RESEARCH_MODULES["S"]
        _L = _RESEARCH_MODULES["L"]

        # 1. Parse hypothesis via Opus lead review
        raw = _L.opus(_PARSE_PROMPT_TPL.format(hyp=hypothesis_text), max_tokens=2000)
        parsed = _L.extract_json(raw)
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}

        tag = parsed.get("tag", "NEEDS_ENRICHMENT")
        spec = parsed.get("feature_spec")
        hyp_clean = parsed.get("hypothesis_text", hypothesis_text)
        mech = parsed.get("mechanistic_justification", "")
        # Derive predictor kind STRICTLY from the feature spec, never from the
        # LLM's self-reported predictor_kind — a mislabel would route to the
        # wrong statistical test (Fisher vs logistic).
        kind = _F.predictor_kind(spec) if spec else None

        if tag != "READY" or not spec:
            research_db.update_job(job_id, status="completed", result_json=_json.dumps(
                {"tag": tag, "parsed": parsed,
                 "message": "hypothesis cannot be tested with current dataset DSL"}))
            return

        if _F.is_confounded(spec):
            research_db.update_job(job_id, status="completed", result_json=_json.dumps(
                {"tag": "DISCARDED", "parsed": parsed,
                 "message": "label-confounded: built on prior_repurposing_count"}))
            return

        # This tester runs a single feature column (Fisher or logistic). Interaction
        # specs need the multi-term fitting path in run_discovery and would raise
        # inside compute(); refuse them explicitly instead of erroring the job.
        if kind in ("interaction", "interaction3"):
            research_db.update_job(job_id, status="completed", result_json=_json.dumps(
                {"tag": "NEEDS_ENRICHMENT", "parsed": parsed,
                 "message": (
                     "interaction hypotheses are not supported in the single-hypothesis "
                     "tester — express the conditional claim as an all_of/not_op subgroup, "
                     "or run it through an autonomous discovery batch"
                 )}))
            return

        # 2. Test on discovery split (methodology locked at job creation time)
        if not os.path.exists(_LABELED_CSV):
            raise FileNotFoundError(f"labeled_dataset.csv not found at {_LABELED_CSV}")

        df = _pd.read_csv(_LABELED_CSV)
        disc = df[df["split"] == "discovery"].copy()
        ts = _dt.datetime.now(_dt.timezone.utc).isoformat()
        run_id = f"research-{job_id[:8]}"
        hid, tc = f"{run_id}-H01", [0]
        log_rows: list[dict] = []
        hist_rows: list[dict] = []

        for framing in ("narrow", "broad"):
            pos = disc["label"] == "repurposed-success"
            neg = (disc["label"] == "genuine-failure") if framing == "narrow" else \
                  disc["label"].isin(["genuine-failure", "administrative-exclude"])
            sub = disc[pos | neg].copy()
            sub["y"] = (sub["label"] == "repurposed-success").astype(int)

            feat = _F.compute(sub, spec)
            ok, why = _F.separation_ok(feat, sub["y"])
            if ok and spec.get("op") in _F._COMPOSITION_OPS:
                # A composed subgroup can be satisfied by a handful of rows; apply
                # the same pre-registered minimum the autonomous pipeline uses.
                ok, why = _F.composite_support_ok(feat, sub["y"])
            tc[0] += 1
            tid = f"{run_id}-T{tc[0]:04d}"

            if not ok:
                hist_rows.append({
                    "test_id": tid, "hypothesis_id": hid, "run_id": run_id,
                    "session_timestamp": ts, "domain_description": "user-submitted",
                    "proposing_llm": "user+opus-parse", "resulting_hypothesis_text": hyp_clean,
                    "discovery_test_type": kind, "outcome_framing": framing,
                    "discovery_raw_p": "", "discovery_fdr_p": "", "discovery_pass": "",
                    "confirmation_pass": "", "confirmation_raw_p": "",
                    "confound_check_summary": "", "outcome_note": f"not tested: {why}",
                })
                continue

            # methodology locked before result (Feature 4)
            locked_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
            res = _S.fisher_binary(feat, sub["y"]) if kind == "binary" \
                else _S.logistic_continuous(feat, sub["y"])

            log_rows.append({
                "test_id": tid, "hypothesis_id": hid, "run_id": run_id,
                "run_timestamp": ts, "hypothesis_text": hyp_clean,
                "test_type": res.test_type, "outcome_framing": framing,
                "raw_p": res.p_value,
                "significance_threshold": _R.SIGNIFICANCE_THRESHOLD,
                "correction_method": _R.CORRECTION_METHOD,
                "locked_at": locked_at,
            })
            hist_rows.append({
                "test_id": tid, "hypothesis_id": hid, "run_id": run_id,
                "session_timestamp": ts, "domain_description": "user-submitted",
                "proposing_llm": "user+opus-parse", "resulting_hypothesis_text": hyp_clean,
                "discovery_test_type": res.test_type, "outcome_framing": framing,
                "discovery_raw_p": res.p_value, "discovery_fdr_p": "",
                "discovery_pass": "", "confirmation_pass": "", "confirmation_raw_p": "",
                "confound_check_summary": "",
                "outcome_note": f"OR={res.odds_ratio:.3g} CI[{res.ci_low:.3g},{res.ci_high:.3g}] n={res.n} mech: {mech}",
            })

        # 3. Append to SAME cumulative log — FDR over everything
        if log_rows:
            _R.append_log_rows(log_rows)
        fdr = _R.cumulative_fdr()
        qmap = {row["test_id"]: row["fdr_q"] for _, row in fdr.iterrows()}
        for hr in hist_rows:
            t = hr.get("test_id")
            if t and t in qmap and hr.get("discovery_raw_p") != "":
                q = qmap[t]
                hr["discovery_fdr_p"] = q
                hr["discovery_pass"] = bool(q < _R.SIGNIFICANCE_THRESHOLD)
        if hist_rows:
            _R.append_history_rows(hist_rows)

        research_db.update_job(job_id, status="completed", result_json=_json.dumps({
            "tag": tag,
            "parsed": parsed,
            "fdr_results": [
                {"test_id": lr["test_id"], "framing": lr["outcome_framing"],
                 "raw_p": lr["raw_p"], "fdr_q": qmap.get(lr["test_id"]),
                 "discovery_pass": hr.get("discovery_pass")}
                for lr, hr in zip(log_rows, [h for h in hist_rows if h.get("test_id") in qmap])
            ],
        }))

    except Exception as exc:  # noqa: BLE001
        research_db.update_job(job_id, status="error", error_message=str(exc))


# --------------------------------------------------------------------------- #
# Autonomous discovery batch (two generators + lead review, no user hypothesis)
# --------------------------------------------------------------------------- #

# In-process guard: at most one autonomous discovery batch runs at a time. Each
# batch fires many LLM calls (Opus + Sol generation, lead review, per-hypothesis
# tests, confirmation, confound), so overlapping runs would be wasteful and would
# race on the shared registry. The lock only guards the "start" decision; the
# batch itself runs in a daemon thread tracked via the research_jobs table.
_discovery_lock = threading.Lock()
_discovery_active_job: dict = {"job_id": None}

# Per-job stop flags for continuous discovery. Keyed by job_id.
# The background thread checks stop_flag["stop"] between batch iterations.
_continuous_stop_flags: dict[str, dict] = {}


def _run_discovery_batch_job(job_id: str) -> None:
    """
    Background thread: run ONE full autonomous discovery batch via
    run_discovery.run_batch() and record the summary on the research job.
    Mirrors exactly what the build-time discovery workflow ran, as a real
    production endpoint. Writes to the SAME cumulative FDR registry.
    """
    try:
        research_db.update_job(job_id, status="running")
        _ensure_research_modules()
        import run_discovery as _RD
        summary = _RD.run_batch(run_id=f"run-{job_id[:8]}")
        research_db.update_job(job_id, status="completed",
                               result_json=_json.dumps({"mode": "autonomous_discovery",
                                                         "summary": summary}))
    except Exception as exc:  # noqa: BLE001
        research_db.update_job(job_id, status="error", error_message=str(exc))
    finally:
        with _discovery_lock:
            if _discovery_active_job["job_id"] == job_id:
                _discovery_active_job["job_id"] = None


def _run_continuous_discovery_job(job_id: str) -> None:
    """
    Background thread: chain autonomous discovery batches continuously until
    a double-pass is found, a safety cap is hit, or the user requests a stop.

    Progress is written to the job's result_json after each batch so the
    frontend can display a live counter while polling.
    """
    stop_flag = _continuous_stop_flags.setdefault(job_id, {"stop": False})
    try:
        research_db.update_job(job_id, status="running")
        _ensure_research_modules()
        import run_discovery as _RD

        def _progress(progress: dict) -> None:
            research_db.update_job(
                job_id,
                status="running",
                result_json=_json.dumps({"mode": "continuous", "progress": progress}),
            )

        summary = _RD.run_continuous_batch(
            stop_flag=stop_flag,
            progress_callback=_progress,
        )
        research_db.update_job(
            job_id,
            status="completed",
            result_json=_json.dumps({"mode": "continuous", "summary": summary}),
        )
    except Exception as exc:  # noqa: BLE001
        research_db.update_job(job_id, status="error", error_message=str(exc))
    finally:
        _continuous_stop_flags.pop(job_id, None)
        with _discovery_lock:
            if _discovery_active_job["job_id"] == job_id:
                _discovery_active_job["job_id"] = None


class ResearchHypothesisRequest(BaseModel):
    hypothesis_text: str


@app.get("/api/research/hypotheses")
def get_research_hypotheses(include_archived: bool = False) -> list[dict]:
    """Return the full bisociation_history joined with log methodology fields.
    Archived entries are excluded by default; pass include_archived=true to see them."""
    _ensure_research_modules()
    _R = _RESEARCH_MODULES["R"]
    _R.migrate_registries()
    hist = _R.load_history()
    if not include_archived:
        hist = hist[~hist["archived"].fillna(False).astype(bool)].reset_index(drop=True)
    log = _R.load_log()

    # join methodology fields from log onto history (same test_id key)
    meth = log.set_index("test_id")[["significance_threshold", "correction_method", "locked_at"]]
    hist = hist.merge(meth, on="test_id", how="left")

    # Serve AUTHORITATIVE cumulative FDR: recompute BH q-values over the entire
    # log at read time and override each tested row's stored (per-run, possibly
    # stale) discovery_fdr_p / discovery_pass. This is what makes the UI claim
    # "adding a hypothesis updates the q-values for all prior entries" true —
    # older CSV rows are never rewritten, so read-time recompute is required.
    fdr = _R.cumulative_fdr()
    qmap = {row["test_id"]: row["fdr_q"] for _, row in fdr.iterrows()}
    if qmap:
        for i, tid in hist["test_id"].items():
            if tid in qmap:
                q = qmap[tid]
                hist.at[i, "discovery_fdr_p"] = q
                hist.at[i, "discovery_pass"] = bool(q < _R.SIGNIFICANCE_THRESHOLD)

    # The CONFIRMATION stage gets the identical treatment. Its stored
    # confirmation_pass is the verdict against the confirmation family as it stood
    # when that test ran; the family grows with every later attempt, so a stored
    # True can go stale. Serve the recomputed status and keep the at-test-time
    # boolean alongside it for audit provenance.
    cqmap = _R.confirmation_qmap()
    hist["confirmation_pass_at_test_time"] = hist["confirmation_pass"]
    hist["confirmation_fdr_q"] = None
    if cqmap:
        for i, tid in hist["test_id"].items():
            if tid in cqmap:
                q = cqmap[tid]
                hist.at[i, "confirmation_fdr_q"] = q
                hist.at[i, "confirmation_pass"] = bool(q < _R.CONFIRMATION_ALPHA)

    # return as records, coercing NaN → None for JSON-serialisability
    records = hist.where(hist.notna(), other=None).to_dict("records")

    # Parse reviewer_tag from outcome_note at read time.
    # The lead reviewer's READY / NEEDS_ENRICHMENT / DISCARDED tag is persisted
    # embedded in outcome_note as "TAG: reason" rather than a standalone column.
    # Extracting it here makes it a first-class field in every API response so the
    # UI and callers can filter/display it without string-parsing outcome_note.
    # Single source of truth: api/dossier.parse_reviewer_tag (shared with the
    # dossier audit workspace — do not fork the parsing).
    for rec in records:
        note = str(rec.get("outcome_note") or "")
        dt = str(rec.get("discovery_test_type") or "").strip()
        dp = rec.get("discovery_raw_p")
        # A row was actually tested if it has a test_type AND a raw p-value.
        # Rows with a test_type but no raw_p are degenerate (separation/non-convergence).
        has_result = bool(dt and dp is not None and dp != "")
        rec["reviewer_tag"] = _dossier.parse_reviewer_tag(note, has_result)
    return records


@app.patch("/api/research/hypotheses/archive-all")
def archive_all_research_hypotheses(archived: bool = True) -> dict:
    """
    Bulk set the archived flag on every hypothesis in the registry.
    archived=true  hides them all from the default view.
    archived=false restores them all.
    Never affects the FDR log — only bisociation_history.archived.
    """
    _ensure_research_modules()
    _R = _RESEARCH_MODULES["R"]
    count = _R.archive_all_hypotheses(archived)
    return {"archived": archived, "count": count}


@app.patch("/api/research/hypotheses/{hypothesis_id}/archive")
def archive_research_hypothesis(hypothesis_id: str, archived: bool = True) -> dict:
    """
    Set or clear the archived flag on a hypothesis (UI-only — never affects FDR log).
    archived=true  hides it from the default view.
    archived=false restores it.
    Returns 404 if hypothesis_id is not in the registry.
    """
    _ensure_research_modules()
    _R = _RESEARCH_MODULES["R"]
    found = _R.set_hypothesis_archived(hypothesis_id, archived)
    if not found:
        raise HTTPException(status_code=404, detail=f"hypothesis_id {hypothesis_id!r} not found")
    return {"hypothesis_id": hypothesis_id, "archived": archived}


@app.post("/api/research/hypotheses")
def submit_research_hypothesis(req: ResearchHypothesisRequest) -> dict:
    """
    Accept a free-text hypothesis, lock the methodology immediately (before
    any result is computed — Feature 4), create an async job, and start
    testing on the same discovery split as pipeline runs.
    Writes to the SAME cumulative FDR log — no separate accounting path.
    """
    if not req.hypothesis_text.strip():
        raise HTTPException(status_code=400, detail="hypothesis_text must not be empty")
    job_id = research_db.create_job(req.hypothesis_text.strip())
    t = threading.Thread(
        target=_run_research_job,
        args=(job_id, req.hypothesis_text.strip()),
        daemon=True,
    )
    t.start()
    return {"job_id": job_id}


@app.post("/api/research/discovery-batch")
def run_discovery_batch(request: Request) -> dict:
    """
    Start a full AUTONOMOUS discovery batch: two independent generators
    (Claude Opus 4.8 + GPT-5.6 Sol) each propose their own bisociative domains,
    a lead reviewer (Opus) consolidates them, and every READY hypothesis is
    tested on the discovery split, FDR-corrected over the whole cumulative log,
    then confirmed on the holdout half and confound-checked. NO user hypothesis
    is provided — the models pick their own domains.

    Runs in a background daemon thread; poll GET /api/research/jobs/{job_id}.
    Guardrails (this batch is expensive — many LLM calls):
    - at most one batch runs at a time (409 if one is already in flight);
    - the same per-IP hourly rate limit as POST /api/runs (429 when exceeded).
    """
    # Per-IP hourly limit first, so an abusive caller is bounded even between
    # batches (the 409 single-run guard only bounds concurrent overlap).
    _guardrails.check_ip_rate_limit(request)

    with _discovery_lock:
        if _discovery_active_job["job_id"] is not None:
            raise HTTPException(
                status_code=409,
                detail="a discovery batch is already running",
            )
        job_id = research_db.create_job("(autonomous discovery batch — no user hypothesis)")
        _discovery_active_job["job_id"] = job_id

    # If the thread fails to start, release the single-run slot so the endpoint
    # doesn't get wedged in a permanently-"busy" state.
    try:
        t = threading.Thread(target=_run_discovery_batch_job, args=(job_id,), daemon=True)
        t.start()
    except Exception as exc:  # noqa: BLE001
        with _discovery_lock:
            if _discovery_active_job["job_id"] == job_id:
                _discovery_active_job["job_id"] = None
        research_db.update_job(job_id, status="error", error_message=f"failed to start: {exc}")
        raise HTTPException(status_code=500, detail="failed to start discovery batch") from exc

    return {"job_id": job_id}


@app.post("/api/research/discovery-continuous")
def run_continuous_discovery(request: Request) -> dict:
    """
    Start continuous autonomous discovery batches, chaining until EITHER:
      - at least one hypothesis achieves a double-pass (discovery AND confirmation), OR
      - a safety cap is reached (default: 20 domains or 50 hypotheses), OR
      - the caller stops the run via POST .../stop.

    Uses the same _discovery_lock as single-batch runs so at most one
    autonomous job (single or continuous) can run at a time.
    Poll GET /api/research/jobs/{job_id} for live per-batch progress.
    """
    _guardrails.check_ip_rate_limit(request)

    with _discovery_lock:
        if _discovery_active_job["job_id"] is not None:
            raise HTTPException(
                status_code=409,
                detail="a discovery batch is already running",
            )
        job_id = research_db.create_job(
            "(continuous discovery — runs until double-pass or cap)"
        )
        _discovery_active_job["job_id"] = job_id

    try:
        t = threading.Thread(
            target=_run_continuous_discovery_job, args=(job_id,), daemon=True
        )
        t.start()
    except Exception as exc:  # noqa: BLE001
        with _discovery_lock:
            if _discovery_active_job["job_id"] == job_id:
                _discovery_active_job["job_id"] = None
        research_db.update_job(
            job_id, status="error", error_message=f"failed to start: {exc}"
        )
        raise HTTPException(
            status_code=500, detail="failed to start continuous discovery"
        ) from exc

    return {"job_id": job_id}


@app.post("/api/research/discovery-continuous/{job_id}/stop")
def stop_continuous_discovery(job_id: str) -> dict:
    """
    Signal a running continuous discovery job to stop after the current
    batch completes. Returns immediately; the job may run for several more
    minutes while the in-flight batch finishes before honouring the stop.
    Returns 404 if no continuous discovery with that job_id is currently active.
    """
    flag = _continuous_stop_flags.get(job_id)
    if flag is None:
        raise HTTPException(
            status_code=404,
            detail="no active continuous discovery job with that job_id",
        )
    flag["stop"] = True
    return {"status": "stop_requested", "job_id": job_id}


@app.get("/api/research/jobs/{job_id}")
def get_research_job(job_id: str) -> dict:
    """Poll status of a research job (user hypothesis OR autonomous discovery batch)."""
    job = research_db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="research job not found")
    return job


@app.post("/api/research/hypotheses/{hypothesis_id}/report")
def generate_hypothesis_report(hypothesis_id: str, refresh: bool = False) -> dict:
    """
    Generate a full, auditable write-up for a hypothesis that passed BOTH
    cumulative-FDR discovery AND holdout confirmation.

    The numbers are assembled deterministically from the registry (with read-time
    cumulative FDR, matching /api/research/hypotheses); Opus 4.8 only narrates them
    and is instructed to introduce no statistic not already present. The response
    returns both the raw `facts` (so the UI can render the audit numbers directly)
    and the narrated `report_markdown`.

    404 if the hypothesis_id is unknown; 409 if it has not passed both stages.
    Results are cached in memory; pass ?refresh=true to force regeneration.
    """
    _ensure_research_modules()
    _HR = _RESEARCH_MODULES["HR"]

    # Always recompute facts FIRST (read-time cumulative FDR), so eligibility and
    # every number match /api/research/hypotheses. The cache is served only when
    # the freshly-computed facts are byte-identical to what was cached — otherwise
    # a hypothesis whose FDR status changed (new tests appended) could be served a
    # stale report, or a no-longer-passing hypothesis could bypass the 409 gate.
    facts = _HR.collect_facts(hypothesis_id)
    if facts is None:
        raise HTTPException(status_code=404, detail=f"hypothesis_id {hypothesis_id!r} not found")
    if not facts["passed_both"]:
        raise HTTPException(
            status_code=409,
            detail="hypothesis has not passed both discovery and confirmation; no report available",
        )

    fingerprint = hashlib.sha256(
        _json.dumps(facts, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    cached = _REPORT_CACHE.get(hypothesis_id)
    if not refresh and cached is not None and cached.get("fingerprint") == fingerprint:
        return {k: v for k, v in cached.items() if k != "fingerprint"} | {"cached": True}

    report_markdown = _HR.generate_report(facts)
    entry = {
        "hypothesis_id": hypothesis_id,
        "facts": facts,
        "report_markdown": report_markdown,
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "fingerprint": fingerprint,
    }
    _REPORT_CACHE[hypothesis_id] = entry
    return {k: v for k, v in entry.items() if k != "fingerprint"} | {"cached": False}


class SaveReportRequest(BaseModel):
    hypothesis_id: str
    hypothesis_text: Optional[str] = None
    report_markdown: str
    facts: Optional[dict] = None
    generated_at: Optional[str] = None


@app.post("/api/reports")
def create_saved_report(req: SaveReportRequest) -> dict:
    """Freeze a generated report as a permanent snapshot in the saved_reports store."""
    return saved_reports_db.save_report(
        hypothesis_id=req.hypothesis_id,
        hypothesis_text=req.hypothesis_text,
        report_markdown=req.report_markdown,
        facts=req.facts,
        generated_at=req.generated_at,
    )


@app.get("/api/reports")
def list_saved_reports() -> list[dict]:
    """List all saved reports, most recently saved first."""
    return saved_reports_db.list_reports()


@app.get("/api/reports/{report_id}")
def get_saved_report(report_id: str) -> dict:
    report = saved_reports_db.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")
    return report


@app.delete("/api/reports/{report_id}")
def delete_saved_report(report_id: str) -> dict:
    if not saved_reports_db.delete_report(report_id):
        raise HTTPException(status_code=404, detail=f"report {report_id!r} not found")
    return {"deleted": True, "id": report_id}


