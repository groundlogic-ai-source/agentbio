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
import hmac
import ipaddress
import io
import json
import os
import re
import stat
import subprocess
import sys
import threading
import zipfile
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import sweep_manager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel

import api.guardrails as _guardrails
from api.policy_contracts import (
    DECISION_CONTRACT_VERSION,
    LITERATURE_SCHEMA_VERSION,
    REPORT_CONTRACT_VERSION,
    REVIEWER_FORMULA_VERSION,
    SAFETY_SCHEMA_VERSION,
    canonical_json_bytes,
)

from main_graph import build_graph
from resume_review import resume_run

from api import jobs_db
from api import research_db
from api import saved_reports_db
from api import triage_db
from api import triage as _triage
from api import dossier as _dossier
from api import audit as _audit
from api.report_pdf import render_case_pdf
from data_sources.source_health import probe_required_sources
from validation.benchmark_v2_completion import inspect_frozen_result

# Node names emitted by graph.stream(...) map 1:1 onto current_stage values.
_PIPELINE_NODES = {
    "target_selection",
    "biologist",
    "chemist",
    "reviewer",
    "structure_validation",
    "writer",
}

_JOB_REPORT_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "output", "job_reports",
)
_STALE_POLICY = (
    "Superseded policy snapshot — historical only — cannot be approved."
)
_DEFAULT_PUBLIC_APP_URL = "https://agentbio.groundlogic.ai"
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _job_report_path(job_id: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_-]", "", job_id)
    if not safe_id or safe_id != job_id:
        raise ValueError("invalid job id for report provenance")
    return os.path.join(_JOB_REPORT_ROOT, safe_id, "report.md")


def _snapshot_binding_line(snapshot_sha256: str) -> str:
    return f"Candidate snapshot SHA-256: `{snapshot_sha256}`"


def _public_origin() -> str:
    """Return only the verified canonical HTTPS origin, never request headers."""
    value = os.environ.get("PUBLIC_APP_URL", _DEFAULT_PUBLIC_APP_URL)
    if not value or value != value.strip() or any(
            ch.isspace() or ord(ch) < 32 for ch in value):
        raise RuntimeError("PUBLIC_APP_URL must be an absolute HTTPS origin without path/query")
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        port = parsed.port  # forces malformed/non-numeric/range validation
    except ValueError as exc:
        raise RuntimeError(
            "PUBLIC_APP_URL must contain a valid hostname and port") from exc
    valid_host = False
    if hostname:
        try:
            ipaddress.ip_address(hostname)
            valid_host = True
        except ValueError:
            labels = hostname.rstrip(".").split(".")
            valid_host = bool(labels) and all(
                re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in labels)
    if (parsed.scheme != "https" or not parsed.netloc or not valid_host
            or parsed.username
            or parsed.password or parsed.path
            or parsed.params or parsed.query or parsed.fragment):
        raise RuntimeError("PUBLIC_APP_URL must be an absolute HTTPS origin without path/query")
    # urlparse has now validated port and bracketed IPv6. Preserve an explicitly
    # configured valid port in the canonical origin.
    return f"https://{parsed.netloc}" if port is not None else f"https://{parsed.netloc}"


def _artifact_url(job_id: str, kind: str, artifact_id: Optional[str] = None) -> str:
    public_kind = {"report_md": "report.md", "report_pdf": "report.pdf",
                   "evidence_zip": "evidence.zip"}.get(kind, kind)
    path = f"/api/runs/{job_id}/artifacts/{public_kind}"
    if kind == "cif":
        if not artifact_id or not _SHA256_RE.fullmatch(artifact_id):
            raise ValueError("CIF artifact id must be a SHA-256")
        path += f"/{artifact_id}"
    return _public_origin() + path


def _safe_local_cif(filename: str) -> Optional[tuple[str, bytes]]:
    """Read one CIF from the owned cache without following names or symlinks."""
    if (not filename or os.path.basename(filename) != filename or "\x00" in filename
            or not filename.lower().endswith(".cif")):
        return None
    root = os.path.realpath(_STRUCTURES_DIR)
    path = os.path.join(root, filename)
    try:
        if os.path.islink(path) or not os.path.isfile(path):
            return None
        if os.path.commonpath([root, os.path.realpath(path)]) != root:
            return None
        size = os.path.getsize(path)
        if size < 1 or size > 10 * 1024 * 1024:
            return None
        with open(path, "rb") as fh:
            value = fh.read(10 * 1024 * 1024 + 1)
        return (filename, value) if len(value) == size else None
    except OSError:
        return None


def _deterministic_evidence_zip(
        job_id: str, report: bytes, pdf: bytes, snapshot: dict[str, Any],
        cifs: list[dict[str, Any]], report_sha256: str, snapshot_sha256: str,
) -> bytes:
    """Create a reproducible evidence package; ZIP metadata never uses wall time."""
    entries: list[tuple[str, bytes, str]] = [
        ("report.md", report, "text/markdown; charset=utf-8"),
        ("report.pdf", pdf, "application/pdf"),
        ("candidate_snapshot.json", canonical_json_bytes(snapshot), "application/json"),
    ]
    for cif in sorted(cifs, key=lambda item: str(item["artifact_id"])):
        entries.append((f"cif/{cif['filename']}", bytes(cif["payload"]), cif["content_type"]))
    manifest = {
        "schema_version": "agentbio-evidence-bundle-v1",
        "job_id": job_id,
        "production_dossier_url": _artifact_url(job_id, "report_pdf"),
        "report_sha256": report_sha256,
        "candidate_snapshot_sha256": snapshot_sha256,
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(),
        "files": [
            {"filename": "report.md", "content_type": "text/markdown; charset=utf-8",
             "size_bytes": len(report), "sha256": report_sha256},
            {"filename": "report.pdf", "content_type": "application/pdf",
             "size_bytes": len(pdf), "sha256": hashlib.sha256(pdf).hexdigest()},
            {"filename": "candidate_snapshot.json", "content_type": "application/json",
             "size_bytes": len(canonical_json_bytes(snapshot)),
             "sha256": snapshot_sha256},
        ],
        "cifs": [{
            "artifact_id": item["artifact_id"], "sha256": item["content_sha256"],
            "filename": item["filename"], "content_type": item["content_type"],
            "size_bytes": item["size_bytes"],
        } for item in sorted(cifs, key=lambda item: str(item["artifact_id"]))],
        "policy_contract_versions": {
            "decision": DECISION_CONTRACT_VERSION,
            "report": REPORT_CONTRACT_VERSION,
            "reviewer_formula": REVIEWER_FORMULA_VERSION,
            "safety": SAFETY_SCHEMA_VERSION,
            "literature": LITERATURE_SCHEMA_VERSION,
        },
    }
    entries.append(("manifest.json", canonical_json_bytes(manifest), "application/json"))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9, strict_timestamps=True) as archive:
        for name, payload, _content_type in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED,
                             compresslevel=9)
    return out.getvalue()


