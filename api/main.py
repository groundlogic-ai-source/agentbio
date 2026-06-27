"""
Silver Bullet — Stage 4 FastAPI backend.

Exposes the existing Stage 1-3 LangGraph pipeline over HTTP. This layer ONLY
imports from the pipeline (main_graph.build_graph) and reuses resume_review's
resume_run() — it never reimplements pipeline or resume logic.

Single-user hobby project: no auth, no accounts, no external task queue. Each run
executes on a plain Python background thread and reports real per-node progress
into jobs.db.

Run:
    uvicorn api.main:app --host 0.0.0.0 --port $PORT
"""

import os
import threading
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from main_graph import build_graph
from resume_review import resume_run

from api import jobs_db

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(_REPO_ROOT, "static")
os.makedirs(STATIC_DIR, exist_ok=True)

# Node names emitted by graph.stream(...) map 1:1 onto current_stage values.
_PIPELINE_NODES = {
    "target_selection",
    "biologist",
    "chemist",
    "reviewer",
    "structure_validation",
    "writer",
}

app = FastAPI(title="Silver Bullet API", version="1.0.0")

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


# --------------------------------------------------------------------------- #
# Request/response models
# --------------------------------------------------------------------------- #
class RunRequest(BaseModel):
    disease_name: Optional[str] = None


class ResumeRequest(BaseModel):
    action: str  # "approve" or "reject"
    notes: Optional[str] = None


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

        for chunk in graph.stream({}, config=config, stream_mode="updates"):
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

                # Record the disease the graph actually selected (Stage 1 always
                # auto-picks the top-ranked candidate; see README).
                if node == "target_selection":
                    target = value.get("target") or {}
                    if target.get("disease_name"):
                        fields["disease_name"] = target["disease_name"]

                if node == "structure_validation":
                    fields["total_cost_usd"] = _sum_structure_cost(
                        value.get("structure_results") or {})

                if node == "writer":
                    reports = value.get("reports") or []
                    if reports and reports[0].get("path"):
                        fields["report_path"] = reports[0]["path"]

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
# API endpoints
# --------------------------------------------------------------------------- #
@app.post("/api/runs")
def start_run(req: RunRequest) -> dict[str, str]:
    """
    Start a pipeline run on a background thread and return its job_id immediately.

    The optional disease_name is stored as a label. The underlying Stage 1 graph
    always auto-picks the top-ranked candidate, so once target selection runs the
    job's disease_name is overwritten with the disease actually chosen.
    """
    job = jobs_db.create_job(disease_name=req.disease_name)
    thread = threading.Thread(
        target=_run_graph,
        args=(job["job_id"], job["thread_id"]),
        daemon=True,
    )
    thread.start()
    return {"job_id": job["job_id"]}


@app.get("/api/runs")
def get_runs() -> list[dict[str, Any]]:
    return jobs_db.list_jobs()


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
        job_id, status="completed", current_stage="done")
    return {"job_id": job_id, "action": action, "review": review, "job": updated}


@app.get("/api/runs/{job_id}/cost")
def get_cost(job_id: str) -> dict[str, Any]:
    job = jobs_db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return {"job_id": job_id, "total_cost_usd": job["total_cost_usd"]}


# --------------------------------------------------------------------------- #
# Static frontend (Stage 5) — empty for now, plus a SPA-style catch-all.
# Mounted/defined AFTER the API routes so /api/* and /docs always win.
# --------------------------------------------------------------------------- #
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/{full_path:path}")
def serve_frontend(full_path: str):
    """Serve the Stage 5 frontend build from /static; falls back to index.html."""
    if full_path.startswith("api"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    if full_path:
        candidate = os.path.join(STATIC_DIR, full_path)
        if os.path.isfile(candidate):
            return FileResponse(candidate)

    index = os.path.join(STATIC_DIR, "index.html")
    if os.path.isfile(index):
        return FileResponse(index)

    return JSONResponse(
        {
            "app": "Silver Bullet API",
            "message": "No frontend build yet (Stage 5). Use the API directly.",
            "docs": "/docs",
            "endpoints": [
                "POST /api/runs",
                "GET /api/runs",
                "GET /api/runs/{job_id}",
                "POST /api/runs/{job_id}/resume",
                "GET /api/runs/{job_id}/cost",
            ],
        },
        status_code=200,
    )
