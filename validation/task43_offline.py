"""Offline ranking-context diagnostic for the engineering acceptance run.

This module is deliberately downstream of the pipeline.  It reads the
report-only ``rank_context`` snapshots emitted by
``run_v2_engineering_acceptance`` and never calls a provider, Reviewer, or
other network-backed code.  Missing context is reported as missing evidence,
not silently reconstructed from an unrelated output artifact.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Any, Iterable, Optional


LABEL = "engineering_acceptance"
DEFAULT_MAX_NEIGHBORS = 20
VALID_ROLES = {"efficacy", "target_link", "disease_link"}
DIRECTIONAL_ACTIONS = {
    "agonist", "antagonist", "inhibitor", "activator", "modulator",
    "blocker", "opener", "partial agonist", "inverse agonist",
    "positive allosteric modulator", "negative allosteric modulator",
}


def load_acceptance_results(path: str) -> dict[str, Any]:
    """Load and validate an engineering-acceptance JSON artifact only."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("acceptance artifact must be a JSON object")
    if data.get("label") != LABEL:
        raise ValueError(
            f"refusing artifact with label {data.get('label')!r}; "
            f"expected {LABEL!r}"
        )
    if not isinstance(data.get("cases"), list):
        raise ValueError("acceptance artifact is missing a cases list")
    return data


def _records(row: dict[str, Any]) -> list[dict[str, Any]]:
    ledger = row.get("evidence_ledger") or row.get("_evidence_ledger") or {}
    records = ledger.get("records") or []
    return [record for record in records if isinstance(record, dict)]


def summarize_evidence(row: dict[str, Any]) -> dict[str, Any]:
    """Classify only the evidence visible in a persisted rank-context row.

    This is intentionally descriptive.  It does not call the acceptance
    validity classifier and does not claim that a candidate is a good
    disease hypothesis merely because it has a high score or approval history.
    """
    records = _records(row)
    qualified = [
        record for record in records
        if str(record.get("qualification_status", "")).lower() == "qualified"
    ]
    role_bearing = [
        record for record in qualified
        if str(record.get("evidence_role", "")).lower() in VALID_ROLES
    ]
    contradicted = [
        record for record in role_bearing
        if str(record.get("contradiction_status", "")).lower() == "contradicted"
    ]
    directional = [
        record for record in role_bearing
        if str(record.get("action", "")).strip().lower() in DIRECTIONAL_ACTIONS
        or str(record.get("direction", "")).strip().lower()
        not in ("", "unknown")
    ]
    unqualified = [
        record for record in records
        if str(record.get("qualification_status", "")).lower()
        in {"unqualified", "unknown", ""}
    ]

    if not records:
        evidence_class = "none_or_identity_only"
        evidence_tier = "none"
    elif not role_bearing:
        evidence_class = (
            "unqualified_or_unresolved" if unqualified
            else "name_or_structure_only"
        )
        evidence_tier = "weak_or_none"
    elif contradicted:
        evidence_class = "qualified_contradicted"
        evidence_tier = "qualified_but_contradicted"
    elif directional:
        evidence_class = "qualified_directional"
        evidence_tier = "qualified_mechanistic"
    else:
        evidence_class = "qualified"
        evidence_tier = "qualified_non_directional"

    ledger = row.get("evidence_ledger") or row.get("_evidence_ledger") or {}
    providers = sorted(set(ledger.get("providers") or []))
    return {
        "evidence_class": evidence_class,
        "evidence_tier": evidence_tier,
        "record_count": len(records),
        "qualified_record_count": len(qualified),
        "role_bearing_qualified_count": len(role_bearing),
        "contradicted_record_count": len(contradicted),
        "directional_record_count": len(directional),
        "providers": providers,
        "target_symbols": sorted(set(ledger.get("target_symbols") or [])),
        "efficacy_confidence": (
            row.get("efficacy_confidence")
            if row.get("efficacy_confidence") is not None
            else ledger.get("efficacy_confidence")
        ),
    }


def _known_rank(case: dict[str, Any]) -> Optional[int]:
    rank = case.get("rank")
    return rank if isinstance(rank, int) and rank > 0 else None


def _ranked_rows(case: dict[str, Any]) -> list[dict[str, Any]]:
    rows = case.get("rank_context")
    if not isinstance(rows, list):
        return []
    return [
        row for row in rows
        if isinstance(row, dict) and isinstance(row.get("rank"), int)
    ]


