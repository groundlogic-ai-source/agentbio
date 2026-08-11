"""
Sealed harness for the frozen audit claim set (audit_claimset_v1).

Runs the 100 pre-registered claims (validation/audit_claim_set_v1.json)
through the PRODUCTION audit path (api.audit.run_audit, narrate=False),
archives raw per-claim outputs BEFORE any metric is computed, then scores
mechanically per validation/audit_claimset_construction_protocol.md §6.

Pre-registered in validation/audit_claimset_preregistration.md (metrics and
thresholds) and validation/audit_claimset_construction_protocol.md (claim
construction and scoring rules). Exactly one scored run: a completed results
artifact refuses re-run (the only exception is the pre-registered
one-fix-one-rerun allowance for a proven harness defect).

LABEL GUARD: runs ONLY under --label audit_claimset_v1. This artifact must
never be reported as benchmark v2, engineering acceptance, or discovery
accuracy.

Usage:
    python3 -m validation.run_audit_claimset --label audit_claimset_v1
    python3 -m validation.run_audit_claimset --label audit_claimset_v1 \
        --recalc-only      # recompute metrics from the raw archive and
                           # compare against the stored results (no new runs)
    python3 -m validation.run_audit_claimset --label audit_claimset_v1 \
        --allow-rerun-after-harness-defect "<reason>"   # one-fix-one-rerun

Artifacts:
    validation/audit_claimset_raw_outputs.json  (raw per-claim audit outputs)
    validation/audit_claimset_results.json
    validation/audit_claimset_results.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REQUIRED_LABEL = "audit_claimset_v1"
FORBIDDEN_LABELS = {"benchmark_v2", "engineering_acceptance",
                    "audit_trap_benchmark"}

_HERE = os.path.dirname(os.path.abspath(__file__))
CLAIM_SET_JSON = os.path.join(_HERE, "audit_claim_set_v1.json")
FREEZE_MANIFEST_JSON = os.path.join(_HERE, "audit_claimset_freeze_manifest.json")
RAW_OUTPUTS_JSON = os.path.join(_HERE, "audit_claimset_raw_outputs.json")
RESULTS_JSON = os.path.join(_HERE, "audit_claimset_results.json")
RESULTS_MD = os.path.join(_HERE, "audit_claimset_results.md")
RERUN_CONSUMED_JSON = os.path.join(
    _HERE, "audit_claimset_rerun_allowance_consumed.json")

# Pre-registered thresholds (audit_claimset_preregistration.md +
# construction protocol §6). Never move these after a scored run — a failure
# is a product defect, not a threshold problem.
PASS_MIN_DEFECT_RECALL = 0.80
PASS_MIN_RECALL_CP_LOWER = 0.65
PASS_MAX_CONTROL_FFR = 0.15
PASS_MAX_FFR_CP_UPPER = 0.30
MAX_GROUP_ABSTENTION_FRAC = 0.10
CUTOFF = "2026-08-10"

GROUP_TOTALS = {"existing_fix": 30, "novel": 30, "control": 40}
DEFECT_GROUPS = ("existing_fix", "novel")

# Limitations prose recorded verbatim into the results artifact. These are
# v1's; a later study's wrapper (e.g. run_audit_claimset_v2) overrides this
# module constant before calling main() — it is module state, resolved at
# results-write time.
RESULTS_LIMITATIONS = [
    "N3 (species/preclinical-only) has ZERO claims: all externally "
    "verifiable candidates failed construction verification and the "
    "shortfall was reallocated per protocol §2. The N3 detector is "
    "untested by this study (synthetic unit coverage only).",
    "Persisted candidate pools predate the black-box/withdrawal "
    "classifier fix; E1/E2 disclosure TEXT may contradict external "
    "artifacts. This is measured by the non-scored disclosure "
    "annotation and never changes scored metrics.",
    "The citation cutoff (2026-08-10) is a mechanical artifact-date "
    "rule, not a judgment of evidence currency.",
    "Pool-context coverage is limited to three persisted cases; "
    "novel-lane claims are pool-free by design.",
]

HTTP_TIMEOUT = 20.0
PACE_SECONDS = 0.15

# Lanes: which audit-context source feeds each novel-detector finding.
FINDING_LANE = {"N1": "regulatory_label", "N2": "regulatory_label",
                "N3": "entity_linked_literature", "N4": "regulatory_label"}
LANE_FAILURE_STATES = {"degraded", "parse_failed", "unavailable"}

# The input fields a claim is allowed to forward to run_audit. NOTHING from
# claim["truth"] may reach the audit path (leakage guard).
_INPUT_TO_KWARG = (("route", "claimed_route"), ("dose", "claimed_dose"),
                   ("modality", "claimed_modality"),
                   ("context", "claimed_context"))


def refuse(msg: str) -> None:
    print(f"[harness] REFUSED: {msg}")
    raise SystemExit(2)


# --------------------------------------------------------------------------- #
# Minimal HTTP (same probe philosophy as the construction script)
# --------------------------------------------------------------------------- #

def _get_json(url: str, params: Optional[dict] = None) -> Optional[dict]:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "agentbio-audit-claimset/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    finally:
        time.sleep(PACE_SECONDS)


def health_gate() -> dict:
    """Live probes of all four audit-lane sources plus pool-job
    resolvability. Any failure refuses the run and produces no score."""
    probes = {
        "chembl": ("https://www.ebi.ac.uk/chembl/api/data/status.json", None),
        "openfda": ("https://api.fda.gov/drug/label.json",
                    {"search": 'openfda.generic_name:"IBUPROFEN"', "limit": 1}),
        "europepmc": ("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                      {"query": "EXT_ID:39084004", "format": "json"}),
        "pubtator": ("https://www.ncbi.nlm.nih.gov/research/pubtator3-api/search/",
                     {"text": "aspirin @@GENE_PTGS1"}),
    }
    results = {name: _get_json(url, params) is not None
               for name, (url, params) in probes.items()}
    failed = [name for name, ok in results.items() if not ok]
    if failed:
        refuse(f"live sources unhealthy: {failed}. Never score during an outage.")
    return results


def check_pool_jobs(claim_set: dict) -> None:
    """Every pool referenced by the claim set must resolve to a completed or
    awaiting_review job with a persisted candidates file."""
    import api.jobs_db as jobs_db
    for job_id in claim_set.get("pools_used_for_reachability", {}):
        job = jobs_db.get_job(job_id)
        if not job or job.get("status") not in ("completed", "awaiting_review"):
            refuse(f"pool job {job_id[:8]} not in completed/awaiting_review "
                   f"state: {job.get('status') if job else 'missing'}")
        path = os.path.join(os.path.dirname(_HERE), "output", "candidates",
                            f"{job_id}.json")
        if not os.path.exists(path):
            refuse(f"pool candidates file missing for job {job_id[:8]}")


# --------------------------------------------------------------------------- #
# Freeze manifest verification
# --------------------------------------------------------------------------- #

def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True,
                         cwd=os.path.dirname(_HERE))
    if out.returncode != 0:
        refuse(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def verify_freeze() -> dict:
    """Hash drift refuses the run. Checks: claim-set sha256 matches the
    manifest; the frozen code commit is an ancestor of HEAD; no .py file
    under the code paths has changed (committed or uncommitted) since."""
    if not os.path.exists(FREEZE_MANIFEST_JSON):
        refuse("freeze manifest missing — the study is not frozen")
    manifest = json.load(open(FREEZE_MANIFEST_JSON))

    digest = hashlib.sha256(open(CLAIM_SET_JSON, "rb").read()).hexdigest()
    if digest != manifest.get("claim_set_file_sha256"):
        refuse(f"claim-set hash drift: {digest[:16]} != "
               f"{str(manifest.get('claim_set_file_sha256'))[:16]}")

    commit = manifest.get("code_commit") or ""
    if not re.match(r"^[0-9a-f]{40}$", commit):
        refuse("freeze manifest has no valid code_commit")
    anc = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"],
                         cwd=os.path.dirname(_HERE))
    if anc.returncode != 0:
        refuse(f"frozen code commit {commit[:8]} is not an ancestor of HEAD")

    code_paths = ["api", "agents", "data_sources", "cache", "validation"]
    changed = _git("diff", "--name-only", commit, "HEAD", "--", *code_paths)
    dirty = _git("status", "--porcelain", "--", *code_paths)
    drift = [p for p in (changed.splitlines() if changed else [])
             if p.strip().endswith(".py")]
    drift += [line[3:] for line in (dirty.splitlines() if dirty else [])
              if line[3:].strip().endswith(".py")]
    if drift:
        refuse(f"code drift since freeze: {sorted(set(drift))}")

    cfg = manifest.get("harness_config", {})
    expected_cfg = {
        "label": REQUIRED_LABEL,
        "citation_cutoff": CUTOFF,
        "pass_min_defect_recall": PASS_MIN_DEFECT_RECALL,
        "pass_min_recall_cp_lower": PASS_MIN_RECALL_CP_LOWER,
        "pass_max_control_ffr": PASS_MAX_CONTROL_FFR,
        "pass_max_ffr_cp_upper": PASS_MAX_FFR_CP_UPPER,
        "max_group_abstention_frac": MAX_GROUP_ABSTENTION_FRAC,
    }
    if cfg != expected_cfg:
        refuse(f"harness config drift: manifest {cfg} != code {expected_cfg}")

    # Bind the scored artifact: once the manifest records scored results, the
    # results file must match the recorded hash. Replacing the published
    # outcome without a documented manifest amendment is drift and refuses.
    scored = manifest.get("scored_results")
    if scored:
        if not os.path.exists(RESULTS_JSON):
            refuse("manifest records scored results but the results artifact "
                   "is missing")
        rhash = hashlib.sha256(open(RESULTS_JSON, "rb").read()).hexdigest()
        if rhash != scored.get("results_sha256"):
            refuse(f"results artifact drift: {rhash[:16]} != "
                   f"{str(scored.get('results_sha256'))[:16]}")
    return manifest


# --------------------------------------------------------------------------- #
# Audit execution — claims pass ONLY input fields to the production path
# --------------------------------------------------------------------------- #

def run_one_claim(claim: dict) -> dict:
    """Invoke production run_audit with ONLY the claim's input fields.
    Anything in claim['truth'] is ground truth for scoring and must never
    reach the audit path."""
    from api.audit import run_audit
    inp = claim["input"]
    kwargs: dict[str, Any] = {
        "disease_name": inp["disease_name"],
        "drug_name": inp["drug_name"],
        "narrate": False,
    }
    if inp.get("job_id_hint"):
        kwargs["job_id_hint"] = inp["job_id_hint"]
    for src_key, arg in _INPUT_TO_KWARG:
        value = (inp.get("claim") or {}).get(src_key)
        if value:
            kwargs[arg] = value
    try:
        return run_audit(**kwargs)
    except Exception as exc:  # a source/environment failure, not a verdict
        return {"status": "__harness_exception__",
                "error": f"{type(exc).__name__}: {exc}"}


def run_all_claims(claims: list[dict]) -> dict:
    """Run every claim, checkpointing EACH raw output to disk immediately.
    The raw archive is complete BEFORE any metric is computed. Returns the
    archive ENVELOPE exactly as written to disk.

    Resume semantics (2026-08-11, after an environment restart destroyed a
    75-claim in-flight run): a leftover ``.partial`` archive is reused ONLY
    when it was written by the same code commit — mixing code versions in
    one archive would be two different measurements.  Completed claims are
    kept; claims whose recorded output is a harness exception (a crash, not
    a verdict) are re-run; missing claims are run.  The study is still one
    observation process under one frozen code commit.
    """
    raw: dict[str, Any] = {}
    tmp = RAW_OUTPUTS_JSON + ".partial"
    head = _git("rev-parse", "HEAD")
    if os.path.exists(tmp):
        try:
            with open(tmp) as fh:
                prior = json.load(fh)
            if prior.get("code_commit") == head:
                for cid, out in (prior.get("outputs") or {}).items():
                    if (isinstance(out, dict)
                            and out.get("status") != "__harness_exception__"):
                        raw[cid] = out
                print(f"[harness] resuming partial archive: kept "
                      f"{len(raw)} completed claim(s); crashed/missing "
                      f"claims will be (re-)run", flush=True)
            else:
                print("[harness] ignoring partial archive from a different "
                      "code commit (one archive = one code version)",
                      flush=True)
        except Exception as exc:  # corrupt partial — start clean
            print(f"[harness] ignoring unreadable partial archive ({exc})",
                  flush=True)

    total = len(claims)
    for claim in claims:
        cid = claim["claim_id"]
        if cid in raw:
            continue
        out = run_one_claim(claim)
        raw[cid] = out
        crashed = (isinstance(out, dict)
                   and out.get("status") == "__harness_exception__")
        # Atomic per-claim checkpoint: write sibling tmp, then rename.
        with open(tmp + ".tmp", "w") as fh:
            json.dump({"claim_ids": [c["claim_id"] for c in claims],
                       "code_commit": head, "outputs": raw}, fh)
        os.replace(tmp + ".tmp", tmp)
        print(f"[harness] audited {len(raw)}/{total} ({cid}"
              f"{' — HARNESS EXCEPTION, will re-run on resume' if crashed else ''})",
              flush=True)
    os.replace(tmp, RAW_OUTPUTS_JSON)
    with open(RAW_OUTPUTS_JSON) as fh:
        return json.load(fh)


def _code_drift_between(commit_a: str, commit_b: str) -> list:
    """.py drift over the audited code paths between two commits."""
    code_paths = ["api", "agents", "data_sources", "cache", "validation"]
    changed = _git("diff", "--name-only", commit_a, commit_b, "--",
                   *code_paths)
    return sorted({p for p in (changed.splitlines() if changed else [])
                   if p.strip().endswith(".py")})


def _archive_bound_to_freeze(existing: dict, manifest: dict) -> bool:
    """A raw archive is the frozen-code observation ONLY if the commit that
    wrote it is code-equivalent (zero .py drift over the audited paths) to
    the freeze manifest's code_commit.  A complete archive produced by
    different code is a different measurement and must never be scored as
    the frozen study (post-score code-review hardening, Amendment 5)."""
    arch_commit = str(existing.get("code_commit") or "")
    frozen = str(manifest.get("code_commit") or "")
    if not re.match(r"^[0-9a-f]{40}$", arch_commit):
        return False
    if arch_commit == frozen:
        return True
    return not _code_drift_between(frozen, arch_commit)


def _archive_complete(claim_set: dict, manifest: dict) -> bool:
    if not os.path.exists(RAW_OUTPUTS_JSON):
        return False
    existing = json.load(open(RAW_OUTPUTS_JSON))
    all_ids = {c["claim_id"] for c in claim_set["claims"]}
    return (set(existing.get("claim_ids") or []) == all_ids
            and set((existing.get("outputs") or {}).keys()) == all_ids
            and _archive_bound_to_freeze(existing, manifest))


def load_or_run_archive(claims: list[dict], manifest: dict) -> dict:
    """Crash recovery: if a COMPLETE raw archive exists but no results were
    ever written (a harness crash between archiving and scoring), score the
    archived outputs instead of re-running the audits. The archive is the
    frozen-code observation — and is admitted ONLY when the commit that
    wrote it is code-equivalent to the freeze manifest's code_commit;
    re-running would be a second measurement, and scoring a foreign-code
    archive would be a different measurement."""
    if os.path.exists(RAW_OUTPUTS_JSON):
        existing = json.load(open(RAW_OUTPUTS_JSON))
        all_ids = {c["claim_id"] for c in claims}
        if set(existing.get("claim_ids") or []) == all_ids and \
                set((existing.get("outputs") or {}).keys()) == all_ids:
            if not _archive_bound_to_freeze(existing, manifest):
                refuse("complete raw archive was written by code that "
                       "differs from the frozen commit over the audited .py "
                       "paths — one archive = one code version; refusing to "
                       "score a different measurement as the frozen study")
            print("[harness] complete raw archive found (code-bound to the "
                  "frozen commit) — scoring the archived frozen-code "
                  "outputs (audits NOT re-run)",
                  flush=True)
            return existing
        refuse("incomplete raw archive exists alongside no results; refusing "
               "to guess — inspect validation/audit_claimset_raw_outputs.json")
    return run_all_claims(claims)


# --------------------------------------------------------------------------- #
# Citation revalidation at score time (Amendment 1 §5)
# --------------------------------------------------------------------------- #

def revalidate_citation(citation: dict) -> str:
    """Returns 'valid' | 'invalid' (artifact no longer verifies against the
    cutoff -> construction defect, excluded from denominators) |
    'unverifiable' (source unreachable -> abstention)."""
    src = citation.get("source")
    ident = citation.get("identifier") or ""
    if src == "fda_label":
        data = _get_json("https://api.fda.gov/drug/label.json",
                         {"search": f'set_id:"{ident}"', "limit": 1})
        if data is None:
            return "unverifiable"
        rows = data.get("results") or []
        if not rows:
            return "invalid"
        eff = str(rows[0].get("effective_time") or "")
        if not re.match(r"^\d{8}$", eff):
            return "invalid"
        iso = f"{eff[:4]}-{eff[4:6]}-{eff[6:]}"
        return "valid" if iso < CUTOFF else "invalid"
    if src in ("chembl_molecule", "chembl_mechanism"):
        status = _get_json("https://www.ebi.ac.uk/chembl/api/data/status.json")
        if status is None:
            return "unverifiable"
        date = status.get("chembl_release_date") or ""
        return "valid" if date and date < CUTOFF else "invalid"
    if src == "europe_pmc":
        data = _get_json(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            {"query": f"EXT_ID:{ident}", "format": "json"})
        if data is None:
            return "unverifiable"
        hits = (data.get("resultList") or {}).get("result") or []
        if not hits:
            return "invalid"
        year = str(hits[0].get("firstPublicationDate") or
                   hits[0].get("pubYear") or "")
        return "valid" if year and year[:10] < CUTOFF else "invalid"
    return "invalid"


# --------------------------------------------------------------------------- #
# Mechanical scoring (construction protocol §6) — pure functions, no I/O
# --------------------------------------------------------------------------- #

def _findings(output: dict) -> list[dict]:
    return (output.get("audit_context") or {}).get("findings") or []


def _lane_status(output: dict, lane: str) -> str:
    src = (output.get("audit_context") or {}).get("sources") or {}
    return str((src.get(lane) or {}).get("status") or "unavailable")


def classify_claim(claim: dict, output: dict) -> dict:
    """Map (claim truth, archived raw output) to an outcome:
      defect claims:  'caught' | 'miss' | 'abstain'
      control claims: 'clean' | 'false_flag' | 'abstain'
    Findings with status 'review' or 'unresolved' NEVER count as flags.
    Source failure states on the claim's relevant lane -> abstention."""
    if output.get("status") == "__harness_exception__":
        return {"outcome": "abstain",
                "reason": f"harness exception: {output.get('error')}"}

    expected = claim["truth"]["expected"]

    if claim["group"] == "control":
        if _lane_status(output, "regulatory_label") in LANE_FAILURE_STATES:
            return {"outcome": "abstain", "reason": "regulatory lane failure"}
        if _lane_status(output, "entity_linked_literature") in LANE_FAILURE_STATES:
            return {"outcome": "abstain", "reason": "literature lane failure"}
        flagged = [f for f in _findings(output) if f.get("status") == "flagged"]
        if flagged:
            return {"outcome": "false_flag",
                    "reason": "flagged findings: "
                              + ",".join(f.get("code", "?") for f in flagged)}
        return {"outcome": "clean", "reason": ""}

    if "finding" in expected:  # N-class
        code = expected["finding"]["code"]
        lane = FINDING_LANE[code]
        if _lane_status(output, lane) in LANE_FAILURE_STATES:
            return {"outcome": "abstain", "reason": f"{lane} lane failure"}
        want = expected["finding"]["status"]
        hit = any(f.get("code") == code and f.get("status") == want
                  for f in _findings(output))
        return {"outcome": "caught" if hit else "miss",
                "reason": "" if hit else
                          f"no {code} finding with status {want}"}

    # E-class: status/cap/disclosure rules against the audit verdict
    checks: list[str] = []
    if "status" in expected:
        checks.append("status" if output.get("status") == expected["status"]
                      else f"status={output.get('status')!r}")
    if "cap_applied" in expected:
        checks.append("cap_applied"
                      if bool(output.get("cap_applied")) == expected["cap_applied"]
                      else "cap_applied mismatch")
    if "cap_reason_contains" in expected:
        needle = expected["cap_reason_contains"].lower()
        checks.append("cap_reason"
                      if needle in (output.get("cap_reason") or "").lower()
                      else f"cap_reason={output.get('cap_reason')!r}")
    candidate = output.get("candidate") or {}
    if "black_box_advisory" in expected:
        checks.append("black_box_advisory"
                      if bool(candidate.get("black_box_advisory")) ==
                         expected["black_box_advisory"]
                      else "black_box_advisory mismatch")
    if "safety_cap_applied" in expected:
        checks.append("safety_cap_applied"
                      if bool(candidate.get("safety_cap_applied")) ==
                         expected["safety_cap_applied"]
                      else "safety_cap_applied mismatch")
    failed = [c for c in checks if "=" in c or "mismatch" in c]
    return {"outcome": "caught" if not failed else "miss",
            "reason": "; ".join(failed)}


