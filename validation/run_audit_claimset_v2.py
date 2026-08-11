"""Sealed harness for the frozen audit claim set v2 (audit_claimset_v2).

This is a thin wrapper over the v1 sealed harness
(validation/run_audit_claimset.py): it overrides the module-level artifact
paths, label, group totals, and results limitations, then runs the SAME
execution, freeze-verification, idempotency, scoring, and threshold code.
The v1 study's seal is untouched — its label guard, paths, and recorded
results are unaffected, and v1's one-fix-one-rerun allowance remains
consumed forever.

v2 discipline (validation/audit_claimset_v2_preregistration.md):
  * exactly one scored run under --label audit_claimset_v2;
  * a fresh, single-use one-fix-one-rerun allowance (archive-only: it may
    re-score the frozen raw archive, NEVER re-execute audits);
  * freeze manifest validation/audit_claimset_v2_freeze_manifest.json binds
    the claim-set SHA, code commit, harness config, cache policy, and
    health requirements; any .py drift under api/agents/data_sources/
    cache/validation between freeze and run refuses the run;
  * this artifact must never be reported as benchmark v2, engineering
    acceptance, or discovery accuracy — and v1's FAIL result is reported
    alongside v2, never replaced by it.

Usage:
    python3 -m validation.run_audit_claimset_v2 --label audit_claimset_v2
    python3 -m validation.run_audit_claimset_v2 --label audit_claimset_v2 \
        --recalc-only
    python3 -m validation.run_audit_claimset_v2 --label audit_claimset_v2 \
        --allow-rerun-after-harness-defect "<reason>"

Artifacts:
    validation/audit_claimset_v2_raw_outputs.json
    validation/audit_claimset_v2_results.json
    validation/audit_claimset_v2_results.md
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from validation import run_audit_claimset as harness

_HERE = os.path.dirname(os.path.abspath(__file__))


def configure() -> None:
    harness.REQUIRED_LABEL = "audit_claimset_v2"
    harness.CLAIM_SET_JSON = os.path.join(_HERE, "audit_claim_set_v2.json")
    harness.FREEZE_MANIFEST_JSON = os.path.join(
        _HERE, "audit_claimset_v2_freeze_manifest.json")
    harness.RAW_OUTPUTS_JSON = os.path.join(
        _HERE, "audit_claimset_v2_raw_outputs.json")
    harness.RESULTS_JSON = os.path.join(
        _HERE, "audit_claimset_v2_results.json")
    harness.RESULTS_MD = os.path.join(
        _HERE, "audit_claimset_v2_results.md")
    harness.RERUN_CONSUMED_JSON = os.path.join(
        _HERE, "audit_claimset_v2_rerun_allowance_consumed.json")

    # v2 composition is construction-determined (pre-registration §2); the
    # fixed-denominator views read the totals off the frozen claim set.
    claim_set = json.load(open(harness.CLAIM_SET_JSON))
    totals = {"existing_fix": 0, "novel": 0, "control": 0}
    for claim in claim_set["claims"]:
        totals[claim["group"]] += 1
    harness.GROUP_TOTALS = totals

    harness.RESULTS_LIMITATIONS = [
        "E4 (unresolvable-name honesty) may have zero or few claims: v1's "
        "construction assumption was falsified (ChEMBL synonyms resolve "
        "major brand names), so v2 accepts only brands verified "
        "NON-resolving in raw ChEMBL at construction. A near-empty E4 "
        "class is reported as untested, not padded.",
        "The N1 label-parse precision defect observed in v1's control arm "
        "(2 of 7 false flags) was deliberately NOT fixed before v2; a "
        "residual control false-flag contribution of up to 2/40 is "
        "expected and does not by itself fail the <=0.15 bar.",
        "Composition is construction-determined: existing_fix is the "
        "honest pool-bounded yield of the three refreshed safety-v2 pools; "
        "novel fills the remainder of the 60-claim defect total per the "
        "registered N1 -> N4 -> N2 reallocation order.",
        "The citation cutoff (2026-08-10) is a mechanical artifact-date "
        "rule, not a judgment of evidence currency.",
        "Pool-context coverage is limited to the two persisted cases "
        "referenced by job_id_hint in this claim set (refreshed to "
        "safety-v2); novel-lane claims are pool-free by design.",
        "N3 (species/preclinical-only) yielded ZERO claims under v2's "
        "tightened gates (v1 gates PLUS no cutoff-eligible FDA label and "
        "no human-trial signal): all five candidates failed raw ground "
        "truth at construction. The N3 defect class is untested in v2 — "
        "reported as such, not padded. The novel group's 59 claims are "
        "N1=8, N2=43 (reallocation), N4=8.",
        "Freeze #1 was destroyed by an environment restart before any "
        "scoring (Amendment 3); this claim set is the registered rebuild "
        "under identical rules. Engineering fixes (EvidenceRecord "
        "coercion, LLM provider round-robin + 429 backoff, per-claim "
        "checkpoint/resume) were applied BEFORE this freeze and are part "
        "of the frozen system under test. Both allowances remain "
        "unconsumed.",
    ]


if __name__ == "__main__":
    configure()
    harness.main()
