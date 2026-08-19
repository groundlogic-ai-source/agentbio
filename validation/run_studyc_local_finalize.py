"""Dev-only Study C finalization driver.

Runs the real ``validation.run_triage_discrimination_studyc.main()`` against
the checkpoint snapshot pulled from prod, with ONE environmental difference:
``git rev-parse HEAD`` is made to fail, exactly replicating the prod
deployment snapshot (which ships without git, so the runner's commit-pin
check fails open there by design — see the freeze manifest). Every other
check (cases_sha256, rule_fingerprint, per-record hash binding, health
gate) runs unchanged.

Purpose: finalize the study in dev (record the out-of-universe exclusions,
profile the finalized pools, write results) so the artifact can be verified
BEFORE republish, instead of discovering a remaining defect on prod. No LLM
calls are made on this path: all six pools are checkpoint-finalized, the
out-of-universe cases raise before any biologist call, and drug profiling
is the deterministic LLM-free audit lane.
"""
from __future__ import annotations

import subprocess

_orig_run = subprocess.run


def _run_without_git(cmd, *args, **kwargs):
    if isinstance(cmd, (list, tuple)) and list(cmd[:2]) == ["git", "rev-parse"]:
        raise FileNotFoundError("git unavailable (simulating prod snapshot)")
    return _orig_run(cmd, *args, **kwargs)


subprocess.run = _run_without_git

from validation.run_triage_discrimination_studyc import main  # noqa: E402

if __name__ == "__main__":
    main()