def annotate_disclosure(claim: dict, output: dict, outcome: str) -> dict:
    """Non-scored disclosure-accuracy annotation (protocol §7): does the
    disclosure of a CAUGHT defect contradict the external artifact? The known
    failure mode is a stale pool badge claiming WITHDRAWN on a marketed drug
    (or vice versa). Never changes scored metrics."""
    cls = claim["defect_class"]
    if outcome != "caught":
        return {"annotation": "not_applicable"}
    if cls == "E1_safety_withdrawal":
        truth_withdrawn = True
    elif cls == "E2_boxed_warning_not_withdrawal":
        truth_withdrawn = False
    else:
        return {"annotation": "not_applicable"}
    candidate = output.get("candidate") or {}
    blob = json.dumps(candidate.get("status_badge") or "") + " " + \
        json.dumps(output.get("disclosure") or "")
    disclosed_withdrawn = bool(re.search(r"withdrawn", blob, re.IGNORECASE))
    consistent = disclosed_withdrawn == truth_withdrawn
    return {"annotation": "consistent" if consistent else "contradicted",
            "status_badge": candidate.get("status_badge"),
            "truth_withdrawn": truth_withdrawn}


# --------------------------------------------------------------------------- #
# Metrics — exact Clopper-Pearson one-sided 95% bounds
# --------------------------------------------------------------------------- #

