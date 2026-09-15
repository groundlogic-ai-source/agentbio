"""Target-family safety-liability checks.

These checks are deliberately narrower than a general adverse-event screen:
they look for a documented drug mechanism against a related protein whose
cross-reactivity can carry a serious, target-family-specific liability.

The result is disclosure-only.  A mechanism record is not a potency/selectivity
measurement, and it is not enough to apply a score cap or make a clinical
safety determination.
"""

from __future__ import annotations

from typing import Any, Iterable


# KCNH1 is Kv10.1/EAG1; KCNH2 is Kv11.1/hERG.  The broader KCNH family is
# included so the rule is not hard-coded to one disease or one candidate.
KCNH_FAMILY_TARGETS = frozenset(f"KCNH{i}" for i in range(1, 9))
KCNH2_HERG_IDENTIFIERS = frozenset({
    "KCNH2",
    "HERG",
    "KV11.1",
    "KV11",
    "Q12809",  # human hERG/Kv11.1 UniProt accession
})


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def _is_herg_target(row: dict[str, Any]) -> bool:
    identifiers = {
        _norm(row.get("target_symbol")),
        _norm(row.get("target_name")),
        _norm(row.get("target_accession")),
        *(_norm(v) for v in (row.get("gene_symbols") or [])),
        *(_norm(v) for v in (row.get("uniprot_ids") or [])),
    }
    return bool(identifiers & KCNH2_HERG_IDENTIFIERS)


def assess_target_family_safety(
    target_symbol: str | None,
    *,
    mechanism_envelope: dict[str, Any] | None = None,
    ledger_records: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assess a related-family safety liability from persisted evidence.

    The current relationship is KCNH-family target → hERG/KCNH2 blockade
    concern.  ``FLAGGED`` means a mechanism identity for the same drug names
    KCNH2/hERG; it does not claim a concentration, selectivity ratio, or
    clinically meaningful exposure.
    """
    target = _norm(target_symbol)
    base = {
        "schema_version": "target-family-safety-v1",
        "status": "NOT_APPLICABLE",
        "pursued_target": target,
        "liability_target": None,
        "relationship": None,
        "evidence": [],
        "disclosure_only": True,
        "score_effect": "none",
    }
    if target not in KCNH_FAMILY_TARGETS or target == "KCNH2":
        return base

    envelope = mechanism_envelope or {}
    matching_targets: list[dict[str, Any]] = []
    for row in envelope.get("targets") or []:
        if isinstance(row, dict) and _is_herg_target(row):
            matching_targets.append(row)

    # A ledger can retain an off-target/cross-target observation even when the
    # mechanism identity lookup was not the source that found it.
    for row in ledger_records or []:
        if not isinstance(row, dict):
            continue
        if _is_herg_target(row):
            matching_targets.append(row)

    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in matching_targets:
        mechanisms = [
            str(v).strip() for v in (row.get("mechanisms") or [])
            if str(v).strip()
        ]
        actions = [
            str(v).strip() for v in (row.get("action_types") or [])
            if str(v).strip()
        ]
        item = {
            "target_chembl_id": row.get("target_chembl_id"),
            "target_name": row.get("target_name"),
            "gene_symbols": row.get("gene_symbols") or [],
            "uniprot_ids": row.get("uniprot_ids") or [],
            "mechanisms": mechanisms,
            "action_types": actions,
            "source": "chembl_mechanism_identity",
        }
        key = repr(sorted(item.items(), key=lambda pair: pair[0]))
        if key not in seen:
            seen.add(key)
            evidence.append(item)

    if evidence:
        return {
            **base,
            "status": "FLAGGED",
            "liability_target": "KCNH2/hERG (Kv11.1)",
            "relationship": (
                "A related KCNH-family target has a documented hERG/KCNH2 "
                "mechanism for this drug; target selectivity and clinically "
                "relevant exposure are unresolved."
            ),
            "evidence": evidence,
        }

    status = _norm(envelope.get("status"))
    if status in {"UNAVAILABLE", "ERROR"}:
        return {
            **base,
            "status": "UNKNOWN",
            "liability_target": "KCNH2/hERG (Kv11.1)",
            "relationship": (
                "The related-family pharmacology lookup was unavailable; "
                "selectivity and cardiac liability were not assessed."
            ),
        }

    return {
        **base,
        "status": "NOT_FOUND",
        "liability_target": "KCNH2/hERG (Kv11.1)",
        "relationship": (
            "No KCNH2/hERG mechanism identity was found in the bounded "
            "lookup; this is not evidence of selectivity or safety."
        ),
    }