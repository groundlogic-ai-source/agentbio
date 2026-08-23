#!/usr/bin/env python3
"""Benchmark v2 provenance verifier — regenerates the raw evidence behind the
dossier's provenance caveat. READ-ONLY: writes nothing, calls no network.

Checks:
  1. results artifact SHA-256 matches the repository pin
  2. results metadata names the committed case list (and the missing screened list)
  3. case-list blob identical at HEAD and tag benchmark-cases-v2
  4. case-list tag and screen-parameter commits predate the 2026-08-09 run
  5. all 32 primary rows identity-match the committed 50-case selection
  6. funnel arithmetic reproduces from per-row data

Exit 0 = all pass, 1 = any failure. Anyone can rerun this and inspect the raw
evidence themselves rather than trusting a narrative summary.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections import Counter

_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_DIR)
RESULTS = os.path.join(_DIR, "benchmark_results_v2.json")
CASE_LIST = os.path.join(_DIR, "benchmark_case_list.json")
EXPECTED_SHA256 = "b318f61c892b76df63c1e4a673d7e9be082b16b609e19722991b673e1cfc2c1d"
RUN_DATE = "2026-08-09"

_failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}\n      {detail}")
    if not ok:
        _failures.append(name)


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True, cwd=_REPO)
    return out.stdout.strip()


def _norm(s: object) -> str:
    return " ".join(str(s or "").strip().lower().split())


def _truthy(v: object) -> bool:
    return str(v).strip().lower() == "true"


def main() -> int:
    raw = open(RESULTS, "rb").read()
    sha = hashlib.sha256(raw).hexdigest()
    check("1. results SHA-256 == repository pin", sha == EXPECTED_SHA256, sha)

    res = json.loads(raw)
    meta = {k: v for k, v in res.items() if k != "cases"}
    print(f"      metadata verbatim: {meta}")
    check("2a. metadata case_list == committed file",
          res.get("case_list") == "validation/benchmark_case_list.json",
          repr(res.get("case_list")))
    check("2b. metadata screened_list == missing _v2 file (disclosed)",
          res.get("screened_list") == "validation/benchmark_case_list_v2.json",
          repr(res.get("screened_list")))

    head_blob = git("rev-parse", "HEAD:validation/benchmark_case_list.json")
    tag_blob = git("rev-parse", "benchmark-cases-v2:validation/benchmark_case_list.json")
    check("3. case-list blob identical at HEAD and tag benchmark-cases-v2",
          bool(head_blob) and head_blob == tag_blob, f"HEAD={head_blob} tag={tag_blob}")

    tag_date = git("log", "-1", "--format=%cI", "benchmark-cases-v2")
    check("4a. case-list tag commit predates run", tag_date < RUN_DATE, tag_date)
    # Resolve by commit message, not hash: the public mirror's history was
    # sanitized (internal notes removed), which rewrites commit hashes. The
    # pre-sanitization hash 0b91101 remains valid in the private archive.
    param_commit = git("log", "-1", "--format=%cI %h",
                       "--grep=v2 preflight: Amendment-1 screen")
    check("4b. screen parameters committed pre-run (Amendment 1/3 commit)",
          bool(param_commit) and param_commit[:10] < RUN_DATE,
          param_commit or "commit not found")
    results_commit = git("log", "-1", "--follow", "--format=%cI %h %s", "--",
                         "validation/benchmark_results_v2.json")
    print(f"      results artifact committed: {results_commit}")

    cl = json.load(open(CASE_LIST))
    prim = {(_norm(c["drug_name"]), _norm(c["ind_name"])) for c in cl["primary"]}
    check("5a. committed selection holds 50 primary cases", len(cl["primary"]) == 50,
          f"n={len(cl['primary'])}, seed={cl.get('seed')}")

    rows = res["cases"]
    prim_rows = [r for r in rows if r.get("subset") == "primary"]
    dev_rows = [r for r in rows if r.get("subset") == "development"]
    matched = sum(1 for r in prim_rows
                  if (_norm(r.get("drug_name")), _norm(r.get("disease_name"))) in prim)
    check("5b. every primary result row identity-matches committed selection",
          matched == len(prim_rows) == 32, f"{matched}/{len(prim_rows)} matched")

    hits = [r for r in prim_rows if _truthy(r.get("found"))]
    in_scope = [r for r in prim_rows if _truthy(r.get("in_universe"))]
    oos = [r for r in prim_rows if not _truthy(r.get("in_universe"))]
    mc = Counter(r.get("miss_class") for r in in_scope if not _truthy(r.get("found")))
    funnel_ok = (len(prim_rows) == 32 and len(in_scope) == 22 and len(oos) == 10
                 and len(hits) == 6 and len(dev_rows) == 15
                 and mc.get("wrong_target") == 13
                 and mc.get("unresolved_no_mechanism") == 3)
    check("6. funnel reproduces from rows (32/22/10/6 + 15 dev; 13+3 misses)",
          funnel_ok,
          f"screened={len(prim_rows)} in_scope={len(in_scope)} oos={len(oos)} "
          f"hits={len(hits)} dev={len(dev_rows)} misses={dict(mc)}")

    print()
    if _failures:
        print(f"RESULT: {len(_failures)} check(s) FAILED: {_failures}")
        return 1
    print("RESULT: all provenance checks pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