def cp_lower_95(k: int, n: int) -> float:
    """One-sided exact lower 95% bound for k successes in n trials."""
    if n <= 0:
        return 0.0
    if k <= 0:
        return 0.0
    from scipy.stats import beta
    return float(beta.ppf(0.05, k, n - k + 1))


def cp_upper_95(x: int, n: int) -> float:
    """One-sided exact upper 95% bound for x events in n trials."""
    if n <= 0:
        return 1.0
    if x >= n:
        return 1.0
    from scipy.stats import beta
    return float(beta.ppf(0.95, x + 1, n - x))


def compute_metrics(claims: list[dict], outcomes: dict[str, dict]) -> dict:
    """outcomes: claim_id -> classify_claim result plus citation revalidation
    state in result['citation'] ('valid' | 'invalid' | 'unverifiable').
    Excluded (citation-invalid) claims are removed from all denominators.
    Both the eligible-denominator metrics (PASS rule) and the conservative
    fixed-denominator view (abstentions counted as not-caught) are reported.
    """
    per_class: dict[str, dict] = {}
    groups: dict[str, dict] = {}

    def bucket(container: dict, key: str) -> dict:
        return container.setdefault(key, {
            "caught": 0, "miss": 0, "false_flag": 0, "clean": 0,
            "abstain": 0, "excluded": 0, "total": 0})

    for claim in claims:
        cid = claim["claim_id"]
        res = outcomes[cid]
        g = bucket(groups, claim["group"])
        c = bucket(per_class, claim["defect_class"])
        g["total"] += 1
        c["total"] += 1
        if res.get("citation") == "invalid":
            g["excluded"] += 1
            c["excluded"] += 1
            continue
        if res.get("citation") == "unverifiable" or \
                res["outcome"] == "abstain":
            g["abstain"] += 1
            c["abstain"] += 1
            continue
        g[res["outcome"]] += 1
        c[res["outcome"]] += 1

    def recall_view(b: dict, total: int, defect: bool) -> dict:
        num = b["caught"] if defect else b["false_flag"]
        fixed_denom = total - b["excluded"]
        eligible = fixed_denom - b["abstain"]
        rate = (num / eligible) if eligible else 0.0
        fixed_rate = (num / fixed_denom) if fixed_denom else 0.0
        if defect:
            return {"caught": num, "eligible": eligible,
                    "fixed_denominator": fixed_denom,
                    "recall": rate, "recall_fixed_denominator": fixed_rate,
                    "cp_lower_95": cp_lower_95(num, eligible) if eligible else 0.0}
        return {"flagged": num, "eligible": eligible,
                "fixed_denominator": fixed_denom,
                "false_flag_rate": rate,
                "false_flag_rate_fixed_denominator": fixed_rate,
                "cp_upper_95": cp_upper_95(num, eligible) if eligible else 1.0}

    defect_caught = sum(groups[g]["caught"] for g in DEFECT_GROUPS if g in groups)
    defect_abstain = sum(groups[g]["abstain"] for g in DEFECT_GROUPS if g in groups)
    defect_excluded = sum(groups[g]["excluded"] for g in DEFECT_GROUPS if g in groups)
    defect_total = sum(GROUP_TOTALS[g] for g in DEFECT_GROUPS)
    defect_fixed_denom = defect_total - defect_excluded
    defect_eligible = defect_fixed_denom - defect_abstain

    ctrl = groups.get("control", bucket(groups, "control"))
    ctrl_fixed_denom = GROUP_TOTALS["control"] - ctrl["excluded"]
    ctrl_eligible = ctrl_fixed_denom - ctrl["abstain"]

    defect_recall = defect_caught / defect_eligible if defect_eligible else 0.0
    control_ffr = ctrl["false_flag"] / ctrl_eligible if ctrl_eligible else 1.0

    abstention_breach = any(
        groups[g]["abstain"] / GROUP_TOTALS[g] > MAX_GROUP_ABSTENTION_FRAC
        for g in GROUP_TOTALS if g in groups)

    novel = groups.get("novel", bucket(groups, "novel"))
    novel_fixed = GROUP_TOTALS["novel"] - novel["excluded"]
    novel_eligible = novel_fixed - novel["abstain"]

    metrics = {
        "defect_recall": defect_recall,
        "defect_recall_cp_lower_95":
            cp_lower_95(defect_caught, defect_eligible) if defect_eligible else 0.0,
        "defect_recall_fixed_denominator":
            defect_caught / defect_fixed_denom if defect_fixed_denom else 0.0,
        "defect_counts": {"caught": defect_caught, "eligible": defect_eligible,
                          "abstained": defect_abstain,
                          "excluded": defect_excluded,
                          "registered_total": defect_total},
        "novel_recall":
            novel["caught"] / novel_eligible if novel_eligible else 0.0,
        "novel_recall_cp_lower_95":
            cp_lower_95(novel["caught"], novel_eligible) if novel_eligible else 0.0,
        "novel_counts": {"caught": novel["caught"], "eligible": novel_eligible,
                         "abstained": novel["abstain"],
                         "excluded": novel["excluded"],
                         "registered_total": GROUP_TOTALS["novel"]},
        "control_false_flag_rate": control_ffr,
        "control_false_flag_cp_upper_95":
            cp_upper_95(ctrl["false_flag"], ctrl_eligible) if ctrl_eligible else 1.0,
        "control_false_flag_fixed_denominator":
            ctrl["false_flag"] / ctrl_fixed_denom if ctrl_fixed_denom else 0.0,
        "control_counts": {"flagged": ctrl["false_flag"], "clean": ctrl["clean"],
                           "eligible": ctrl_eligible,
                           "abstained": ctrl["abstain"],
                           "excluded": ctrl["excluded"],
                           "registered_total": GROUP_TOTALS["control"]},
        "abstention_fraction_by_group": {
            g: groups[g]["abstain"] / GROUP_TOTALS[g] for g in GROUP_TOTALS
            if g in groups},
        "abstention_breach": abstention_breach,
        "per_group": groups,
        "per_class": per_class,
    }
    if abstention_breach:
        metrics["verdict"] = "INVALID-DATA"
    else:
        passed = (defect_recall >= PASS_MIN_DEFECT_RECALL
                  and metrics["defect_recall_cp_lower_95"] >= PASS_MIN_RECALL_CP_LOWER
                  and control_ffr <= PASS_MAX_CONTROL_FFR
                  and metrics["control_false_flag_cp_upper_95"] <= PASS_MAX_FFR_CP_UPPER)
        metrics["verdict"] = "PASS" if passed else "FAIL"
    return metrics