def _rewrite_structure_artifacts(
        report_text: str, job_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Replace every transient structure reference and collect safe CIF bytes."""
    marker = "Structure artifact unavailable in frozen package."
    unavailable = False
    by_digest: dict[str, dict[str, Any]] = {}

    def local_tail(tail: str) -> str:
        nonlocal unavailable
        filename = tail.split("?", 1)[0].split("#", 1)[0]
        if not re.fullmatch(r"[A-Za-z0-9._-]+\.cif", filename, re.IGNORECASE):
            unavailable = True
            return marker
        local = _safe_local_cif(filename)
        if not local:
            unavailable = True
            return marker
        name, payload = local
        digest = hashlib.sha256(payload).hexdigest()
        by_digest.setdefault(digest, {
            "artifact_id": digest, "filename": name, "payload": payload,
            "content_sha256": digest, "content_type": "chemical/x-cif",
            "size_bytes": len(payload),
        })
        return _artifact_url(job_id, "cif", digest)

    def local_link(match: re.Match[str]) -> str:
        replacement = local_tail(match.group(1).split("/api/structures/", 1)[1])
        return marker if replacement == marker else f"[Download durable CIF]({replacement})"

    report_text = re.sub(
        r"\[[^\]]*\]\(((?:https?://[^/\s)]+)?/api/structures/[^)]*)\)",
        local_link, report_text, flags=re.IGNORECASE)
    report_text = re.sub(
        r"(?:https?://[^/\s)\]]+)?/api/structures/([^\s)\]>'\"]*)",
        lambda match: local_tail(match.group(1)), report_text, flags=re.IGNORECASE)

    def remote(_match: re.Match[str]) -> str:
        nonlocal unavailable
        unavailable = True
        return marker

    report_text = re.sub(
        r"\[[^\]]*(?:cif|structure)[^\]]*\]\("
        r"(?!(?:https?://[^/\s)]+)?/api/runs/[^/\s)]+/artifacts/cif/"
        r"[a-f0-9]{64}(?:[?#][^)]*)?\))"
        r"https?://[^)]+\)",
        remote, report_text, flags=re.IGNORECASE)
    report_text = re.sub(
        r"(?<!/api/runs/)"
        r"https?://[^\s)\]]+\.cif(?:\?[^\s)\]]*)?",
        remote, report_text, flags=re.IGNORECASE)
    # A mixed report can contain one malformed/transient reference alongside
    # a successfully captured durable CIF.  Do not append a package-level
    # "unavailable" claim when the package contains a durable structure.
    if unavailable and not by_digest:
        report_text += f"\n\n> **{marker}**\n"
    return report_text, sorted(by_digest.values(), key=lambda row: row["artifact_id"])


def _validate_evidence_zip(
    job: dict[str, Any], snapshot: dict[str, Any], report: dict[str, Any],
    pdf: dict[str, Any], evidence: dict[str, Any], cifs: list[dict[str, Any]],
) -> None:
    """Deeply verify the closed evidence package against database authorities."""
    raw = bytes(evidence["payload"])
    if len(raw) > 100 * 1024 * 1024:
        raise ValueError("evidence ZIP exceeds limit")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        if len(infos) > 128 or sum(i.file_size for i in infos) > 150 * 1024 * 1024:
            raise ValueError("evidence ZIP expansion exceeds limit")
        names = [i.filename for i in infos]
        if len(names) != len(set(names)):
            raise ValueError("duplicate ZIP entry")
        for info in infos:
            name = info.filename
            if (not name or name.startswith(("/", "\\"))
                    or "\\" in name or any(ord(ch) < 32 for ch in name)
                    or ".." in name.split("/") or info.flag_bits & 0x1):
                raise ValueError("unsafe ZIP entry")
        manifest_raw = archive.read("manifest.json")
        manifest = json.loads(manifest_raw)
        expected_contracts = {
            "decision": DECISION_CONTRACT_VERSION,
            "report": REPORT_CONTRACT_VERSION,
            "reviewer_formula": REVIEWER_FORMULA_VERSION,
            "safety": SAFETY_SCHEMA_VERSION,
            "literature": LITERATURE_SCHEMA_VERSION,
        }
        if (manifest.get("schema_version") != "agentbio-evidence-bundle-v1"
                or manifest.get("job_id") != job.get("job_id")
                or manifest.get("production_dossier_url")
                != _artifact_url(str(job.get("job_id")), "report_pdf")
                or manifest.get("report_sha256") != job.get("report_sha256")
                or manifest.get("candidate_snapshot_sha256")
                != job.get("candidate_snapshot_sha256")
                or manifest.get("pdf_sha256") != pdf.get("content_sha256")
                or manifest.get("policy_contract_versions") != expected_contracts):
            raise ValueError("manifest provenance mismatch")
        for artifact in (report, pdf, evidence, *cifs):
            payload = bytes(artifact["payload"])
            if (hashlib.sha256(payload).hexdigest() != artifact["content_sha256"]
                    or len(payload) != artifact.get("size_bytes", len(payload))
                    or artifact.get("report_sha256") != job.get("report_sha256")
                    or artifact.get("candidate_snapshot_sha256")
                    != job.get("candidate_snapshot_sha256")):
                raise ValueError("database artifact binding mismatch")
        expected = {
            "report.md": (bytes(report["payload"]), report["content_type"]),
            "report.pdf": (bytes(pdf["payload"]), pdf["content_type"]),
            "candidate_snapshot.json": (
                canonical_json_bytes(snapshot), "application/json"),
        }
        cif_manifest = manifest.get("cifs")
        if not isinstance(cif_manifest, list):
            raise ValueError("CIF manifest missing")
        declared_cif_ids = [row.get("artifact_id") for row in cif_manifest
                            if isinstance(row, dict)]
        declared_cif_names = [row.get("filename") for row in cif_manifest
                              if isinstance(row, dict)]
        if (len(declared_cif_ids) != len(cif_manifest)
                or len(declared_cif_ids) != len(set(declared_cif_ids))
                or len(declared_cif_names) != len(set(declared_cif_names))):
            raise ValueError("duplicate CIF declaration")
        by_id = {row["artifact_id"]: row for row in cifs}
        if (len(by_id) != len(cifs)
                or set(declared_cif_ids) != set(by_id)
                or {row["filename"] for row in cifs} != set(declared_cif_names)):
            raise ValueError("CIF declaration mismatch")
        for declared in cif_manifest:
            db = by_id[declared["artifact_id"]]
            name = f"cif/{db['filename']}"
            if (declared.get("filename") != db["filename"]
                    or declared.get("content_type") != db["content_type"]
                    or declared.get("sha256") != db["content_sha256"]
                    or declared.get("size_bytes") != db["size_bytes"]):
                raise ValueError("CIF metadata mismatch")
            expected[name] = (bytes(db["payload"]), db["content_type"])
        required_names = set(expected) | {"manifest.json"}
        if set(names) != required_names:
            raise ValueError("unexpected or missing ZIP entry")
        file_manifest = manifest.get("files")
        if not isinstance(file_manifest, list):
            raise ValueError("file manifest missing")
        file_names = [row.get("filename") for row in file_manifest
                      if isinstance(row, dict)]
        if (len(file_names) != len(file_manifest)
                or len(file_names) != len(set(file_names))):
            raise ValueError("duplicate file declaration")
        declared_files = {row.get("filename"): row for row in file_manifest}
        if set(declared_files) != {"report.md", "report.pdf", "candidate_snapshot.json"}:
            raise ValueError("file declarations invalid")
        for name, (payload, content_type) in expected.items():
            actual = archive.read(name)
            if actual != payload:
                raise ValueError("ZIP entry differs from database artifact")
            if name in declared_files:
                declared = declared_files[name]
                if (declared.get("sha256") != hashlib.sha256(actual).hexdigest()
                        or declared.get("size_bytes") != len(actual)
                        or declared.get("content_type") != content_type):
                    raise ValueError("file manifest hash/size/type mismatch")
        if archive.read("candidate_snapshot.json") != canonical_json_bytes(snapshot):
            raise ValueError("snapshot JSON is noncanonical")


def _persist_actionable_report(
        job_id: str, generated_path: str) -> dict[str, Any]:
    """Freeze the writer output only after its durable reviewer snapshot exists."""
    snapshot = jobs_db.get_candidate_snapshot(job_id)
    if not isinstance(snapshot, dict):
        raise RuntimeError(
            "Durable job candidate snapshot missing; report cannot become "
            "actionable.")
    snapshot_formula = snapshot.get("formula") or {}
    snapshot_candidates = snapshot.get("candidates") or []
    if (
        snapshot_formula.get("formula_version") != REVIEWER_FORMULA_VERSION
        or snapshot.get("safety_schema_version") != SAFETY_SCHEMA_VERSION
        or snapshot.get("literature_schema_version")
        != LITERATURE_SCHEMA_VERSION
        or snapshot.get("report_contract_version") != REPORT_CONTRACT_VERSION
        or not snapshot.get("reviewer_input_fingerprint")
        or snapshot.get("source_coverage_complete") is not True
        or bool(snapshot.get("source_failure_details"))
        or not snapshot_candidates
        or any(
            (candidate.get("dossier_evidence_contract") or {}).get(
                "contract_version") != REPORT_CONTRACT_VERSION
            or (candidate.get("candidate_source_coverage") or {}).get(
                "complete") is not True
            for candidate in snapshot_candidates
        )
    ):
        raise RuntimeError(
            "Reviewer snapshot is incomplete or uses superseded policy "
            "contracts; report cannot become actionable.")
    snapshot_hash = hashlib.sha256(canonical_json_bytes(snapshot)).hexdigest()
    with open(generated_path, "rb") as fh:
        generated_bytes = fh.read()
    # Reports are Markdown/UTF-8; validate and bind the exact durable snapshot
    # before hashing or creating the immutable copy.
    generated_report = generated_bytes.decode("utf-8")
    binding_prefix = (
        "<!-- AgentBio immutable decision provenance -->\n"
        f"{_snapshot_binding_line(snapshot_hash)}\n\n"
    )
    report_bytes = (
        generated_bytes
        if generated_report.startswith(binding_prefix)
        else f"{binding_prefix}{generated_report}".encode("utf-8")
    )
    # Expiring/local structure URLs must never survive inside an immutable
    # dossier.  Persist only safely read cache files and replace their links
    # with content-addressed durable artifact URLs.
    report_text = report_bytes.decode("utf-8")
    report_text, cif_rows = _rewrite_structure_artifacts(report_text, job_id)
    report_bytes = report_text.encode("utf-8")
    metadata = {
        "report_path": f"artifact://{job_id}/report.md",
        "decision_contract_version": DECISION_CONTRACT_VERSION,
        "report_contract_version": REPORT_CONTRACT_VERSION,
        "report_sha256": hashlib.sha256(report_bytes).hexdigest(),
        "candidate_snapshot_sha256": snapshot_hash,
        "reviewer_input_fingerprint":
            snapshot.get("reviewer_input_fingerprint"),
    }
    # Generate every byte before the single database transaction.  There is
    # never a committed partial artifact set.
    source_job = jobs_db.get_job(job_id) or {}
    frozen_job = {
        "job_id": job_id,
        "disease_name": snapshot.get("disease_name") or source_job.get("disease_name"),
        "created_at": source_job.get("created_at"),
        # Use the durable job update timestamp as the snapshot timestamp.  It
        # is stable across retry rendering and avoids the 1980 fpdf2 fallback.
        "report_snapshot_at": source_job.get("updated_at")
        or source_job.get("created_at"),
        "report_sha256": metadata["report_sha256"],
    }
    pdf = render_case_pdf(report_text, frozen_job)
    evidence = _deterministic_evidence_zip(
        job_id, report_bytes, pdf, snapshot, cif_rows,
        metadata["report_sha256"], snapshot_hash)
    jobs_db.finalize_job_artifact_bundle(
            job_id, report_path=f"artifact://{job_id}/report.md",
            **{key: metadata[key] for key in (
                "decision_contract_version", "report_contract_version",
                "report_sha256", "candidate_snapshot_sha256",
                "reviewer_input_fingerprint")},
            artifacts=[
                {"kind": "report_md", "payload": report_bytes,
                 "filename": "report.md", "content_type": "text/markdown; charset=utf-8"},
                {"kind": "report_pdf", "payload": pdf, "filename": "report.pdf",
                 "content_type": "application/pdf"},
                {"kind": "evidence_zip", "payload": evidence, "filename": "evidence.zip",
                 "content_type": "application/zip"},
                *[{"kind": "cif", "payload": row["payload"], "filename": row["filename"],
                   "content_type": row["content_type"]} for row in cif_rows],
            ])
    return metadata


def _actionability(job: dict[str, Any]) -> dict[str, Any]:
    """Validate every durable boundary needed for an actionable decision."""
    reasons: list[str] = []
    snapshot: Optional[dict[str, Any]]
    try:
        snapshot = jobs_db.get_candidate_snapshot(str(job.get("job_id") or ""))
    except Exception:
        snapshot = None
    if not isinstance(snapshot, dict):
        reasons.append("durable job candidate snapshot is missing")
    expected_metadata = {
        "decision_contract_version": DECISION_CONTRACT_VERSION,
        "report_contract_version": REPORT_CONTRACT_VERSION,
    }
    for field, expected in expected_metadata.items():
        if job.get(field) != expected:
            reasons.append(f"{field} is missing or superseded")

    if snapshot is not None:
        formula = snapshot.get("formula") or {}
        if formula.get("formula_version") != REVIEWER_FORMULA_VERSION:
            reasons.append("reviewer formula contract is missing or superseded")
        if snapshot.get("safety_schema_version") != SAFETY_SCHEMA_VERSION:
            reasons.append("safety contract is missing or superseded")
        if snapshot.get("literature_schema_version") != LITERATURE_SCHEMA_VERSION:
            reasons.append("literature contract is missing or superseded")
        if snapshot.get("report_contract_version") != REPORT_CONTRACT_VERSION:
            reasons.append("report contract is missing or superseded")
        if snapshot.get("source_coverage_complete") is not True:
            reasons.append("required source coverage is incomplete")
        if snapshot.get("source_failure_details"):
            reasons.append("target/source failure envelopes are present")
        fingerprint = snapshot.get("reviewer_input_fingerprint")
        if (
            not fingerprint
            or fingerprint != job.get("reviewer_input_fingerprint")
        ):
            reasons.append("reviewer input fingerprint is missing or mismatched")
        candidate_contracts = [
            (candidate.get("dossier_evidence_contract") or {}).get(
                "contract_version")
            for candidate in snapshot.get("candidates", [])
        ]
        if (
            not candidate_contracts
            or any(version != REPORT_CONTRACT_VERSION
                   for version in candidate_contracts)
        ):
            reasons.append("candidate dossier contract is missing or superseded")
        candidate_coverage = [
            candidate.get("candidate_source_coverage") or {}
            for candidate in snapshot.get("candidates", [])
        ]
        if (
            not candidate_coverage
            or any(coverage.get("complete") is not True
                   for coverage in candidate_coverage)
        ):
            reasons.append("candidate-level required source coverage is incomplete")
        actual_snapshot_hash = hashlib.sha256(
            canonical_json_bytes(snapshot)).hexdigest()
        if not job.get("candidate_snapshot_sha256") or not hmac.compare_digest(
                actual_snapshot_hash, str(job.get("candidate_snapshot_sha256"))):
            reasons.append("candidate snapshot hash is missing or mismatched")

    report_artifact = None
    try:
        report_artifact = jobs_db.get_job_artifact(str(job.get("job_id")), "report_md")
    except Exception:
        pass
    if not report_artifact:
        reasons.append("persisted report Markdown artifact is missing")
    report_bytes: Optional[bytes] = (bytes(report_artifact["payload"])
                                     if report_artifact else None)
    if report_bytes is not None:
        actual_hash = hashlib.sha256(report_bytes).hexdigest()
        if not job.get("report_sha256") or not hmac.compare_digest(
                actual_hash, str(job.get("report_sha256"))):
            reasons.append("report hash is missing or mismatched")
        expected_binding = _snapshot_binding_line(
            str(job.get("candidate_snapshot_sha256") or ""))
        try:
            report_text = report_bytes.decode("utf-8")
        except UnicodeDecodeError:
            report_text = ""
        if not report_text.startswith(
                "<!-- AgentBio immutable decision provenance -->\n"
                f"{expected_binding}\n"):
            reasons.append(
                "report does not contain the exact candidate snapshot binding")
    # A current dossier is actionable only when its final PDF and evidence
    # package are durable and bind to the same frozen report/snapshot pair.
    try:
        pdf_artifact = jobs_db.get_job_artifact(str(job.get("job_id")), "report_pdf")
        zip_artifact = jobs_db.get_job_artifact(str(job.get("job_id")), "evidence_zip")
    except Exception:
        pdf_artifact = zip_artifact = None
    for label, artifact in (("persisted report PDF", pdf_artifact),
                            ("persisted evidence ZIP", zip_artifact)):
        if not artifact:
            reasons.append(f"{label} is missing")
        elif (artifact.get("report_sha256") != job.get("report_sha256")
              or artifact.get("candidate_snapshot_sha256")
              != job.get("candidate_snapshot_sha256")
              or hashlib.sha256(bytes(artifact.get("payload") or b"")).hexdigest()
              != artifact.get("content_sha256")):
            reasons.append(f"{label} provenance is mismatched")
    if zip_artifact:
        try:
            if not (snapshot and report_artifact and pdf_artifact):
                raise ValueError("bound authorities missing")
            cif_artifacts = []
            for metadata in jobs_db.list_job_artifacts(
                    str(job.get("job_id")), "cif"):
                artifact = jobs_db.get_job_artifact(
                    str(job.get("job_id")), "cif", metadata["artifact_id"])
                if artifact:
                    cif_artifacts.append(artifact)
            _validate_evidence_zip(job, snapshot, report_artifact, pdf_artifact,
                                   zip_artifact, cif_artifacts)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError,
                zipfile.BadZipFile):
            reasons.append("evidence ZIP manifest is invalid")
    return {
        "actionable": not reasons,
        "stale_policy": None if not reasons else _STALE_POLICY,
        "stale_reasons": reasons,
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
    claimed_route: Optional[str] = None
    claimed_dose: Optional[str] = None
    claimed_modality: Optional[str] = None
    claimed_context: Optional[str] = None


class ClaimContext(BaseModel):
    route: Optional[str] = None
    dose: Optional[str] = None
    modality: Optional[str] = None
    context: Optional[str] = None


class TriageRequest(BaseModel):
    disease_name: str
    drug_names: list[str]
    job_id: Optional[str] = None  # hint an existing job to bypass DB search
    claim_contexts: Optional[dict[str, ClaimContext]] = None


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
        source_health = probe_required_sources()
        if not source_health["healthy"]:
            unavailable = [
                f"{name} ({status.get('error') or 'unavailable'})"
                for name, status in source_health["sources"].items()
                if not status.get("available")
            ]
            jobs_db.update_job_status(
                job_id,
                status="source_unavailable",
                current_stage="done",
                error_message=(
                    "Required source admission check failed: "
                    f"{'; '.join(unavailable)}. This is retryable; retry the "
                    "research run when the external source has recovered."
                ),
            )
            return
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

        # Set when the pre-dossier eligibility gate terminates the run: the
        # pipeline ran correctly but had nothing recommendable, which is a
        # distinct outcome from both "completed" and "error".
        ineligible: dict[str, Any] = {}

        for chunk in graph.stream(initial_state, config=config, stream_mode="updates"):
            if "__interrupt__" in chunk:
                # human_review reached: pipeline paused for the reviewer.
                jobs_db.update_job_status(
                    job_id, status="awaiting_review",
                    current_stage="awaiting_review")
                return

            for node, value in chunk.items():
                if node == "eligibility_gate":
                    verdict = (value or {}).get("eligibility") or {}
                    if not verdict.get("eligible"):
                        ineligible = verdict
                    continue
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
                        fields.update(_persist_actionable_report(
                            job_id, reports[0]["path"]))

                jobs_db.update_job_status(job_id, **fields)

        if ineligible:
            # Correct pipeline execution with nothing recommendable. Reported as
            # its own terminal state so it is never mistaken for a signed-off
            # dossier, and never sent to a human as an Approve/Reject decision.
            jobs_db.update_job_status(
                job_id,
                status=ineligible.get("terminal_status") or "no_eligible_candidate",
                current_stage="done",
                error_message=ineligible.get("terminal_reason") or ineligible.get("reason") or
                "No eligible repurposing candidate found.",
            )
            return

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


def _report_download_name(job: dict[str, Any]) -> str:
    """A header-safe, stable attachment name; never reflect arbitrary DB text."""
    disease = re.sub(r"[^A-Za-z0-9]+", "-", str(job.get("disease_name") or "case"))
    disease = disease.strip("-").lower()[:60] or "case"
    job_id = re.sub(r"[^A-Za-z0-9_-]", "", str(job.get("job_id") or ""))[:32]
    return f"agentbio-{disease}-{job_id or 'report'}.pdf"


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
@app.get("/api/how-it-works")
def how_it_works() -> PlainTextResponse:
    """Serve docs/HOW_AGENTBIO_WORKS.md so the UI's How It Works tab always
    renders the same engineering reference that ships in the repo."""
    doc = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "docs", "HOW_AGENTBIO_WORKS.md")
    if not os.path.isfile(doc):
        raise HTTPException(404, "docs/HOW_AGENTBIO_WORKS.md not found")
    with open(doc, "r", encoding="utf-8") as fh:
        return PlainTextResponse(fh.read(), media_type="text/markdown; charset=utf-8")


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
    # The deployed jobs schema has no separate JSON terminal-metadata columns.
    # Preserve the durable, schema-backed status/error fields while exposing a
    # stable terminal contract to API consumers.
    if job["status"] in (
            "no_eligible_candidate", "source_unavailable", "degraded_unscorable"):
        job["terminal_reason"] = job.get("error_message")
        job["retryable"] = job["status"] in (
            "source_unavailable", "degraded_unscorable")
    if job["status"] in ("awaiting_review", "completed"):
        report_artifact = jobs_db.get_job_artifact(job_id, "report_md")
        job["report"] = (bytes(report_artifact["payload"]).decode("utf-8")
                         if report_artifact else _read_report(job.get("report_path")))
    artifacts = jobs_db.list_job_artifacts(job_id)
    artifact_view: dict[str, Any] = {"cifs": []}
    for artifact in artifacts:
        item = dict(artifact)
        item["url"] = _artifact_url(
            job_id, item["kind"],
            item["artifact_id"] if item["kind"] == "cif" else None)
        if item["kind"] == "cif":
            artifact_view["cifs"].append(item)
        else:
            artifact_view[item["kind"]] = item
    job["artifacts"] = artifact_view
    job.update(_actionability(job))
    structure_accounted = (
        job.get("current_stage") in {
            "structure_validation", "writer", "awaiting_review", "done"}
        or job.get("status") in {"awaiting_review", "completed"}
    )
    job["run_accounting"] = {
        "structure_validation": {
            "status": "OBSERVED" if structure_accounted else "UNKNOWN",
            "cost_usd": (
                job.get("total_cost_usd") if structure_accounted else None
            ),
        },
        "llm": {
            "status": "UNKNOWN",
            "cost_usd": None,
            "reason": (
                "Provider telemetry is not yet durably attributable per run; "
                "no process-global or estimated USD value is reported."
            ),
        },
    }
    if not structure_accounted:
        # The database's historical 0.0 default is not evidence that no cost
        # occurred. Expose unknown until the metered structure node reports.
        job["total_cost_usd"] = None
    return job


@app.get("/api/runs/{job_id}/report.pdf")
def download_case_report_pdf(job_id: str) -> Response:
    """Download the persisted case-report snapshot as a server-rendered PDF."""
    job = jobs_db.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    artifact = jobs_db.get_job_artifact(job_id, "report_pdf")
    if artifact:
        return _artifact_response(artifact)
    report = _read_report(job.get("report_path"))
    if not report:
        raise HTTPException(status_code=404, detail="persisted report snapshot not found")
    try:
        report_path = job.get("report_path")
        frozen_job = dict(job)
        policy = _actionability(job)
        frozen_job.update(policy)
        if not policy["actionable"]:
            report = (
                "# SUPERSEDED POLICY SNAPSHOT — HISTORICAL ONLY\n\n"
                "**This report cannot be approved.** "
                f"{'; '.join(policy['stale_reasons'])}\n\n---\n\n"
                + report
            )
        if report_path and os.path.exists(report_path):
            frozen_job["report_snapshot_at"] = datetime.fromtimestamp(
                os.path.getmtime(report_path), tz=timezone.utc
            ).isoformat()
        else:
            frozen_job["report_snapshot_at"] = job.get("created_at")
        payload = render_case_pdf(report, frozen_job)
    except Exception as exc:  # rendering failures should be explicit to users
        raise HTTPException(status_code=500, detail=f"could not render report PDF: {exc}") from exc
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{_report_download_name(job)}"',
            "Cache-Control": "private, no-store",
        },
    )


def _artifact_response(artifact: dict[str, Any]) -> Response:
    """Serve immutable database bytes with content-addressed HTTP semantics."""
    filename = re.sub(r'[^A-Za-z0-9._-]', "_", str(artifact["filename"]))
    return Response(
        content=bytes(artifact["payload"]),
        media_type=str(artifact["content_type"]),
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "ETag": f'"{artifact["content_sha256"]}"',
            "Cache-Control": "public,max-age=31536000,immutable",
        },
    )


@app.get("/api/runs/{job_id}/artifacts/{kind}")
def download_artifact(job_id: str, kind: str) -> Response:
    kind = {"report.md": "report_md", "report.pdf": "report_pdf",
            "evidence.zip": "evidence_zip"}.get(kind, kind)
    if kind not in ("report_md", "report_pdf", "evidence_zip"):
        raise HTTPException(status_code=404, detail="artifact kind not found")
    artifact = jobs_db.get_job_artifact(job_id, kind)
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    return _artifact_response(artifact)


@app.get("/api/runs/{job_id}/artifacts/cif/{artifact_id}")
def download_cif_artifact(job_id: str, artifact_id: str) -> Response:
    if not _SHA256_RE.fullmatch(artifact_id):
        raise HTTPException(status_code=404, detail="artifact not found")
    artifact = jobs_db.get_job_artifact(job_id, "cif", artifact_id)
    if not artifact:
        raise HTTPException(status_code=404, detail="artifact not found")
    return _artifact_response(artifact)


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
    policy = _actionability(job)
    if not policy["actionable"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": _STALE_POLICY,
                "stale_reasons": policy["stale_reasons"],
            },
        )
    claimed = jobs_db.claim_job_for_review(job_id)
    if claimed is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Review decision already claimed by another request; this "
                "request did not invoke resume_run."
            ),
        )
    # Persist attempted-decision context for crash recovery without treating the
    # attempt as a completed durable decision.
    jobs_db.update_job_status(
        job_id,
        status="reviewing",
        error_message=(
            f"Attempted decision '{action}' was atomically claimed; checkpoint "
            "side effects remain uncertain until completion."
        ),
        review_notes=req.notes or "",
    )

    try:
        review = resume_run(claimed["thread_id"], action, req.notes or "")
    except Exception as exc:  # noqa: BLE001
        jobs_db.update_job_status(
            job_id,
            status="error",
            error_message=(
                f"Review decision attempt '{action}' failed after atomic claim; "
                "checkpoint side effects may have occurred and the job was not "
                f"restored to awaiting_review: {exc}"
            ),
            review_notes=req.notes or "",
        )
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
    structure_accounted = (
        job.get("current_stage") in {
            "structure_validation", "writer", "awaiting_review", "done"}
        or job.get("status") in {"awaiting_review", "completed"}
    )
    return {
        "job_id": job_id,
        "total_cost_usd": (
            job.get("total_cost_usd") if structure_accounted else None
        ),
        "accounting": {
            "structure_validation": (
                "OBSERVED" if structure_accounted else "UNKNOWN"),
            "llm": "UNKNOWN",
            "llm_cost_usd": None,
        },
    }


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
        claimed_route=(req.claimed_route or "").strip(),
        claimed_dose=(req.claimed_dose or "").strip(),
        claimed_modality=(req.claimed_modality or "").strip(),
        claimed_context=(req.claimed_context or "").strip(),
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
        claim_contexts={
            name: context.model_dump()
            for name, context in (req.claim_contexts or {}).items()
        },
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


# Allow-listed frozen validation reports, served read-only for in-app reading
# from the Research tab's benchmark cards. Entries must point at committed,
# frozen artifacts only — this endpoint never writes, regenerates, or serves
# mutable content. Adding a report means adding a line here, nothing else.
_BENCHMARK_REPORTS = {
    "benchmark-v2": ("Benchmark v2 — pre-registered protocol",
                     "benchmark_v2_preregistration.md"),
    "engineering-acceptance": ("Engineering acceptance — full report",
                               "engineering_acceptance_results.md"),
    "small-molecule": ("Small-molecule retrospective — full report",
                       "repodb_results_smallmol.md"),
    "top-k": ("Top-K retrospective — full report",
              "repodb_results_topk.md"),
    "audit-trap": ("Audit trap benchmark — full report",
                   "audit_trap_results.md"),
}


@app.get("/api/research/benchmark-report/{report_id}")
def get_benchmark_report(report_id: str) -> dict[str, str]:
    """Serve one frozen validation report as markdown for in-app viewing.

    Read-only and allow-listed: only committed artifacts in validation/ are
    servable; unknown ids and missing files both 404.
    """
    entry = _BENCHMARK_REPORTS.get(report_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown benchmark report id")
    title, filename = entry
    path = os.path.join(_VALIDATION_DIR, filename)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404,
                            detail=f"report file {filename} not present")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            markdown = fh.read()
    except OSError as exc:
        raise HTTPException(status_code=503,
                            detail=f"report file unreadable: {exc}") from exc
    return {"id": report_id, "title": title, "markdown": markdown}


@app.get("/api/research/benchmarks")
def get_research_benchmarks() -> dict[str, Any]:
    """Expose existing validation artifacts with their provenance and limits."""
    # report_id must be a key of _BENCHMARK_REPORTS — the frontend renders the
    # "Read full report" button from it, so it must stay stable even if the
    # display label copy changes.
    artifacts = [
        ("Engineering acceptance", "engineering_acceptance_results.json",
         "engineering-acceptance"),
        ("Small-molecule retrospective", "repodb_results_smallmol.json",
         "small-molecule"),
        ("Top-K retrospective", "repodb_results_topk.json", "top-k"),
    ]
    summaries = []
    for label, filename, report_id in artifacts:
        artifact = _load_validation_artifact(filename)
        if artifact is not None:
            summary = _benchmark_summary(artifact, label)
            summary["report_id"] = report_id
            summaries.append(summary)
    trap_artifact = _load_validation_artifact("audit_trap_results.json")
    if trap_artifact is not None:
        trap_summary = _audit_trap_summary(trap_artifact)
        trap_summary["report_id"] = "audit-trap"
        summaries.append(trap_summary)
    v2_status = inspect_frozen_result()
    return {
        "benchmarks": summaries,
        "pilot_status": (
            "complete_frozen" if v2_status["complete"]
            else "frozen_artifact_requires_review"
        ),
        "pilot_note": (
            "Benchmark v2 completed as the single pre-registered run on "
            "August 9, 2026. Its frozen result is reported separately from the "
            "historical development artifacts below and must not be rerun or "
            "pooled with them."
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
_BENCH_DONE = os.path.join(_BENCH_DIR, ".prod_benchmark_done")
_BENCH_PAUSE = os.path.join(_BENCH_DIR, ".prod_benchmark_pause")
_BENCH_ATTEMPTS = os.path.join(_BENCH_DIR, ".v2_control_attempts")


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


def _bench_read_json(path: str) -> Optional[dict]:
    """Read a checkpoint the supervisor may be mid-write on; one short retry."""
    import time as _time
    for attempt in range(2):
        try:
            with open(path) as f:
                return json.load(f)
        except json.JSONDecodeError:
            if attempt == 0:
                _time.sleep(0.5)
        except OSError:
            return None
    return None


def _bench_supervisor_alive() -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "prod_benchmark_supervisor.sh"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


@app.get("/internal/benchmark-status")
def benchmark_status() -> dict:
    """Read-only state of the completed, frozen benchmark-v2 run."""
    frozen_completion = inspect_frozen_result(_BENCH_RESULTS_JSON)
    status: dict[str, Any] = {
        "supervisor_log": _BENCH_LOG,
        "supervisor_alive": _bench_supervisor_alive(),
        "paused_before_benchmark": os.path.exists(_BENCH_PAUSE),
        "control_discard_attempts": None,
        "phase": frozen_completion["phase"],
        "rerun_allowed": False,
        "frozen_completion": frozen_completion,
    }
    try:
        with open(_BENCH_ATTEMPTS) as f:
            status["control_discard_attempts"] = int(f.read().strip() or 0)
    except (OSError, ValueError):
        pass
    if os.path.exists(_BENCH_DONE):
        try:
            with open(_BENCH_DONE) as f:
                status["terminal_state"] = f.read().strip()
        except OSError:
            status["terminal_state"] = "unknown"
    control = _bench_read_json(_BENCH_CONTROL_JSON)
    if control is not None:
        rows = control.get("rows", [])
        status["control"] = {
            "rows_completed": len(rows),
            "rows_expected": 52,
            "snapshots": len(control.get("target_snapshots", [])),
            "snapshots_expected": 13,
            "hits": sum(1 for r in rows if r.get("generated")),
            **_bench_file_meta(_BENCH_CONTROL_JSON),
        }
    else:
        status["control"] = _bench_file_meta(_BENCH_CONTROL_JSON)
    status["case_list"] = _bench_file_meta(_BENCH_CASE_LIST)
    results = _bench_read_json(_BENCH_RESULTS_JSON)
    if results is not None:
        cases = results.get("cases", results.get("results", []))
        status["benchmark"] = {
            "cases_completed": len(cases) if isinstance(cases, list) else None,
            **_bench_file_meta(_BENCH_RESULTS_JSON),
        }
    else:
        status["benchmark"] = _bench_file_meta(_BENCH_RESULTS_JSON)
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
            data = _bench_read_json(path)
            if data is None:
                raise HTTPException(status_code=503, detail=f"artifact {label} is mid-write; retry")
            return JSONResponse({"artifact": label, "data": data})
    raise HTTPException(status_code=404, detail="no benchmark artifacts yet")


@app.get("/internal/benchmark-log")
def benchmark_log(lines: int = 200) -> dict:
    """Tail of the production benchmark supervisor log (preflight + harness output).

    Needed to diagnose control discard/validation events, which the short
    status tail cannot reach once verbose arm output scrolls past them.
    """
    n = max(1, min(lines, 2000))
    try:
        with open(_BENCH_LOG) as f:
            content = f.read().splitlines()
    except OSError:
        raise HTTPException(status_code=404, detail="supervisor log not found")
    tail = [ln for ln in content if "MorganGenerator" not in ln][-n:]
    return {"lines": len(tail), "total_lines": len(content), "log": tail}


@app.post("/internal/benchmark-greenlight")
def benchmark_greenlight() -> dict:
    """Refuse a second execution of the one-shot, completed v2 benchmark."""
    frozen = inspect_frozen_result(_BENCH_RESULTS_JSON)
    raise HTTPException(
        status_code=409,
        detail=(
            "Benchmark v2 already completed as the single pre-registered run "
            f"({frozen['completed_on']}). Its frozen result cannot be greenlit "
            "or rerun."
        ),
    )


# --------------------------------------------------------------------------- #
# Study B 24/7 supervisor (Reserved VM; read-only progress + pull-back)
# --------------------------------------------------------------------------- #

_STUDYB_LOG = os.path.join(_BENCH_DIR, "prod_studyb.log")
_STUDYB_RESULTS = os.path.join(_BENCH_DIR, "triage_discrimination_studyb_results.json")
_STUDYB_CKPT = os.path.join(_BENCH_DIR, "triage_discrimination_studyb_checkpoint.jsonl")
_STUDYB_DONE = os.path.join(_BENCH_DIR, ".prod_studyb_done")
_STUDYB_HEAD = os.path.join(_BENCH_DIR, ".prod_studyb_freeze_head")
# sha256 of the PubChem snapshot is expensive (whole-file read); cache it and
# only re-hash when the file changes. Same for the checkpoint pool/target
# counts, which otherwise parse a 339MB JSONL on every status poll.
_SNAP_SHA_CACHE: dict = {}
_CKPT_COUNT_CACHE: dict = {}


def _studyb_freeze_head() -> Optional[str]:
    try:
        with open(_STUDYB_HEAD) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _studyb_supervisor_alive() -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "prod_studyb_supervisor.sh"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


@app.get("/internal/studyb-status")
def studyb_status() -> dict:
    """Read-only progress of the 24/7 Study B pool rebuild."""
    status: dict[str, Any] = {
        "supervisor_log": _STUDYB_LOG,
        "supervisor_alive": _studyb_supervisor_alive(),
        "freeze_head": _studyb_freeze_head(),
        "results": _bench_file_meta(_STUDYB_RESULTS),
    }
    try:
        from data_sources import pubchem_snapshot as _snap
        snap_path = _snap.SNAPSHOT_PATH
        mtime = os.path.getmtime(snap_path) if snap_path.exists() else None
        if mtime is None:
            status["pubchem_snapshot_sha256"] = None
        else:
            if _SNAP_SHA_CACHE.get("mtime") != mtime:
                _SNAP_SHA_CACHE["mtime"] = mtime
                _SNAP_SHA_CACHE["sha256"] = _snap.sha256()
            status["pubchem_snapshot_sha256"] = _SNAP_SHA_CACHE.get("sha256")
    except Exception:  # noqa: BLE001 — status endpoint must stay read-only-safe
        status["pubchem_snapshot_sha256"] = None
    if os.path.exists(_STUDYB_DONE):
        try:
            with open(_STUDYB_DONE) as f:
                status["terminal_state"] = f.read().strip()
        except OSError:
            status["terminal_state"] = "unknown"
    targets = pools = 0
    try:
        # Cache the parse by mtime: pool records embed full ranked pools
        # (~70MB of JSON per line), so a fresh parse costs real CPU exactly
        # when the box is busiest. The file only changes when a pool or
        # target finalizes, so mtime-keyed caching keeps this endpoint cheap
        # and still correct. (A raw byte-scan is NOT safe: nested candidate
        # payloads contain the same "kind" markers and overcount.)
        mtime = os.path.getmtime(_STUDYB_CKPT)
        if _CKPT_COUNT_CACHE.get("mtime") != mtime:
            t = p = 0
            with open(_STUDYB_CKPT) as f:
                for line in f:
                    if not line.strip():
                        continue
                    kind = json.loads(line).get("kind")
                    if kind == "target":
                        t += 1
                    elif kind == "pool":
                        p += 1
            _CKPT_COUNT_CACHE.clear()
            _CKPT_COUNT_CACHE.update({"mtime": mtime, "targets": t, "pools": p})
        targets = _CKPT_COUNT_CACHE["targets"]
        pools = _CKPT_COUNT_CACHE["pools"]
    except (OSError, json.JSONDecodeError):
        pass
    status["checkpoint"] = {"targets_finalized": targets,
                            "pools_finalized": pools,
                            **_bench_file_meta(_STUDYB_CKPT)}
    tail: list[str] = []
    try:
        # Bounded tail-read: the prod supervisor log grows unbounded across
        # restarts; never read the whole file on a status call.
        with open(_STUDYB_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            tail = [ln for ln in f.read().decode("utf-8", "replace").splitlines()
                    if "MorganGenerator" not in ln][-15:]
    except OSError:
        pass
    status["log_tail"] = tail
    return status


@app.get("/internal/studyb-results")
def studyb_results() -> JSONResponse:
    """Return the Study B results artifact so it can be pulled back to dev.

    The Reserved VM disk is wiped on restart/redeploy; pull this BEFORE any
    republish so the frozen result is committed in the repo.
    """
    if not os.path.exists(_STUDYB_RESULTS):
        raise HTTPException(status_code=404, detail="no studyb results yet")
    data = _bench_read_json(_STUDYB_RESULTS)
    if data is None:
        raise HTTPException(status_code=503, detail="results mid-write; retry")
    return JSONResponse({"artifact": "triage_discrimination_studyb", "data": data})


@app.get("/internal/studyb-checkpoint")
def studyb_checkpoint() -> FileResponse:
    """Stream the raw Study B checkpoint so dev can resume from prod progress
    after a republish (prod disk is wiped on redeploy).

    Streams instead of parsing into a JSON envelope: the checkpoint is
    hundreds of MB, and building the envelope in memory on the 1-vCPU prod
    box starved uvicorn (LB health checks failed, backend pulled). The
    freeze-head pin rides in the X-Freeze-Head header: without it, restoring
    after a prod disk wipe would let a newer deployment mint a fresh pin and
    resume old work under changed code.
    """
    if not os.path.exists(_STUDYB_CKPT):
        raise HTTPException(status_code=404, detail="no studyb checkpoint yet")
    return FileResponse(
        _STUDYB_CKPT,
        media_type="application/x-ndjson",
        filename="triage_discrimination_studyb_checkpoint.jsonl",
        headers={"X-Freeze-Head": _studyb_freeze_head() or ""})


# --------------------------------------------------------------------------- #
# Study C (triage discrimination: confirmed positives vs genuine negatives)
# --------------------------------------------------------------------------- #

_STUDYC_LOG = os.path.join(_BENCH_DIR, "prod_studyc.log")
_STUDYC_RESULTS = os.path.join(_BENCH_DIR, "triage_discrimination_studyc_results.json")
_STUDYC_CKPT = os.path.join(_BENCH_DIR, "triage_discrimination_studyc_checkpoint.jsonl")
_STUDYC_DONE = os.path.join(_BENCH_DIR, ".prod_studyc_done")
_STUDYC_COUNT_CACHE: dict = {}


def _studyc_supervisor_alive() -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "prod_studyc_supervisor.sh"],
            capture_output=True, text=True, timeout=5,
        )
        return bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


@app.get("/internal/studyc-status")
def studyc_status() -> dict:
    """Read-only progress of the 24/7 Study C discrimination run."""
    status: dict[str, Any] = {
        "supervisor_log": _STUDYC_LOG,
        "supervisor_alive": _studyc_supervisor_alive(),
        "results": _bench_file_meta(_STUDYC_RESULTS),
    }
    if os.path.exists(_STUDYC_DONE):
        try:
            with open(_STUDYC_DONE) as f:
                status["terminal_state"] = f.read().strip()
        except OSError:
            status["terminal_state"] = "unknown"
    targets = pools = 0
    try:
        # Same mtime-cached parse discipline as studyb-status: pool records
        # embed full ranked pools, so an uncached parse per poll would starve
        # the 1-vCPU prod box exactly when it is busiest.
        mtime = os.path.getmtime(_STUDYC_CKPT)
        if _STUDYC_COUNT_CACHE.get("mtime") != mtime:
            t = p = 0
            with open(_STUDYC_CKPT) as f:
                for line in f:
                    if not line.strip():
                        continue
                    kind = json.loads(line).get("kind")
                    if kind == "target":
                        t += 1
                    elif kind == "pool":
                        p += 1
            _STUDYC_COUNT_CACHE.clear()
            _STUDYC_COUNT_CACHE.update(
                {"mtime": mtime, "targets": t, "pools": p})
        targets = _STUDYC_COUNT_CACHE["targets"]
        pools = _STUDYC_COUNT_CACHE["pools"]
    except (OSError, json.JSONDecodeError):
        pass
    status["checkpoint"] = {"targets_finalized": targets,
                            "pools_finalized": pools,
                            **_bench_file_meta(_STUDYC_CKPT)}
    tail: list[str] = []
    try:
        # Bounded tail-read: the prod supervisor log grows unbounded across
        # restarts; never read the whole file on a status call.
        with open(_STUDYC_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 256 * 1024))
            tail = [ln for ln in f.read().decode("utf-8", "replace").splitlines()
                    if "MorganGenerator" not in ln][-15:]
    except OSError:
        pass
    status["log_tail"] = tail
    return status


@app.get("/internal/studyc-results")
def studyc_results() -> JSONResponse:
    """Return the Study C results artifact so it can be pulled back to dev.

    The Reserved VM disk is wiped on restart/redeploy; pull this BEFORE any
    republish so the frozen result is committed in the repo.
    """
    if not os.path.exists(_STUDYC_RESULTS):
        raise HTTPException(status_code=404, detail="no studyc results yet")
    data = _bench_read_json(_STUDYC_RESULTS)
    if data is None:
        raise HTTPException(status_code=503, detail="results mid-write; retry")
    return JSONResponse({"artifact": "triage_discrimination_studyc",
                         "data": data})


@app.get("/internal/studyc-checkpoint")
def studyc_checkpoint() -> FileResponse:
    """Stream the raw Study C checkpoint for pull-back before a republish.

    Streams for the same reason as /internal/studyb-checkpoint.
    """
    if not os.path.exists(_STUDYC_CKPT):
        raise HTTPException(status_code=404, detail="no studyc checkpoint yet")
    return FileResponse(
        _STUDYC_CKPT,
        media_type="application/x-ndjson",
        filename="triage_discrimination_studyc_checkpoint.jsonl")


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
            # row_key must be NULL-safe: outcome_framing (and, defensively,
            # hypothesis_id) can be NULL on pre-debug rows, and registry_reset_backup
            # .row_key is NOT NULL — a bare || chain produced NULL and 500'd the reset.
            cur.execute(
                "INSERT INTO registry_reset_backup (source_table, row_key, payload) "
                "SELECT 'bisociation_history', "
                "  COALESCE(hypothesis_id, 'no-hypothesis') || '/' "
                "  || COALESCE(outcome_framing, 'no-framing') || '#' || id, "
                "  to_jsonb(t) "
                f"FROM bisociation_history t WHERE {_DEL}"
            )
            cur.execute(f"DELETE FROM bisociation_history WHERE {_DEL}")
            bh = cur.rowcount
            cur.execute(
                "INSERT INTO registry_reset_backup (source_table, row_key, payload) "
                "SELECT 'hypothesis_log', "
                "  COALESCE(test_id, hypothesis_id, md5(to_jsonb(t)::text)), "
                "  to_jsonb(t) "
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


def _sanitize_run_label(label: Optional[str]) -> Optional[str]:
    """Slugify an optional run-family label (a-z, 0-9, dash; max 24 chars).

    The label prefixes every run_id of the batch so a deliberate NEW research
    family is identifiable in the registry WITHOUT deleting history — the
    cumulative FDR family intentionally spans all runs, old and new (wiping
    history to lighten the multiple-testing burden would be optional stopping).
    """
    if not label:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")[:24].strip("-")
    return slug or None


def _run_discovery_batch_job(job_id: str, run_label: Optional[str] = None) -> None:
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
        prefix = f"{run_label}-" if run_label else ""
        summary = _RD.run_batch(run_id=f"{prefix}run-{job_id[:8]}")
        research_db.update_job(job_id, status="completed",
                               result_json=_json.dumps({"mode": "autonomous_discovery",
                                                         "summary": summary}))
    except Exception as exc:  # noqa: BLE001
        research_db.update_job(job_id, status="error", error_message=str(exc))
    finally:
        with _discovery_lock:
            if _discovery_active_job["job_id"] == job_id:
                _discovery_active_job["job_id"] = None


def _run_continuous_discovery_job(job_id: str, run_label: Optional[str] = None,
                                  max_batches: Optional[int] = None) -> None:
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
            run_id_prefix=f"{run_label}-" if run_label else "",
            hard_max_batches=_RD.clamp_max_batches(max_batches),
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


class DiscoveryRunRequest(BaseModel):
    # Optional family label; prefixes run_ids so a deliberately NEW research
    # family is identifiable without deleting registry history (which would
    # reset the cumulative FDR family — optional stopping).
    run_label: Optional[str] = None
    # Optional continuous-mode batch cap (spend bound). None -> server default
    # (DEFAULT_MAX_BATCHES); clamped to [1, HARD_MAX_BATCHES]. Continuous mode
    # only — ignored by the single-batch endpoint.
    max_batches: Optional[int] = None


@app.post("/api/research/discovery-batch")
def run_discovery_batch(request: Request,
                        body: Optional[DiscoveryRunRequest] = None) -> dict:
    """
    Start a full AUTONOMOUS discovery batch: two independent generators
    (Claude Opus 4.8 + GPT-5.6 Sol) each propose their own bisociative domains,
    a lead reviewer (Opus) consolidates them, and every READY hypothesis is
    tested on the discovery split, FDR-corrected over the whole cumulative log,
    then confirmed on the holdout half and confound-checked. NO user hypothesis
    is provided — the models pick their own domains.

    Optional JSON body: {"run_label": "<slug>"} tags every run_id of this batch
    with a family prefix (e.g. "fresh26-run-a1b2c3d4"). The label is a naming
    aid only — it does NOT alter the cumulative FDR family.

    Runs in a background daemon thread; poll GET /api/research/jobs/{job_id}.
    Guardrails (this batch is expensive — many LLM calls):
    - at most one batch runs at a time (409 if one is already in flight);
    - the same per-IP hourly rate limit as POST /api/runs (429 when exceeded).
    """
    # Per-IP hourly limit first, so an abusive caller is bounded even between
    # batches (the 409 single-run guard only bounds concurrent overlap).
    _guardrails.check_ip_rate_limit(request)

    run_label = _sanitize_run_label(body.run_label if body else None)

    with _discovery_lock:
        if _discovery_active_job["job_id"] is not None:
            raise HTTPException(
                status_code=409,
                detail="a discovery batch is already running",
            )
        desc = "(autonomous discovery batch — no user hypothesis)"
        if run_label:
            desc = f"(autonomous discovery batch — family '{run_label}', no user hypothesis)"
        job_id = research_db.create_job(desc)
        _discovery_active_job["job_id"] = job_id

    # If the thread fails to start, release the single-run slot so the endpoint
    # doesn't get wedged in a permanently-"busy" state.
    try:
        t = threading.Thread(target=_run_discovery_batch_job,
                             args=(job_id, run_label), daemon=True)
        t.start()
    except Exception as exc:  # noqa: BLE001
        with _discovery_lock:
            if _discovery_active_job["job_id"] == job_id:
                _discovery_active_job["job_id"] = None
        research_db.update_job(job_id, status="error", error_message=f"failed to start: {exc}")
        raise HTTPException(status_code=500, detail="failed to start discovery batch") from exc

    return {"job_id": job_id}


@app.post("/api/research/discovery-continuous")
def run_continuous_discovery(request: Request,
                             body: Optional[DiscoveryRunRequest] = None) -> dict:
    """
    Start continuous autonomous discovery batches, chaining until EITHER:
      - at least one hypothesis achieves a double-pass (discovery AND confirmation), OR
      - the batch cap is reached (body "max_batches", default 10, ceiling 40), OR
      - the 6-hour time bound is hit or batches keep failing, OR
      - the caller stops the run via POST .../stop.

    The 20-domain / 50-hypothesis budgets are SOFT: exceeding them is reported
    but does not end the run. A cap stop is reported as "search did not finish",
    never as a negative result.

    Optional JSON body: {"run_label": "<slug>"} tags every batch's run_id with
    a family prefix (naming aid only — the cumulative FDR family is unchanged).

    Uses the same _discovery_lock as single-batch runs so at most one
    autonomous job (single or continuous) can run at a time.
    Poll GET /api/research/jobs/{job_id} for live per-batch progress.
    """
    _guardrails.check_ip_rate_limit(request)

    run_label = _sanitize_run_label(body.run_label if body else None)

    with _discovery_lock:
        if _discovery_active_job["job_id"] is not None:
            raise HTTPException(
                status_code=409,
                detail="a discovery batch is already running",
            )
        desc = "(continuous discovery — runs until double-pass or cap)"
        if run_label:
            desc = f"(continuous discovery — family '{run_label}', runs until double-pass or cap)"
        job_id = research_db.create_job(desc)
        _discovery_active_job["job_id"] = job_id

    try:
        t = threading.Thread(
            target=_run_continuous_discovery_job,
            args=(job_id, run_label, body.max_batches if body else None),
            daemon=True,
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


