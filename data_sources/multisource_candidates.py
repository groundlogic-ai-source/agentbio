"""
Multi-source, target-first repurposing candidate collection.

This module is the single place that fans a target out across the v2
TARGET-FIRST source adapters (GtoPdb, DrugCentral), converts every adapter
candidate/evidence blob into the normalized :class:`EvidenceRecord` contract
defined by :mod:`data_sources.evidence_ledger`, and merges them (together with
the legacy ChEMBL enriched candidates, when supplied) into ONE identity-keyed
union via :func:`evidence_ledger.merge_candidates`.

Design rules (each exercised by validation/test_multisource_candidates.py):

  1. TARGET-FIRST ONLY.  We call every adapter's target-first entry point with a
     UniProt accession (and, for DrugCentral, a gene fallback symbol).  We NEVER
     query any source by drug name — a name lookup would let a held-out
     benchmark drug leak in through the back door.

  2. NORMALIZE, DON'T RE-SCORE.  Each adapter row and each of its per-row
     evidence blobs becomes one or more EvidenceRecord objects.  Quality
     calibration, dedup-by-lineage and the active-moiety union are ALL delegated
     to the ledger; this module never invents a "more providers = better" bonus.
     Because the ledger deduplicates by lineage (not by provider), a drug found
     by GtoPdb AND DrugCentral AND ChEMBL is unioned by InChIKey block into ONE
     candidate whose distinct-evidence count reflects distinct artifacts only.

  3. REGULATORY APPROVAL IS EXPLICIT EVIDENCE.  When an adapter row is an
     approved / established product we add a REGULATORY_APPROVAL record carrying
     a ``phase`` measurement so the merged candidate's ``max_phase`` /
     ``is_approved_drug`` are populated even when the underlying source has no
     ChEMBL max_phase.

  4. SOURCE STATUS IS PRESERVED.  The returned envelope carries a
     ``source_status`` map (provider -> {status, error, release}) taken verbatim
     from each adapter envelope, so a caller can tell "no candidates" from
     "source was unavailable".

Public surface:
  * :func:`collect_target_candidates` — fan-out + normalize + merge.
  * :func:`normalize_chembl_enriched` — fold legacy ChEMBL enriched dicts into
    the same ledger as separate pChEMBL and assay-confidence records.
  * :func:`records_from_gtopdb_envelope` / :func:`records_from_drugcentral_envelope`
    — the per-adapter converters (exposed for unit testing).
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from data_sources import gtopdb, drugcentral_v2
from data_sources.evidence_ledger import (
    ContradictionStatus,
    Direction,
    EvidenceRecord,
    EvidenceRole,
    QualificationStatus,
    SourceType,
    merge_candidates,
    inchikey_block,
    normalize_name,
)

__all__ = [
    "collect_target_candidates",
    "normalize_chembl_enriched",
    "records_from_gtopdb_envelope",
    "records_from_drugcentral_envelope",
    "merge_chemist_candidates",
    "SUPPORTED_SOURCES",
    "DEFAULT_ENABLED_SOURCES",
    "normalize_enabled_sources",
]

# Supported target-first provider identifiers at the collection boundary.
# ``chembl`` is folded in via the ``chembl_enriched`` passthrough, while
# ``gtopdb`` and ``drugcentral`` are fanned out to their target-first adapters.
SUPPORTED_SOURCES = ("chembl", "gtopdb", "drugcentral")

# Default: every currently supported source is enabled (backward compatible).
DEFAULT_ENABLED_SOURCES = frozenset(SUPPORTED_SOURCES)


def normalize_enabled_sources(
    enabled_sources: Optional[Iterable[str]],
) -> frozenset[str]:
    """Normalize and validate an ``enabled_sources`` selection.

    ``None`` means "all supported sources" (backward compatible). Any provided
    iterable is lowercased/stripped and validated against SUPPORTED_SOURCES;
    an unknown provider is a hard error so a typo can never silently disable a
    source.
    """
    if enabled_sources is None:
        return DEFAULT_ENABLED_SOURCES
    normalized = {str(s).strip().lower() for s in enabled_sources if str(s).strip()}
    unknown = normalized - set(SUPPORTED_SOURCES)
    if unknown:
        raise ValueError(
            f"unknown source(s) in enabled_sources: {sorted(unknown)}; "
            f"supported: {sorted(SUPPORTED_SOURCES)}"
        )
    return frozenset(normalized)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _direction_from_action(action: Optional[str]) -> Direction:
    """Map a free-text pharmacological action onto a ledger Direction enum."""
    text = _clean(action).lower()
    if not text:
        return Direction.UNKNOWN
    if "antagonist" in text or "inhibitor" in text or "inhibition" in text:
        return (Direction.INHIBITOR if "inhib" in text else Direction.ANTAGONIST)
    if "agonist" in text or "activator" in text or "activation" in text:
        return (Direction.ACTIVATOR if "activ" in text else Direction.AGONIST)
    if "modulator" in text or "allosteric" in text:
        return Direction.MODULATOR
    return Direction.UNKNOWN


def _source_status(envelope: dict[str, Any]) -> dict[str, Any]:
    """Extract the provider-health slice we preserve in the return envelope."""
    return {
        "status": envelope.get("status"),
        "error": envelope.get("error"),
        "release": envelope.get("release"),
    }


_LEDGER_AUTHORITATIVE_FIELDS = {
    "drug_name", "molecule_chembl_id", "smiles", "inchikey",
    "pchembl_value", "confidence_score", "efficacy_confidence", "max_phase",
    "is_approved_drug", "source_chembl_ids", "source_activity_ids",
    "target_symbol", "uniprot_id", "target_discovery_method", "disease_name",
    "ot_association_score", "source_types", "source_health",
    "target_memberships", "_evidence_ledger",
}

_PROCESS_METADATA_FIELDS = {
    "mechanism_class",
    "therapeutic_role",
    "process_support",
    "process_source_status",
    "process_memberships",
}


def _same_candidate(a: dict[str, Any], b: dict[str, Any]) -> bool:
    ab = inchikey_block(_clean(a.get("inchikey")))
    bb = inchikey_block(_clean(b.get("inchikey")))
    if ab and bb:
        return ab == bb
    aid = _clean(a.get("molecule_chembl_id"))
    bid = _clean(b.get("molecule_chembl_id"))
    if aid and bid and aid.upper().startswith("CHEMBL") and bid.upper().startswith("CHEMBL"):
        return aid.upper() == bid.upper()
    # Name is a last resort only when neither side has a structural identity.
    if not ab and not bb:
        an = normalize_name(_clean(a.get("drug_name")))
        bn = normalize_name(_clean(b.get("drug_name")))
        return bool(an and bn and an == bn)
    return False


def _overlay_passthrough_fields(
    merged: list[dict[str, Any]],
    inputs: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve legacy enrichment without letting it overwrite ledger facts."""
    source_rows = [row for row in inputs if isinstance(row, dict)]
    for cand in merged:
        matches = [row for row in source_rows if _same_candidate(cand, row)]
        if not matches:
            continue
        # Deterministic best row: strongest legacy affinity, then stable ID/name.
        matches.sort(key=lambda row: (
            -(_to_float(row.get("pchembl_value")) or -1.0),
            _clean(row.get("molecule_chembl_id")),
            _clean(row.get("drug_name")),
        ))
        for row in matches:
            for key, value in row.items():
                if (key in _LEDGER_AUTHORITATIVE_FIELDS
                        or key in _PROCESS_METADATA_FIELDS
                        or key.startswith("_")):
                    continue
                if key not in cand or cand.get(key) in (None, "", [], {}):
                    cand[key] = value

        # Process classifications are evidence-backed provenance, not generic
        # passthrough fields. Preserve every cited target/process membership and
        # let that evidence override default ``disease_modifying`` metadata from
        # another target row for the same active moiety.
        memberships: list[dict[str, Any]] = []
        seen_memberships: set[tuple[str, str, str, str]] = set()
        for row in matches:
            mechanism_class = _clean(row.get("mechanism_class"))
            support = [
                item for item in (row.get("process_support") or [])
                if isinstance(item, dict)
            ]
            if not mechanism_class or not support:
                continue
            membership = {
                "target_symbol": _clean(row.get("target_symbol")),
                "uniprot_id": _clean(row.get("uniprot_id")),
                "target_discovery_method": _clean(
                    row.get("target_discovery_method")
                ),
                "mechanism_class": mechanism_class,
                "therapeutic_role": (
                    _clean(row.get("therapeutic_role"))
                    or "disease_modifying"
                ),
                "process_support": support,
                "process_source_status": row.get("process_source_status"),
            }
            key = (
                membership["target_symbol"],
                membership["uniprot_id"],
                membership["mechanism_class"],
                membership["therapeutic_role"],
            )
            if key not in seen_memberships:
                seen_memberships.add(key)
                memberships.append(membership)

        memberships.sort(key=lambda item: (
            item["target_symbol"],
            item["uniprot_id"],
            item["mechanism_class"],
            item["therapeutic_role"],
        ))
        if memberships:
            cand["process_memberships"] = memberships
            canonical = memberships[0]
            for key in (
                "mechanism_class",
                "therapeutic_role",
                "process_support",
                "process_source_status",
            ):
                cand[key] = canonical[key]
        else:
            # No cited process membership: retain the former deterministic
            # passthrough behavior for non-process candidates.
            for row in matches:
                for key in (
                    "mechanism_class",
                    "therapeutic_role",
                    "process_support",
                    "process_source_status",
                ):
                    value = row.get(key)
                    if (key not in cand
                            or cand.get(key) in (None, "", [], {})):
                        if value not in (None, "", [], {}):
                            cand[key] = value
    return merged