# --------------------------------------------------------------------------- #
# Score-from-archive (the ONLY metric path; also used by --recalc-only)
# --------------------------------------------------------------------------- #

def score_from_archive(claim_set: dict, raw: dict,
                       revalidate: bool = True) -> dict:
    claims = claim_set["claims"]
    outcomes: dict[str, dict] = {}
    annotations: dict[str, dict] = {}
    citation_cache: dict[str, str] = {}
    for claim in claims:
        cid = claim["claim_id"]
        output = raw["outputs"].get(cid)
        if output is None:
            outcomes[cid] = {"outcome": "abstain",
                             "reason": "missing raw output",
                             "citation": "valid"}
            continue
        cit = claim["truth"]["citation"]
        if revalidate:
            ckey = json.dumps(cit, sort_keys=True)
            if ckey not in citation_cache:
                citation_cache[ckey] = revalidate_citation(cit)
            cit_state = citation_cache[ckey]
        else:
            cit_state = "valid"
        result = classify_claim(claim, output)
        result["citation"] = cit_state
        outcomes[cid] = result
        annotations[cid] = annotate_disclosure(claim, output,
                                               result["outcome"])
    metrics = compute_metrics(claims, outcomes)
    return {"outcomes": outcomes, "annotations": annotations,
            "metrics": metrics}