def analyze_above_known_drug(
    case: dict[str, Any],
    max_neighbors: int = DEFAULT_MAX_NEIGHBORS,
) -> dict[str, Any]:
    """Analyze persisted rows above the known drug, without re-ranking."""
    known_rank = _known_rank(case)
    rows = sorted(_ranked_rows(case), key=lambda row: row["rank"])
    result: dict[str, Any] = {
        "drug_name": case.get("drug_name"),
        "disease_name": case.get("disease_name"),
        "known_rank": known_rank,
        "total_candidates": case.get("total_candidates"),
        "context_rows": len(rows),
        "context_ranks": [row["rank"] for row in rows],
        "diagnostic_status": "ready",
        "neighbors_above": [],
        "summary": {},
    }

    if known_rank is None:
        result["diagnostic_status"] = (
            "known_drug_not_in_final_reviewed_or_rank_not_persisted"
        )
        return result
    if not rows:
        result["diagnostic_status"] = "rank_context_not_persisted"
        return result

    known_rows = [row for row in rows if row["rank"] == known_rank]
    if not known_rows:
        result["diagnostic_status"] = (
            "known_drug_ranked_but_context_window_missing_known_row"
        )
        return result

    known_score = known_rows[0].get("composite_score")
    neighbors = [row for row in rows if row["rank"] < known_rank]
    neighbors = neighbors[-max_neighbors:] if max_neighbors > 0 else []
    public_neighbors = []
    for row in neighbors:
        evidence = summarize_evidence(row)
        score = row.get("composite_score")
        delta = None
        if isinstance(score, (int, float)) and isinstance(known_score, (int, float)):
            delta = round(float(score) - float(known_score), 4)
        public_neighbors.append({
            "rank": row["rank"],
            "drug_name": row.get("drug_name"),
            "target_symbol": row.get("target_symbol"),
            "molecule_chembl_id": row.get("molecule_chembl_id"),
            "composite_score": score,
            "score_delta_vs_known": delta,
            "score_components": row.get("score_components") or {},
            "strong_match": row.get("strong_match"),
            "mechanism_class": row.get("mechanism_class"),
            "therapeutic_role": row.get("therapeutic_role"),
            "target_discovery_method": row.get("target_discovery_method"),
            "evidence": evidence,
        })
    result["known_score"] = known_score
    result["neighbors_above"] = public_neighbors
    result["summary"] = dict(
        Counter(item["evidence"]["evidence_class"] for item in public_neighbors)
    )
    return result


def build_task43_report(
    results: dict[str, Any],
    max_neighbors: int = DEFAULT_MAX_NEIGHBORS,
) -> str:
    """Build a deterministic Markdown report from persisted JSON."""
    analyses = [
        analyze_above_known_drug(case, max_neighbors=max_neighbors)
        for case in results.get("cases", [])
    ]
    lines = [
        "# Offline ranking diagnostic",
        "",
        "This report is descriptive only. It does not establish that a "
        "higher-ranked candidate is a credible competing hypothesis, and it "
        "does not establish that the known drug was mis-scored.",
        "",
        "No ranking weights, thresholds, candidate pools, or source calls are "
        "used by this analysis.",
        "",
        "## Cases",
        "",
    ]
    for analysis in analyses:
        title = f"{analysis['drug_name']} / {analysis['disease_name']}"
        lines.extend([
            f"### {title}",
            "",
            f"- Diagnostic status: **{analysis['diagnostic_status']}**",
            f"- Known-drug rank: {analysis.get('known_rank') or '—'}"
            f" / {analysis.get('total_candidates') or '—'}",
            f"- Persisted context ranks: "
            f"{', '.join(map(str, analysis.get('context_ranks', []))) or '—'}",
        ])
        if analysis.get("known_score") is not None:
            lines.append(f"- Known-drug composite score: "
                         f"{analysis['known_score']}")
        neighbors = analysis.get("neighbors_above") or []
        if neighbors:
            lines.extend([
                "",
                "| Rank | Candidate | Target | Score | Δ vs known | Evidence class | Providers |",
                "|---:|---|---|---:|---:|---|---|",
            ])
            for neighbor in neighbors:
                evidence = neighbor["evidence"]
                lines.append(
                    f"| {neighbor['rank']} | {neighbor.get('drug_name') or '—'} | "
                    f"{neighbor.get('target_symbol') or '—'} | "
                    f"{neighbor.get('composite_score') if neighbor.get('composite_score') is not None else '—'} | "
                    f"{neighbor.get('score_delta_vs_known') if neighbor.get('score_delta_vs_known') is not None else '—'} | "
                    f"{evidence['evidence_class']} | "
                    f"{', '.join(evidence['providers']) or '—'} |"
                )
            lines.extend([
                "",
                "Evidence-class counts: "
                + ", ".join(
                    f"{key}={value}"
                    for key, value in sorted(analysis["summary"].items())
                ),
            ])
        elif analysis["diagnostic_status"] == "ready":
            lines.append("- No persisted higher-ranked neighbors were available.")
        lines.append("")

    lines.extend([
        "## Interpretation guardrail",
        "",
        "A `qualified_directional` row is evidence-bearing and directionally "
        "described, not proof that it is a valid disease hypothesis. A "
        "`none_or_identity_only` or `unqualified_or_unresolved` row is not "
        "support for a credible competing hypothesis. Human review of the "
        "underlying target and disease mechanism is still required.",
        "",
    ])
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline analysis of persisted acceptance rank context.")
    parser.add_argument("input", help="engineering_acceptance_results.json")
    parser.add_argument(
        "--output", default="-",
        help="Markdown output path, or '-' for stdout (default: '-')")
    parser.add_argument(
        "--max-neighbors", type=int, default=DEFAULT_MAX_NEIGHBORS)
    args = parser.parse_args(argv)
    if args.max_neighbors < 0:
        parser.error("--max-neighbors must be non-negative")
    results = load_acceptance_results(args.input)
    report = build_task43_report(results, args.max_neighbors)
    if args.output == "-":
        print(report)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())