def merge_chemist_candidates(
    candidates: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Union already-normalized Chemist candidates across target pools."""
    rows = [row for row in candidates if isinstance(row, dict)]
    records: list[dict[str, Any]] = []
    for row in rows:
        records.extend((row.get("_evidence_ledger") or {}).get("records") or [])
    if not records:
        # Compatibility for callers predating the ledger. The production
        # Chemist always emits records after v2 integration.
        return rows
    return _overlay_passthrough_fields(merge_candidates(records), rows)


# ---------------------------------------------------------------------------
# GtoPdb converter
# ---------------------------------------------------------------------------

def records_from_gtopdb_envelope(
    envelope: dict[str, Any],
    *,
    uniprot_id: str = "",
    gene: str = "",
    disease_name: str = "",
    ot_score: Optional[float] = None,
    target_discovery_method: str = "",
) -> list[EvidenceRecord]:
    """Convert a GtoPdb ``get_target_interactions`` envelope into records.

    Each candidate row yields:
      * a MECHANISM target-link record (the curated ligand->target action), and
      * a REGULATORY_APPROVAL record when the ligand is an approved drug,
    plus one PUBLICATION record per distinct literature ref (PMID) so co-cited
    papers dedup by lineage against other providers.
    """
    if not isinstance(envelope, dict):
        return []
    records: list[EvidenceRecord] = []
    for cand in envelope.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        molecule_id = _clean(cand.get("chembl_id")) or _clean(
            cand.get("provider_ligand_id"))
        name = _clean(cand.get("name")) or _clean(cand.get("inn"))
        inchikey = _clean(cand.get("inchikey"))
        smiles = _clean(cand.get("smiles"))
        target_name = gene or _clean(cand.get("target_name"))
        action = _clean(cand.get("action")) or _clean(cand.get("action_type"))
        is_withdrawn = bool(cand.get("is_withdrawn"))

        base = dict(
            provider="gtopdb",
            molecule_id=molecule_id,
            molecule_name=name,
            inchikey=inchikey,
            smiles=smiles,
            target_symbol=target_name,
            target_accession=uniprot_id,
            target_species="Homo sapiens",
            disease_name=disease_name,
        )

        # Curated mechanism / target-link record (the GtoPdb interaction).
        records.append(EvidenceRecord(
            source_type=SourceType.MECHANISM,
            evidence_role=EvidenceRole.TARGET_LINK,
            source_id=_clean(cand.get("provider_interaction_id")),
            action=action,
            direction=_direction_from_action(action),
            measurement_type=_clean(cand.get("affinity_parameter")),
            measurement_value=_to_float(cand.get("affinity")),
            assay_id="",
            context="gtopdb_interaction",
            qualification_status=QualificationStatus.QUALIFIED,
            contradiction_status=(
                ContradictionStatus.CONTRADICTED if is_withdrawn
                else ContradictionStatus.NONE),
            **base,
        ))

        # Explicit regulatory-approval evidence for an approved ligand.
        if bool(cand.get("is_approved", True)):
            records.append(_approval_record(
                provider="gtopdb",
                source_id=f"gtopdb-approval:{_clean(cand.get('provider_ligand_id'))}",
                base=base,
                withdrawn=is_withdrawn,
            ))

        # One publication record per distinct PMID (lineage dedups across
        # providers so a co-cited paper is never double counted).
        for ref in cand.get("refs") or []:
            if not isinstance(ref, dict):
                continue
            pmid = _clean(ref.get("pmid"))
            if not pmid:
                continue
            records.append(EvidenceRecord(
                provider="gtopdb",
                source_type=SourceType.PUBLICATION,
                evidence_role=EvidenceRole.EFFICACY,
                source_id=f"gtopdb-ref:{pmid}",
                publication_id=pmid,
                molecule_id=molecule_id,
                molecule_name=name,
                inchikey=inchikey,
                smiles=smiles,
                target_symbol=target_name,
                target_accession=uniprot_id,
                target_species="Homo sapiens",
                disease_name=disease_name,
                qualification_status=QualificationStatus.QUALIFIED,
            ))

    if ot_score is not None:
        records.extend(_genetic_link_records(
            records, uniprot_id, disease_name, ot_score, provider="gtopdb"))
    return records


# ---------------------------------------------------------------------------
# DrugCentral converter
# ---------------------------------------------------------------------------

def records_from_drugcentral_envelope(
    envelope: dict[str, Any],
    *,
    uniprot_id: str = "",
    disease_name: str = "",
    ot_score: Optional[float] = None,
    target_discovery_method: str = "",
) -> list[EvidenceRecord]:
    """Convert a DrugCentral ``get_target_interactions`` envelope into records.

    Each candidate row yields:
      * a BIOACTIVITY_ASSAY efficacy record per distinct activity (act_id), and
      * a REGULATORY_APPROVAL record (DrugCentral candidates are established
        marketed products by construction).
    """
    if not isinstance(envelope, dict):
        return []
    records: list[EvidenceRecord] = []
    for cand in envelope.get("candidates") or []:
        if not isinstance(cand, dict):
            continue
        name = _clean(cand.get("name"))
        molecule_id = _clean(cand.get("struct_id"))
        inchikey = _clean(cand.get("inchikey"))
        smiles = _clean(cand.get("smiles"))
        gene = _clean(cand.get("gene"))
        accession = _clean(cand.get("accession")) or _clean(
            cand.get("swissprot")) or uniprot_id
        action = _clean(cand.get("action_type")) or _clean(cand.get("moa"))

        base = dict(
            provider="drugcentral",
            molecule_id=molecule_id,
            molecule_name=name,
            inchikey=inchikey,
            smiles=smiles,
            target_symbol=gene or _clean(cand.get("target_name")),
            target_accession=accession,
            target_species="Homo sapiens",
            disease_name=disease_name,
        )

        # One efficacy record per distinct activity blob (dedups by act_id).
        evidence = cand.get("evidence") or []
        seen_act: set[str] = set()
        emitted = False
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            act_id = _clean(ev.get("act_id"))
            key = act_id or f"row:{molecule_id}"
            if key in seen_act:
                continue
            seen_act.add(key)
            emitted = True
            records.append(EvidenceRecord(
                source_type=SourceType.BIOACTIVITY_ASSAY,
                evidence_role=EvidenceRole.EFFICACY,
                source_id=f"drugcentral-act:{act_id}" if act_id else
                          f"drugcentral-row:{molecule_id}",
                assay_id=act_id,
                action=_clean(ev.get("action_type")) or action,
                direction=_direction_from_action(
                    _clean(ev.get("action_type")) or action),
                measurement_type=_clean(ev.get("act_type")),
                measurement_value=_to_float(ev.get("act_value")),
                measurement_unit=_clean(ev.get("act_unit")),
                context=_clean(ev.get("act_source")),
                qualification_status=QualificationStatus.QUALIFIED,
                **base,
            ))
        if not emitted:
            # No activity blobs: still emit a bare efficacy row so the drug is
            # represented (established product with curated MoA only).
            records.append(EvidenceRecord(
                source_type=SourceType.MECHANISM,
                evidence_role=EvidenceRole.TARGET_LINK,
                source_id=f"drugcentral-row:{molecule_id}",
                action=action,
                direction=_direction_from_action(action),
                qualification_status=QualificationStatus.QUALIFIED,
                **base,
            ))

        # DrugCentral candidates are established (OFP/OFM) products by filter.
        records.append(_approval_record(
            provider="drugcentral",
            source_id=f"drugcentral-approval:{molecule_id}",
            base=base,
            withdrawn=False,
        ))

    if ot_score is not None:
        records.extend(_genetic_link_records(
            records, uniprot_id, disease_name, ot_score,
            provider="drugcentral"))
    return records


# ---------------------------------------------------------------------------
# legacy ChEMBL enriched -> ledger
# ---------------------------------------------------------------------------

def normalize_chembl_enriched(
    enriched: Iterable[dict[str, Any]],
) -> list[EvidenceRecord]:
    """Fold existing ChEMBL *enriched* candidate dicts into ledger records.

    ``enriched`` are the dicts produced by the legacy Chemist pipeline
    (agents/chemist.py ``_enrich_compounds`` over chembl candidates): each
    carries ``molecule_chembl_id``, ``drug_name``/``pref_name``, ``smiles``,
    ``inchikey``, ``pchembl_value``, ``confidence_score`` (0-9 assay scale),
    ``max_phase``, ``target_symbol``, ``uniprot_id``, ``disease_name``,
    ``ot_association_score`` and ``target_discovery_method``.

    We emit SEPARATE records for the two orthogonal ChEMBL signals so neither is
    lost in the union:
      * a BIOACTIVITY_ASSAY record carrying the ``pchembl`` measurement, and
      * a BIOACTIVITY_ASSAY record carrying the ``assay_confidence`` measurement
        (the 0-9 curator confidence), which the ledger surfaces as the merged
        candidate's ``confidence_score``.
    Plus a REGULATORY_APPROVAL record when ``max_phase >= 4`` and, when an OT
    association score is present, a GENETIC_ASSOCIATION disease-link record.
    """
    records: list[EvidenceRecord] = []
    for c in enriched or []:
        if not isinstance(c, dict):
            continue
        molecule_id = _clean(c.get("molecule_chembl_id"))
        name = _clean(c.get("drug_name")) or _clean(c.get("pref_name"))
        inchikey = _clean(c.get("inchikey"))
        smiles = _clean(c.get("smiles")) or _clean(c.get("canonical_smiles"))
        target_symbol = _clean(c.get("target_symbol"))
        uniprot_id = _clean(c.get("uniprot_id"))
        disease_name = _clean(c.get("disease_name"))
        discovery = _clean(c.get("target_discovery_method"))
        ot_score = _to_float(c.get("ot_association_score"))
        max_phase = _to_float(c.get("max_phase"))
        pchembl = _to_float(c.get("pchembl_value"))
        confidence = _to_float(c.get("confidence_score"))

        # Stable per-molecule assay lineage anchor.  We DON'T key on individual
        # activity ids here (the enriched dict aggregates over a molecule); the
        # ledger's lineage still keeps this ChEMBL assay datum distinct from
        # GtoPdb/DrugCentral evidence while collapsing exact ChEMBL re-imports.
        act_ids = c.get("source_activity_ids") or []
        assay_anchor = (f"{molecule_id}:{act_ids[0]}"
                        if act_ids else molecule_id)

        base = dict(
            provider="chembl",
            molecule_id=molecule_id,
            molecule_name=name,
            inchikey=inchikey,
            smiles=smiles,
            target_symbol=target_symbol,
            target_accession=uniprot_id,
            target_species="Homo sapiens",
            disease_name=disease_name,
        )

        # pChEMBL bioactivity record.
        records.append(EvidenceRecord(
            source_type=SourceType.BIOACTIVITY_ASSAY,
            evidence_role=EvidenceRole.EFFICACY,
            source_id=f"chembl-pchembl:{assay_anchor}",
            assay_id=f"chembl-assay:{assay_anchor}",
            measurement_type="pchembl",
            measurement_value=pchembl,
            qualification_status=QualificationStatus.QUALIFIED,
            **base,
        ))

        # SEPARATE assay-confidence record (0-9 curator scale) — kept distinct
        # from the pChEMBL potency record so both survive the union.
        if confidence is not None:
            records.append(EvidenceRecord(
                source_type=SourceType.BIOACTIVITY_ASSAY,
                evidence_role=EvidenceRole.EFFICACY,
                source_id=f"chembl-confidence:{assay_anchor}",
                assay_id=f"chembl-confidence:{assay_anchor}",
                measurement_type="assay_confidence",
                measurement_value=confidence,
                qualification_status=QualificationStatus.QUALIFIED,
                **base,
            ))

        if max_phase is not None and max_phase >= 4:
            records.append(_approval_record(
                provider="chembl",
                source_id=f"chembl-approval:{molecule_id}",
                base=base,
                withdrawn=False,
                phase=max_phase,
            ))

        if ot_score is not None:
            records.append(_ot_disease_link(
                provider="chembl", base=base, uniprot_id=uniprot_id,
                disease_name=disease_name, ot_score=ot_score))

        # Carry the discovery method through a target-link record when set.
        if discovery:
            records.append(EvidenceRecord(
                source_type=_discovery_source_type(discovery),
                evidence_role=EvidenceRole.TARGET_LINK,
                source_id=f"chembl-discovery:{molecule_id}",
                qualification_status=QualificationStatus.QUALIFIED,
                **base,
            ))
    return records


# ---------------------------------------------------------------------------
# shared record builders
# ---------------------------------------------------------------------------

def _approval_record(*, provider: str, source_id: str, base: dict[str, Any],
                     withdrawn: bool, phase: float = 4.0) -> EvidenceRecord:
    """A REGULATORY_APPROVAL record carrying a ``phase`` measurement."""
    return EvidenceRecord(
        source_type=SourceType.REGULATORY_APPROVAL,
        evidence_role=EvidenceRole.APPROVAL,
        source_id=source_id,
        measurement_type="phase",
        measurement_value=phase,
        context=("withdrawn" if withdrawn else "approved"),
        qualification_status=QualificationStatus.QUALIFIED,
        contradiction_status=(ContradictionStatus.CONTRADICTED if withdrawn
                              else ContradictionStatus.NONE),
        **base,
    )


def _discovery_source_type(discovery: str) -> SourceType:
    return {
        "genetic_association": SourceType.GENETIC_ASSOCIATION,
        "pharmacological_precedent": SourceType.MECHANISM,
        "pathway_neighbor": SourceType.PATHWAY,
    }.get(discovery, SourceType.MECHANISM)


def _ot_disease_link(*, provider: str, base: dict[str, Any], uniprot_id: str,
                     disease_name: str, ot_score: float) -> EvidenceRecord:
    b = dict(base)
    b["provider"] = provider
    return EvidenceRecord(
        source_type=SourceType.GENETIC_ASSOCIATION,
        evidence_role=EvidenceRole.DISEASE_LINK,
        source_id=f"{provider}-ot:{uniprot_id}:{disease_name}",
        measurement_type="ot_association",
        measurement_value=ot_score,
        qualification_status=QualificationStatus.QUALIFIED,
        **{k: v for k, v in b.items()},
    )


def _genetic_link_records(existing: list[EvidenceRecord], uniprot_id: str,
                          disease_name: str, ot_score: float, *,
                          provider: str) -> list[EvidenceRecord]:
    """Attach one OT genetic disease-link record per distinct moiety already
    present in ``existing`` (so the OT score lands on real candidates, keyed by
    structure, not on a phantom standalone record)."""
    seen: set[tuple[str, str, str]] = set()
    out: list[EvidenceRecord] = []
    for r in existing:
        ident = (r.inchikey, r.molecule_id, r.molecule_name)
        if ident in seen:
            continue
        seen.add(ident)
        base = dict(
            provider=provider,
            molecule_id=r.molecule_id,
            molecule_name=r.molecule_name,
            inchikey=r.inchikey,
            smiles=r.smiles,
            target_symbol=r.target_symbol,
            target_accession=uniprot_id or r.target_accession,
            target_species="Homo sapiens",
            disease_name=disease_name,
        )
        out.append(_ot_disease_link(
            provider=provider, base=base, uniprot_id=uniprot_id,
            disease_name=disease_name, ot_score=ot_score))
    return out


def _process_link_records(
    existing: list[EvidenceRecord],
    *,
    uniprot_id: str,
    gene: str,
    disease_name: str,
    mechanism_class: str,
    therapeutic_role: str,
    process_support: Iterable[dict[str, Any]],
) -> list[EvidenceRecord]:
    """Attach cited disease→process→target assertions to each active moiety."""
    if not mechanism_class:
        return []
    support = [s for s in process_support or [] if isinstance(s, dict)]
    if not support:
        return []
    seen_moieties: set[tuple[str, str, str]] = set()
    out: list[EvidenceRecord] = []
    for rec in existing:
        ident = (rec.inchikey, rec.molecule_id, rec.molecule_name)
        if ident in seen_moieties:
            continue
        seen_moieties.add(ident)
        for source in support:
            publication_id = _clean(source.get("pmid")) or _clean(
                source.get("pmcid"))
            if not publication_id:
                continue
            out.append(EvidenceRecord(
                provider="europepmc",
                source_type=SourceType.PUBLICATION,
                evidence_role=EvidenceRole.DISEASE_LINK,
                source_id=f"europepmc:{publication_id}:{mechanism_class}",
                publication_id=publication_id,
                molecule_id=rec.molecule_id,
                molecule_name=rec.molecule_name,
                inchikey=rec.inchikey,
                smiles=rec.smiles,
                target_symbol=gene or rec.target_symbol,
                target_accession=uniprot_id or rec.target_accession,
                target_species="Homo sapiens",
                disease_name=disease_name,
                phenotype=mechanism_class,
                context=(
                    f"mechanism_class={mechanism_class};"
                    f"therapeutic_role={therapeutic_role};"
                    f"title={_clean(source.get('title'))}"
                ),
                qualification_status=QualificationStatus.QUALIFIED,
            ))
    return out


# ---------------------------------------------------------------------------
# top-level fan-out
# ---------------------------------------------------------------------------

def collect_target_candidates(
    uniprot_id: str,
    gene: str,
    disease_name: str,
    ot_score: Optional[float],
    target_discovery_method: str,
    repurposing_only: bool = True,
    *,
    chembl_enriched: Optional[Iterable[dict[str, Any]]] = None,
    mechanism_class: str = "",
    therapeutic_role: str = "disease_modifying",
    process_support: Optional[Iterable[dict[str, Any]]] = None,
    enabled_sources: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    """Target-first multi-source candidate collection.

    Calls the GtoPdb and DrugCentral target-first adapters (by UniProt
    accession, with a gene fallback for DrugCentral — NEVER by drug name),
    converts every returned candidate/evidence blob into normalized
    EvidenceRecord objects, adds explicit regulatory-approval records, folds in
    the legacy ChEMBL enriched candidates when supplied, and merges the whole
    pool via :func:`evidence_ledger.merge_candidates` (union by active moiety,
    dedup by lineage — never a provider-count bonus).

    Returns::

        {
          "candidates":    [ <chemist-compatible dict>, ... ],
          "source_status": { provider: {status, error, release}, ... },
        }

    ``source_status`` preserves each adapter's health verbatim so callers can
    distinguish "source unavailable" from a genuine "no candidates".

    ``enabled_sources`` selects which providers are contacted at this collection
    boundary. ``None`` (the default) enables every supported source, preserving
    the historical behavior. Supported values are ``chembl``, ``gtopdb`` and
    ``drugcentral``. A DISABLED provider's target-first adapter is NEVER called
    and surfaces in ``source_status`` with ``status == "disabled"`` — which is
    an explicit ablation choice, NOT a source failure and NOT the same as
    "unavailable".
    """
    enabled = normalize_enabled_sources(enabled_sources)

    records: list[EvidenceRecord] = []
    source_status: dict[str, Any] = {}

    _disabled = {"status": "disabled", "error": None, "release": None}

    # GtoPdb target-first fan-out (skipped entirely when disabled).
    if "gtopdb" in enabled:
        gtopdb_env = gtopdb.get_target_interactions(
            uniprot_id, approved_only=repurposing_only)
        records.extend(records_from_gtopdb_envelope(
            gtopdb_env, uniprot_id=uniprot_id, gene=gene,
            disease_name=disease_name,
            ot_score=ot_score,
            target_discovery_method=target_discovery_method))
        source_status["gtopdb"] = _source_status(gtopdb_env)
    else:
        source_status["gtopdb"] = dict(_disabled)

    # DrugCentral target-first fan-out (skipped entirely when disabled).
    if "drugcentral" in enabled:
        drugcentral_env = drugcentral_v2.get_target_interactions(
            uniprot_id, gene=gene or None)
        records.extend(records_from_drugcentral_envelope(
            drugcentral_env, uniprot_id=uniprot_id, disease_name=disease_name,
            ot_score=ot_score,
            target_discovery_method=target_discovery_method))
        source_status["drugcentral"] = _source_status(drugcentral_env)
    else:
        source_status["drugcentral"] = dict(_disabled)

    # ChEMBL enters via the enriched passthrough. When ChEMBL is disabled the
    # caller-supplied enriched rows are NOT folded into the ledger.
    chembl_on = "chembl" in enabled
    if chembl_on and chembl_enriched:
        records.extend(normalize_chembl_enriched(chembl_enriched))

    records.extend(_process_link_records(
        records,
        uniprot_id=uniprot_id,
        gene=gene,
        disease_name=disease_name,
        mechanism_class=mechanism_class,
        therapeutic_role=therapeutic_role,
        process_support=process_support or [],
    ))

    candidates = merge_candidates(records)
    if chembl_on:
        candidates = _overlay_passthrough_fields(
            candidates, list(chembl_enriched or []))

    if not chembl_on:
        source_status["chembl"] = dict(_disabled)
    elif chembl_enriched is not None:
        source_status["chembl"] = {
            "status": "ok" if chembl_enriched else "empty",
            "error": None,
            "release": None,
        }

    return {"candidates": candidates, "source_status": source_status}