# --------------------------------------------------------------------------- #
# Results artifact
# --------------------------------------------------------------------------- #

def write_results(metrics: dict, outcomes: dict, annotations: dict,
                  manifest: dict, health: dict, rerun_reason: str) -> None:
    results = {
        "label": REQUIRED_LABEL,
        "scored_at": datetime.now(timezone.utc).isoformat(),
        "claim_set_sha256": manifest["claim_set_file_sha256"],
        "freeze_code_commit": manifest["code_commit"],
        "cache_policy": manifest.get("cache_policy"),
        "health_probes": health,
        "rerun_after_harness_defect": rerun_reason or None,
        "metrics": {k: v for k, v in metrics.items()
                    if k not in ("per_group", "per_class")},
        "per_class": metrics["per_class"],
        "per_group": metrics["per_group"],
        "outcomes": outcomes,
        "disclosure_annotations": annotations,
        "limitations": RESULTS_LIMITATIONS,
    }
    with open(RESULTS_JSON, "w") as fh:
        json.dump(results, fh, indent=2)
    _write_results_md(results)


def _write_results_md(results: dict) -> None:
    m = results["metrics"]
    lines = [
        f"# Frozen audit claim-set {REQUIRED_LABEL.split('_')[-1]} — "
        "scored results",
        "",
        f"- Label: `{results['label']}` · scored {results['scored_at']}",
        f"- Claim set sha256: `{results['claim_set_sha256'][:16]}…`",
        f"- Freeze code commit: `{results['freeze_code_commit'][:8]}`",
        f"- **Verdict: {m['verdict']}**",
        "",
        "## Headline metrics (pre-registered thresholds)",
        "",
        "| Metric | Value | 95% bound | Threshold | Met |",
        "|--------|-------|-----------|-----------|-----|",
        f"| defect_recall | {m['defect_recall']:.3f} "
        f"({m['defect_counts']['caught']}/{m['defect_counts']['eligible']}) "
        f"| CP lower {m['defect_recall_cp_lower_95']:.3f} "
        f"| ≥ {PASS_MIN_DEFECT_RECALL}, lower ≥ {PASS_MIN_RECALL_CP_LOWER} "
        f"| {'yes' if m['defect_recall'] >= PASS_MIN_DEFECT_RECALL and m['defect_recall_cp_lower_95'] >= PASS_MIN_RECALL_CP_LOWER else 'NO'} |",
        f"| control_false_flag | {m['control_false_flag_rate']:.3f} "
        f"({m['control_counts']['flagged']}/{m['control_counts']['eligible']}) "
        f"| CP upper {m['control_false_flag_cp_upper_95']:.3f} "
        f"| ≤ {PASS_MAX_CONTROL_FFR}, upper ≤ {PASS_MAX_FFR_CP_UPPER} "
        f"| {'yes' if m['control_false_flag_rate'] <= PASS_MAX_CONTROL_FFR and m['control_false_flag_cp_upper_95'] <= PASS_MAX_FFR_CP_UPPER else 'NO'} |",
        f"| novel_recall (no threshold) | {m['novel_recall']:.3f} "
        f"({m['novel_counts']['caught']}/{m['novel_counts']['eligible']}) "
        f"| CP lower {m['novel_recall_cp_lower_95']:.3f} | — | — |",
        "",
        f"Fixed-denominator views (abstentions as not-caught): defect "
        f"{m['defect_recall_fixed_denominator']:.3f}, control false-flag "
        f"{m['control_false_flag_fixed_denominator']:.3f}.",
        "",
        "## Per-class breakdown",
        "",
        "| Class | caught/flagged | miss/clean | abstain | excluded | total |",
        "|-------|-----|------|---------|----------|-------|",
    ]
    for cls, b in sorted(results["per_class"].items()):
        pos = b["caught"] if cls != "none" else b["false_flag"]
        neg = b["miss"] if cls != "none" else b["clean"]
        lines.append(f"| {cls} | {pos} | {neg} | {b['abstain']} "
                     f"| {b['excluded']} | {b['total']} |")
    lines += [
        "",
        "## Abstentions and exclusions",
        "",
    ]
    for cid, res in sorted(results["outcomes"].items()):
        if res["outcome"] == "abstain" or res.get("citation") != "valid":
            lines.append(f"- {cid}: {res['outcome']}"
                         f"{' (citation ' + res['citation'] + ')' if res.get('citation') != 'valid' else ''}"
                         f" — {res.get('reason', '')}")
    contra = [cid for cid, a in results["disclosure_annotations"].items()
              if a.get("annotation") == "contradicted"]
    lines += [
        "",
        "## Disclosure-accuracy annotation (non-scored)",
        "",
        f"Caught defects whose disclosure contradicts the external artifact: "
        f"{len(contra)}"
        + ((" — " + ", ".join(contra)) if contra else ""),
        "",
        "## Limitations",
        "",
    ]
    lines += [f"- {lim}" for lim in results["limitations"]]
    lines.append("")
    with open(RESULTS_MD, "w") as fh:
        fh.write("\n".join(lines))


