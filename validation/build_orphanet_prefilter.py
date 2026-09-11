"""Build a cheap, auditable Orphanet gene-association pre-screen queue.

This is intentionally not the six-filter AgentBio screen.  It only joins the
live Orphanet disease universe to Orphanet's assessed disease-causing germline
gene associations.  Approved-treatment status, prior trials, target
druggability, and mechanism direction remain unassessed and must be checked in
the expensive downstream screen.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from typing import Any

import requests

# Support both `python3 validation/build_orphanet_prefilter.py` and
# `python3 -m validation.build_orphanet_prefilter`.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agents.target_selection import _build_candidate_universe, _disorder_group_maps


PRODUCT6_URL = "https://www.orphadata.com/data/xml/en_product6.xml"
OUTPUT_PATH = os.path.join(
    REPO_ROOT,
    "output",
    "orphanet_prefilter_queue.json",
)


def _text(parent: ET.Element, path: str) -> str | None:
    value = parent.findtext(path)
    return value.strip() if value and value.strip() else None


def _parse_gene_associations(xml_bytes: bytes) -> dict[str, list[dict[str, Any]]]:
    root = ET.fromstring(xml_bytes)
    by_code: dict[str, list[dict[str, Any]]] = {}

    for disorder in root.findall(".//Disorder"):
        code = _text(disorder, "OrphaCode")
        if not code:
            continue

        associations: list[dict[str, Any]] = []
        for assoc in disorder.findall("./DisorderGeneAssociationList/DisorderGeneAssociation"):
            gene = assoc.find("Gene")
            if gene is None:
                continue

            associations.append(
                {
                    "symbol": _text(gene, "Symbol"),
                    "name": _text(gene, "Name"),
                    "association_type": _text(
                        assoc, "DisorderGeneAssociationType/Name"
                    ),
                    "association_status": _text(
                        assoc, "DisorderGeneAssociationStatus/Name"
                    ),
                    "source_of_validation": _text(assoc, "SourceOfValidation"),
                }
            )

        if associations:
            by_code[code] = associations

    return by_code


def build_queue() -> dict[str, Any]:
    universe = _build_candidate_universe()
    orphanet_universe = {
        str(row["orpha_code"]): row
        for row in universe
        if row.get("source") == "orphanet" and row.get("orpha_code") is not None
    }

    response = requests.get(PRODUCT6_URL, timeout=180)
    response.raise_for_status()
    associations_by_code = _parse_gene_associations(response.content)

    group_by_code, _ = _disorder_group_maps()
    rows: list[dict[str, Any]] = []
    association_statuses: Counter[str] = Counter()

    for code, associations in associations_by_code.items():
        disease = orphanet_universe.get(code)
        if disease is None:
            continue

        confirmed = [
            association
            for association in associations
            if association.get("association_type")
            == "Disease-causing germline mutation(s) in"
            and association.get("association_status") == "Assessed"
        ]
        if not confirmed:
            continue

        for association in confirmed:
            association_statuses[association.get("association_status") or "unknown"] += 1

        rows.append(
            {
                "disease_name": disease["name"],
                "orpha_code": disease["orpha_code"],
                "disorder_group": group_by_code.get(code),
                "gene_associations": confirmed,
                "gene_count": len(confirmed),
                "confirmed_germline_gene_association": True,
                "approved_treatment_status": "not_checked",
                "prior_trial_status": "not_checked",
                "target_druggability_status": "not_checked",
                "mechanism_direction_status": "not_checked",
            }
        )

    rows.sort(key=lambda row: (row["disease_name"].casefold(), row["orpha_code"]))
    return {
        "schema_version": "orphanet-prefilter-v1",
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source": {
            "name": "Orphanet disease-gene associations",
            "url": PRODUCT6_URL,
            "scope": (
                "Assessed disease-causing germline associations joined to the "
                "specific Orphanet universe; no downstream six-filter claims."
            ),
        },
        "counts": {
            "specific_orphanet_universe": len(orphanet_universe),
            "product6_diseases_with_any_gene_association": len(associations_by_code),
            "prefilter_candidates": len(rows),
            "confirmed_gene_associations": sum(row["gene_count"] for row in rows),
            "association_status_counts": dict(association_statuses),
        },
        "candidates": rows,
    }


def main() -> None:
    payload = build_queue()
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(json.dumps(payload["counts"], indent=2, sort_keys=True))
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()