# --------------------------------------------------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--recalc-only", action="store_true")
    parser.add_argument("--allow-rerun-after-harness-defect", default="")
    args = parser.parse_args()

    if args.label in FORBIDDEN_LABELS or args.label != REQUIRED_LABEL:
        refuse(f"label must be exactly '{REQUIRED_LABEL}'")

    manifest = verify_freeze()
    claim_set = json.load(open(CLAIM_SET_JSON))

    # Idempotency: a completed scored artifact refuses re-run. The
    # --recalc-only verification path is exempt: it is read-only over the
    # archive and results, and exists precisely to check a completed run.
    # The one-fix-one-rerun allowance is single-use (consumption marker) and
    # archive-only: it may re-score the frozen archive, NEVER re-measure.
    rerun_reason = ""
    if os.path.exists(RESULTS_JSON) and not args.recalc_only:
        if not args.allow_rerun_after_harness_defect:
            refuse("a completed scored artifact exists "
                   "(validation/audit_claimset_results.json). The only "
                   "exception is the pre-registered one-fix-one-rerun "
                   "allowance: --allow-rerun-after-harness-defect '<reason>'")
        # Consumption is recorded in BOTH the marker file and the freeze
        # manifest; either record alone seals the allowance (the marker
        # could be absent on a fresh clone of a completed study).
        manifest_consumed = bool(
            (manifest.get("rerun_allowance") or {}).get("consumed"))
        if os.path.exists(RERUN_CONSUMED_JSON) or manifest_consumed:
            refuse("the one-fix-one-rerun allowance has already been "
                   "consumed (consumption marker and/or freeze manifest "
                   "record); no further rerun is permitted")
        if not _archive_complete(claim_set, manifest):
            refuse("the rerun allowance is archive-only: it re-scores the "
                   "frozen raw archive and never re-executes audits; no "
                   "complete freeze-bound archive is present")
        rerun_reason = args.allow_rerun_after_harness_defect
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        os.replace(RESULTS_JSON,
                   RESULTS_JSON + f".superseded-{ts}")
        with open(RERUN_CONSUMED_JSON, "w") as fh:
            json.dump({"reason": rerun_reason, "consumed_at": ts,
                       "superseded_results":
                           os.path.basename(RESULTS_JSON) +
                           f".superseded-{ts}"}, fh, indent=2)
        print(f"[harness] one-fix-one-rerun allowance CONSUMED: "
              f"{rerun_reason}")

    if args.recalc_only:
        if not os.path.exists(RAW_OUTPUTS_JSON) or \
                not os.path.exists(RESULTS_JSON):
            refuse("--recalc-only needs both the raw archive and results")
        raw = json.load(open(RAW_OUTPUTS_JSON))
        health_gate()
        fresh = score_from_archive(claim_set, raw)
        stored = json.load(open(RESULTS_JSON))
        stored_metrics = stored["metrics"]
        keys = ("defect_recall", "defect_recall_cp_lower_95",
                "novel_recall", "control_false_flag_rate",
                "control_false_flag_cp_upper_95", "verdict")
        diffs = {k: (stored_metrics.get(k), fresh["metrics"].get(k))
                 for k in keys
                 if stored_metrics.get(k) != fresh["metrics"].get(k)}
        if diffs:
            print(f"[harness] RECALC MISMATCH: {diffs}")
            raise SystemExit(3)
        print("[harness] RECALC MATCH: independent recomputation from the "
              "raw archive reproduces the stored metrics exactly.")
        return

    # Pre-run gate: never burn a scored run during a known outage.
    health_gate()
    check_pool_jobs(claim_set)

    raw = load_or_run_archive(claim_set["claims"], manifest)
    # Metrics are computed from the on-disk archive, never from in-memory
    # run outputs (protocol §8: archived BEFORE metric computation).
    raw = json.load(open(RAW_OUTPUTS_JSON))

    # Health gate immediately before scoring (construction protocol §8).
    health = health_gate()

    scored = score_from_archive(claim_set, raw)
    write_results(scored["metrics"], scored["outcomes"],
                  scored["annotations"], manifest, health, rerun_reason)
    m = scored["metrics"]
    print(f"[harness] DONE: verdict {m['verdict']} · "
          f"defect_recall {m['defect_recall']:.3f} "
          f"(CP lower {m['defect_recall_cp_lower_95']:.3f}) · "
          f"control false-flag {m['control_false_flag_rate']:.3f} "
          f"(CP upper {m['control_false_flag_cp_upper_95']:.3f}) · "
          f"novel_recall {m['novel_recall']:.3f}")


if __name__ == "__main__":
    main()
