"""
Common evidence ledger + active-moiety candidate union (PURE module).

This module is deliberately self-contained and side-effect free: no network,
no filesystem, no third-party imports.  It gives the pipeline ONE normalized
evidence contract shared by every provider/source adapter, a deterministic way
to deduplicate the *same underlying assay/publication/label/trial* across
providers (so that having N providers report the same fact never inflates the
evidence), and an identity-keyed union of candidates that is byte-compatible
with the Chemist output dictionaries consumed downstream (agents/chemist.py).

Design invariants (each is exercised by validation/test_evidence_ledger.py):

  1. Evidence is deduplicated by a DETERMINISTIC LINEAGE KEY, not by provider.
     If ChEMBL and Open Targets both cite PMID 12345 for the same assay, that
     is ONE piece of evidence, counted once.  Provider count is never a boost.

  2. Candidate identity prioritises the STRUCTURAL active moiety /
     connectivity: the first InChIKey block (the 14-char skeleton) collapses
     salt/ester/hydrate forms of one active moiety.  When no structure is
     available we fall back to a stable provider molecule ID, and only then to
     a normalized name.  We NEVER collapse two records that merely share a name
     but have distinct structures, and we NEVER collapse structurally-unrelated
     records when structure is simply absent (name fallback is last-resort and
     never overrides a present-but-different structural block).

  3. Evidence quality is calibrated per MODALITY without requiring a pChEMBL
     value.  Absence of a modality returns NOT_APPLICABLE (a sentinel), which
     is distinct from a real 0.0 score.  Efficacy confidence and safety
     confidence are kept separate and never averaged together.

  4. The merged candidate preserves every source record, every target
     membership, every source_type, every contradiction, the MAX approval
     phase seen, the BEST quantitative affinity seen, and an explicit
     per-source health map.

The module has no dependency on agents/ or on the source adapters; adapters are
expected to emit EvidenceRecord-shaped dicts and callers assemble Candidates.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Optional

try:  # TypedDict is used purely for documentation of the wire contract.
    from typing_extensions import TypedDict
except Exception:  # pragma: no cover - typing_extensions is a repo dependency
    from typing import TypedDict  # type: ignore


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class EvidenceRole(str, Enum):
    """What role a record plays in the case for a candidate."""
    EFFICACY = "efficacy"          # activity / mechanism supporting the use
    SAFETY = "safety"              # black-box, withdrawal, adverse signal
    TARGET_LINK = "target_link"    # drug -> target association / mechanism
    DISEASE_LINK = "disease_link"  # target/drug -> disease association
    STRUCTURE = "structure"        # structural / identity evidence
    APPROVAL = "approval"          # regulatory approval / max_phase
    OTHER = "other"


class SourceType(str, Enum):
    """Broad modality of an evidence source (drives quality calibration)."""
    BIOACTIVITY_ASSAY = "bioactivity_assay"      # ChEMBL IC50/Ki etc.
    MECHANISM = "mechanism"                        # curated drug->target MoA
    GENETIC_ASSOCIATION = "genetic_association"    # Open Targets genetics
    PATHWAY = "pathway"                            # Reactome / network
    CLINICAL_TRIAL = "clinical_trial"              # ClinicalTrials.gov
    DRUG_LABEL = "drug_label"                      # openFDA label
    PUBLICATION = "publication"                    # literature (PMID)
    REGULATORY_APPROVAL = "regulatory_approval"    # approval / phase
    STRUCTURE_DB = "structure_db"                  # PubChem / structure lookup
    ADVERSE_EVENT = "adverse_event"                # safety signal
    OTHER = "other"


class Direction(str, Enum):
    """Directionality of a drug's action on a target where known."""
    AGONIST = "agonist"
    ANTAGONIST = "antagonist"
    INHIBITOR = "inhibitor"
    ACTIVATOR = "activator"
    MODULATOR = "modulator"
    UNKNOWN = "unknown"


class QualificationStatus(str, Enum):
    """Whether a record passed the pipeline's qualifying filters."""
    QUALIFIED = "qualified"          # meets confidence/species/assay bar
    UNQUALIFIED = "unqualified"      # explicitly failed a bar
    UNKNOWN = "unknown"              # not assessed


class ContradictionStatus(str, Enum):
    """Whether the record is (or is part of) a contradiction."""
    NONE = "none"
    CONTRADICTED = "contradicted"    # another record disagrees
    CONTRADICTS = "contradicts"      # this record disagrees with another


# Sentinel for "quality does not apply to this modality" — distinct from 0.0.
class _NotApplicable:
    _instance: Optional["_NotApplicable"] = None

    def __new__(cls) -> "_NotApplicable":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return "NOT_APPLICABLE"

    def __bool__(self) -> bool:  # never truthy; but is NOT equal to 0.0
        return False


NOT_APPLICABLE = _NotApplicable()


# ---------------------------------------------------------------------------
# Wire contract (TypedDict) — what an adapter emits
# ---------------------------------------------------------------------------

class EvidenceRecordDict(TypedDict, total=False):
    """Normalized evidence record contract shared by every source adapter.

    Every field is optional at the wire level (adapters differ in coverage);
    :func:`normalize_evidence` fills sane defaults and computes the lineage key.
    """
    # Provenance
    provider: str                # which adapter produced this (chembl, ot, ...)
    source_type: str             # SourceType value
    source_id: str               # provider-native record id
    source_version: str          # provider data/release version
    lineage_id: str              # OPTIONAL explicit lineage override
    evidence_role: str           # EvidenceRole value
    # Molecule identity
    molecule_id: str             # provider-native molecule id (e.g. CHEMBL25)
    molecule_name: str
    inchikey: str
    smiles: str
    # Target
    target_symbol: str
    target_accession: str        # e.g. UniProt accession
    target_species: str
    # Action / direction
    action: str
    direction: str               # Direction value
    # Measurement
    measurement_type: str        # e.g. "pchembl", "ic50", "phase"
    measurement_value: float
    measurement_unit: str
    # Assay / context
    assay_id: str
    context: str
    # Publication / label / trial
    publication_id: str          # e.g. PMID
    label_id: str                # e.g. openFDA set id
    trial_id: str                # e.g. NCT id
    # Disease / phenotype
    disease_id: str
    disease_name: str
    phenotype: str
    # Status
    qualification_status: str    # QualificationStatus value
    contradiction_status: str    # ContradictionStatus value


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
# 14-char connectivity block of a standard InChIKey (AAAAAAAAAAAAAA-...-N).
_INCHIKEY_RE = re.compile(r"^([A-Z]{14})-([A-Z]{8,10})-([A-Z0-9])$")


def normalize_name(name: Optional[str]) -> str:
    """Case/space-fold a drug or molecule name for stable comparison.

    Salt/ester WORDS are intentionally NOT stripped here: name-based identity
    is a last resort and must not silently merge "drug X" with "drug X sodium"
    when no structure is available.  Structural collapse is the job of the
    InChIKey block, not of name munging.
    """
    if not name:
        return ""
    return _WS_RE.sub(" ", str(name).strip().lower())


def inchikey_block(inchikey: Optional[str]) -> Optional[str]:
    """Return the 14-char connectivity (active-moiety) block of an InChIKey.

    The first block encodes the molecular skeleton and is identical for salt,
    ester, hydrate and stereo variants of one active moiety, so it is the right
    key for active-moiety union.  Returns None for missing/malformed keys
    (so callers never key on garbage).
    """
    if not inchikey:
        return None
    key = str(inchikey).strip().upper()
    m = _INCHIKEY_RE.match(key)
    if m:
        return m.group(1)
    # Tolerate a bare 14-char block if that is all the adapter had.
    if re.fullmatch(r"[A-Z]{14}", key):
        return key
    return None


def _s(value: Any) -> str:
    return "" if value is None else str(value).strip()


# ---------------------------------------------------------------------------
# EvidenceRecord dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceRecord:
    """A single, normalized piece of evidence with a deterministic lineage key."""
    provider: str = ""
    source_type: SourceType = SourceType.OTHER
    source_id: str = ""
    source_version: str = ""
    evidence_role: EvidenceRole = EvidenceRole.OTHER

    molecule_id: str = ""
    molecule_name: str = ""
    inchikey: str = ""
    smiles: str = ""

    target_symbol: str = ""
    target_accession: str = ""
    target_species: str = ""

    action: str = ""
    direction: Direction = Direction.UNKNOWN

    measurement_type: str = ""
    measurement_value: Optional[float] = None
    measurement_unit: str = ""

    assay_id: str = ""
    context: str = ""

    publication_id: str = ""
    label_id: str = ""
    trial_id: str = ""

    disease_id: str = ""
    disease_name: str = ""
    phenotype: str = ""

    qualification_status: QualificationStatus = QualificationStatus.UNKNOWN
    contradiction_status: ContradictionStatus = ContradictionStatus.NONE

    # Explicit override; if empty, computed by :meth:`lineage_key`.
    lineage_id: str = ""

    def lineage_key(self) -> str:
        """Deterministic key identifying the UNDERLYING evidence artifact.

        The key deliberately EXCLUDES ``provider`` and ``source_version`` so
        that the same assay/publication/label/trial cited by multiple providers
        collapses to a single piece of evidence.  Priority order:

          1. Explicit ``lineage_id`` (adapter told us).
          2. Trial id (NCT) — a trial is a trial regardless of who indexed it.
          3. Label id — a regulatory label is one artifact.
          4. Assay id (+ target + measurement) — one assay is one datum.
          5. Publication id (+ target) — one paper's claim about a target.
          6. Fallback: a structural/semantic tuple of the record so that two
             genuinely identical facts still merge, but distinct facts do not.
        """
        if self.lineage_id:
            return f"explicit:{self.lineage_id.strip().lower()}"
        if self.trial_id:
            return f"trial:{_s(self.trial_id).lower()}"
        if self.label_id:
            return f"label:{_s(self.label_id).lower()}"
        if self.assay_id:
            return (
                f"assay:{_s(self.assay_id).lower()}"
                f"|t={_s(self.target_accession).lower() or _s(self.target_symbol).lower()}"
                f"|m={_s(self.measurement_type).lower()}"
            )
        if self.publication_id:
            return (
                f"pub:{_s(self.publication_id).lower()}"
                f"|t={_s(self.target_accession).lower() or _s(self.target_symbol).lower()}"
                f"|role={self.evidence_role.value}"
            )
        # Last-resort semantic identity of the datum itself.
        block = inchikey_block(self.inchikey) or normalize_name(self.molecule_name)
        return (
            "fact:"
            f"mol={block}"
            f"|t={_s(self.target_accession).lower() or _s(self.target_symbol).lower()}"
            f"|role={self.evidence_role.value}"
            f"|st={self.source_type.value}"
            f"|mt={_s(self.measurement_type).lower()}"
            f"|dis={_s(self.disease_id).lower() or normalize_name(self.disease_name)}"
        )


def _coerce_enum(enum_cls, value, default):
    if value is None or value == "":
        return default
    if isinstance(value, enum_cls):
        return value
    try:
        return enum_cls(str(value).strip().lower())
    except ValueError:
        return default


def _coerce_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def normalize_evidence(raw: Any) -> EvidenceRecord:
    """Turn an adapter dict (or an existing EvidenceRecord) into a record.

    Missing fields become well-typed defaults; enums are coerced leniently.
    """
    if isinstance(raw, EvidenceRecord):
        return raw
    d: dict[str, Any] = dict(raw or {})
    return EvidenceRecord(
        provider=_s(d.get("provider")),
        source_type=_coerce_enum(SourceType, d.get("source_type"), SourceType.OTHER),
        source_id=_s(d.get("source_id")),
        source_version=_s(d.get("source_version")),
        evidence_role=_coerce_enum(EvidenceRole, d.get("evidence_role"), EvidenceRole.OTHER),
        molecule_id=_s(d.get("molecule_id")),
        molecule_name=_s(d.get("molecule_name")),
        inchikey=_s(d.get("inchikey")).upper(),
        smiles=_s(d.get("smiles")),
        target_symbol=_s(d.get("target_symbol")),
        target_accession=_s(d.get("target_accession")),
        target_species=_s(d.get("target_species")),
        action=_s(d.get("action")),
        direction=_coerce_enum(Direction, d.get("direction"), Direction.UNKNOWN),
        measurement_type=_s(d.get("measurement_type")),
        measurement_value=_coerce_float(d.get("measurement_value")),
        measurement_unit=_s(d.get("measurement_unit")),
        assay_id=_s(d.get("assay_id")),
        context=_s(d.get("context")),
        publication_id=_s(d.get("publication_id")),
        label_id=_s(d.get("label_id")),
        trial_id=_s(d.get("trial_id")),
        disease_id=_s(d.get("disease_id")),
        disease_name=_s(d.get("disease_name")),
        phenotype=_s(d.get("phenotype")),
        qualification_status=_coerce_enum(
            QualificationStatus, d.get("qualification_status"), QualificationStatus.UNKNOWN),
        contradiction_status=_coerce_enum(
            ContradictionStatus, d.get("contradiction_status"), ContradictionStatus.NONE),
        lineage_id=_s(d.get("lineage_id")),
    )


# ---------------------------------------------------------------------------
# Evidence quality calibration (per modality; no pChEMBL requirement)
# ---------------------------------------------------------------------------

# Base quality per source modality on a calibrated 0..1 scale.  These reflect
# how directly a modality supports a repurposing claim; they are intentionally
# INDEPENDENT of any numeric affinity so that non-assay modalities (genetic,
# mechanism, label, trial) are not scored as zero just because there is no
# pChEMBL value.
_MODALITY_BASE_QUALITY: dict[SourceType, float] = {
    SourceType.CLINICAL_TRIAL:       0.90,
    SourceType.MECHANISM:            0.85,
    SourceType.REGULATORY_APPROVAL:  0.85,
    SourceType.DRUG_LABEL:           0.80,
    SourceType.GENETIC_ASSOCIATION:  0.75,
    SourceType.BIOACTIVITY_ASSAY:    0.70,
    SourceType.PUBLICATION:          0.55,
    SourceType.PATHWAY:              0.45,
    SourceType.STRUCTURE_DB:         0.40,
    SourceType.ADVERSE_EVENT:        0.70,
    SourceType.OTHER:                0.30,
}

# Modalities that can carry an efficacy signal vs a safety signal.  A modality
# absent from BOTH sets returns NOT_APPLICABLE for that dimension.
_EFFICACY_MODALITIES = {
    SourceType.CLINICAL_TRIAL, SourceType.MECHANISM,
    SourceType.GENETIC_ASSOCIATION, SourceType.BIOACTIVITY_ASSAY,
    SourceType.PUBLICATION, SourceType.PATHWAY,
}
_SAFETY_MODALITIES = {
    SourceType.ADVERSE_EVENT, SourceType.DRUG_LABEL, SourceType.REGULATORY_APPROVAL,
}


def evidence_quality(record: EvidenceRecord):
    """Calibrated 0..1 quality for ONE record, or NOT_APPLICABLE.

    A bioactivity assay with a real pChEMBL value gets a small, bounded lift
    proportional to potency, but the base score does NOT require a pChEMBL
    value.  A record whose modality is unknown/other still gets its base
    quality; only genuinely non-scorable input returns NOT_APPLICABLE.
    """
    base = _MODALITY_BASE_QUALITY.get(record.source_type)
    if base is None:
        return NOT_APPLICABLE
    quality = base
    # Optional potency lift for real quantitative affinity (pChEMBL ~ 4..10).
    if (record.source_type == SourceType.BIOACTIVITY_ASSAY
            and record.measurement_type.lower() in ("pchembl", "pchembl_value")
            and record.measurement_value is not None):
        # Map pChEMBL 5..9 -> 0..0.25 lift, clamped.
        lift = max(0.0, min(0.25, (record.measurement_value - 5.0) / 16.0))
        quality = min(1.0, base + lift)
    # Unqualified records are penalised but never dropped to a false 0.
    if record.qualification_status == QualificationStatus.UNQUALIFIED:
        quality *= 0.5
    # Contradicted records lose confidence.
    if record.contradiction_status == ContradictionStatus.CONTRADICTED:
        quality *= 0.6
    return round(min(1.0, max(0.0, quality)), 4)


def _dimension_quality(records: Iterable[EvidenceRecord], modalities: set):
    """Best calibrated quality across records whose modality is in ``modalities``.

    Returns NOT_APPLICABLE (distinct from 0.0) when no record contributes to
    this dimension, so "we have no safety evidence" is never confused with
    "safety quality is zero".
    """
    scores = []
    for r in records:
        if r.source_type in modalities:
            q = evidence_quality(r)
            if q is not NOT_APPLICABLE:
                scores.append(q)
    if not scores:
        return NOT_APPLICABLE
    return round(max(scores), 4)


def efficacy_confidence(records: Iterable[EvidenceRecord]):
    """Efficacy-only calibrated confidence (0..1) or NOT_APPLICABLE."""
    return _dimension_quality(records, _EFFICACY_MODALITIES)


def safety_confidence(records: Iterable[EvidenceRecord]):
    """Safety-only calibrated confidence (0..1) or NOT_APPLICABLE.

    Kept explicitly SEPARATE from efficacy — the two are never averaged.
    """
    return _dimension_quality(records, _SAFETY_MODALITIES)


# ---------------------------------------------------------------------------
# Candidate identity
# ---------------------------------------------------------------------------

def candidate_identity(record: EvidenceRecord) -> str:
    """Deterministic identity key for the active moiety a record concerns.

    Priority:
      1. InChIKey connectivity block (active-moiety / structural identity) —
         collapses salt/ester/hydrate forms of one moiety.
      2. A stable provider molecule ID (namespaced by provider so two
         providers' opaque ids never accidentally collide).
      3. Normalized name (LAST resort; only when neither structure nor a
         provider id is available).  Because this is last, two records with
         different structures never merge on name, and a record WITH structure
         is never merged into a name-only bucket.
    """
    block = inchikey_block(record.inchikey)
    if block:
        return f"moiety:{block}"
    if record.molecule_id:
        prov = record.provider or "?"
        return f"molid:{prov.lower()}:{record.molecule_id.strip().lower()}"
    name = normalize_name(record.molecule_name)
    if name:
        return f"name:{name}"
    # No identity at all — key on source id so we never merge unrelated blanks.
    return f"anon:{record.provider.lower()}:{record.source_id.lower()}"


# ---------------------------------------------------------------------------
# Candidate union
# ---------------------------------------------------------------------------

@dataclass
class MergedCandidate:
    """Identity-keyed union of every record concerning one active moiety."""
    identity: str
    records: list[EvidenceRecord] = field(default_factory=list)
    # Preserved memberships / provenance
    target_symbols: set = field(default_factory=set)
    target_accessions: set = field(default_factory=set)
    source_types: set = field(default_factory=set)
    providers: set = field(default_factory=set)
    disease_names: set = field(default_factory=set)
    # Aggregates
    max_phase: Optional[float] = None
    best_affinity: Optional[float] = None       # highest pChEMBL seen
    assay_confidence_score: Optional[int] = None
    contradictions: list = field(default_factory=list)
    # Explicit per-source health: provider -> True (any qualified record) or
    # False (present but every record unqualified / failed).
    source_health: dict = field(default_factory=dict)
    # Best identity fields observed (for downstream Chemist compatibility).
    drug_name: str = ""
    inchikey: str = ""
    smiles: str = ""
    molecule_chembl_id: Optional[str] = None
    uniprot_id: Optional[str] = None
    disease_name: str = ""
    ot_association_score: Optional[float] = None
    target_discovery_method: str = ""

    def _num_lineages(self) -> int:
        return len({r.lineage_key() for r in self.records})

    def to_chemist_candidate(self) -> dict[str, Any]:
        """Return a dict byte-compatible with agents/chemist.py output.

        Preserves every field the Chemist emits, PLUS the ledger's extra
        provenance under ``_evidence_ledger`` so nothing is lost while
        remaining a strict superset of the Chemist contract.
        """
        eff = efficacy_confidence(self.records)
        saf = safety_confidence(self.records)
        evidence_records = [
            {
                "provider": r.provider,
                "source_type": r.source_type.value,
                "source_id": r.source_id,
                "source_version": r.source_version,
                "lineage_id": r.lineage_key(),
                "evidence_role": r.evidence_role.value,
                "molecule_id": r.molecule_id,
                "molecule_name": r.molecule_name,
                "inchikey": r.inchikey,
                "smiles": r.smiles,
                "target_symbol": r.target_symbol,
                "target_accession": r.target_accession,
                "target_species": r.target_species,
                "action": r.action,
                "direction": r.direction.value,
                "measurement_type": r.measurement_type,
                "measurement_value": r.measurement_value,
                "measurement_unit": r.measurement_unit,
                "assay_id": r.assay_id,
                "context": r.context,
                "publication_id": r.publication_id,
                "label_id": r.label_id,
                "trial_id": r.trial_id,
                "disease_id": r.disease_id,
                "disease_name": r.disease_name,
                "phenotype": r.phenotype,
                "qualification_status": r.qualification_status.value,
                "contradiction_status": r.contradiction_status.value,
            }
            for r in sorted(self.records, key=lambda item: item.lineage_key())
        ]
        return {
            # --- fields the Chemist output already carries ---
            "drug_name": self.drug_name,
            "molecule_chembl_id": self.molecule_chembl_id,
            "smiles": self.smiles,
            "inchikey": self.inchikey,
            "pchembl_value": self.best_affinity,
            # Preserve the legacy ChEMBL assay-confidence scale (0-9).  The
            # cross-modality evidence confidence is a separate 0-1 field.
            "confidence_score": self.assay_confidence_score,
            "efficacy_confidence": (
                None if eff is NOT_APPLICABLE else float(eff)
            ),
            "max_phase": self.max_phase,
            "is_approved_drug": (self.max_phase is not None and self.max_phase >= 4),
            "source_chembl_ids": sorted(
                {r.molecule_id for r in self.records if r.molecule_id}),
            "source_activity_ids": sorted(
                {r.assay_id for r in self.records if r.assay_id}),
            "target_symbol": (sorted(self.target_symbols)[0]
                              if self.target_symbols else ""),
            "uniprot_id": self.uniprot_id,
            "target_discovery_method": self.target_discovery_method,
            "disease_name": self.disease_name,
            "ot_association_score": self.ot_association_score,
            "source_types": sorted(st.value for st in self.source_types),
            "source_health": dict(sorted(self.source_health.items())),
            "target_memberships": [
                {"target_symbol": symbol}
                for symbol in sorted(self.target_symbols)
            ] + [
                {"uniprot_id": accession}
                for accession in sorted(self.target_accessions)
            ],
            # --- ledger extras (superset; safe to ignore downstream) ---
            "_evidence_ledger": {
                "identity": self.identity,
                "efficacy_confidence": (None if eff is NOT_APPLICABLE else eff),
                "safety_confidence": (None if saf is NOT_APPLICABLE else saf),
                "efficacy_not_applicable": eff is NOT_APPLICABLE,
                "safety_not_applicable": saf is NOT_APPLICABLE,
                "target_symbols": sorted(self.target_symbols),
                "target_accessions": sorted(self.target_accessions),
                "source_types": sorted(st.value for st in self.source_types),
                "providers": sorted(self.providers),
                "disease_names": sorted(self.disease_names),
                "distinct_evidence_count": self._num_lineages(),
                "record_count": len(self.records),
                "contradiction_count": len(self.contradictions),
                "source_health": dict(sorted(self.source_health.items())),
                "records": evidence_records,
            },
        }


def merge_candidates(candidates: Iterable[Any]) -> list[dict[str, Any]]:
    """Identity-keyed union of raw evidence records into Chemist candidates.

    ``candidates`` is an iterable of EvidenceRecord objects OR adapter dicts.
    Records are grouped by :func:`candidate_identity`; within each group
    duplicate evidence is collapsed by lineage key (NOT by provider), so that
    the same assay/publication/label/trial reported by several providers counts
    once.  The result is a deterministically-ordered list of Chemist-compatible
    dicts, one per active moiety.
    """
    records = [normalize_evidence(c) for c in candidates]
    groups: dict[str, MergedCandidate] = {}

    for rec in records:
        ident = candidate_identity(rec)
        mc = groups.get(ident)
        if mc is None:
            mc = MergedCandidate(identity=ident)
            groups[ident] = mc
        _absorb(mc, rec)

    # Deterministic order: by identity key.
    ordered = [groups[k] for k in sorted(groups.keys())]
    return [mc.to_chemist_candidate() for mc in ordered]


def _absorb(mc: MergedCandidate, rec: EvidenceRecord) -> None:
    """Fold a single record into a MergedCandidate, deduping by lineage."""
    # Deduplicate identical underlying evidence across providers by lineage.
    key = rec.lineage_key()
    existing = {r.lineage_key(): r for r in mc.records}
    if key in existing:
        # Keep the record that is qualified over an unqualified duplicate;
        # otherwise keep the first (stable).  Provider count never grows here.
        prev = existing[key]
        if (prev.qualification_status != QualificationStatus.QUALIFIED
                and rec.qualification_status == QualificationStatus.QUALIFIED):
            mc.records = [rec if r is prev else r for r in mc.records]
        # Still record provider participation / health even when deduped.
    else:
        mc.records.append(rec)

    # Memberships (order-independent sets).
    if rec.target_symbol:
        mc.target_symbols.add(rec.target_symbol)
    if rec.target_accession:
        mc.target_accessions.add(rec.target_accession)
    mc.source_types.add(rec.source_type)
    if rec.provider:
        mc.providers.add(rec.provider)
    if rec.disease_name:
        mc.disease_names.add(rec.disease_name)

    # Contradictions preserved explicitly.
    if rec.contradiction_status in (ContradictionStatus.CONTRADICTED,
                                    ContradictionStatus.CONTRADICTS):
        mc.contradictions.append({
            "provider": rec.provider,
            "source_id": rec.source_id,
            "status": rec.contradiction_status.value,
            "target_symbol": rec.target_symbol,
            "role": rec.evidence_role.value,
        })

    # Per-source health: qualified anywhere -> healthy; else present-but-degraded.
    prov = rec.provider or "?"
    healthy = rec.qualification_status == QualificationStatus.QUALIFIED
    mc.source_health[prov] = mc.source_health.get(prov, False) or healthy

    # Max approval phase (regulatory / approval measurement).
    if (rec.measurement_type.lower() in ("phase", "max_phase")
            and rec.measurement_value is not None):
        mc.max_phase = (rec.measurement_value if mc.max_phase is None
                        else max(mc.max_phase, rec.measurement_value))

    # Best quantitative affinity (highest pChEMBL).
    if (rec.measurement_type.lower() in ("pchembl", "pchembl_value")
            and rec.measurement_value is not None):
        mc.best_affinity = (rec.measurement_value if mc.best_affinity is None
                            else max(mc.best_affinity, rec.measurement_value))

    if (rec.measurement_type.lower() == "assay_confidence"
            and rec.measurement_value is not None):
        score = int(round(rec.measurement_value))
        mc.assay_confidence_score = (
            score if mc.assay_confidence_score is None
            else max(mc.assay_confidence_score, score)
        )

    # Prefer the most complete identity fields (never overwrite good with empty).
    if not mc.inchikey and rec.inchikey:
        mc.inchikey = rec.inchikey
    if not mc.smiles and rec.smiles:
        mc.smiles = rec.smiles
    if not mc.drug_name and rec.molecule_name:
        mc.drug_name = rec.molecule_name
    # Prefer a ChEMBL-looking id for molecule_chembl_id.
    if rec.molecule_id and (mc.molecule_chembl_id is None
                            or (not str(mc.molecule_chembl_id).upper().startswith("CHEMBL")
                                and rec.molecule_id.upper().startswith("CHEMBL"))):
        mc.molecule_chembl_id = rec.molecule_id
    if not mc.uniprot_id and rec.target_accession:
        mc.uniprot_id = rec.target_accession
    if not mc.disease_name and rec.disease_name:
        mc.disease_name = rec.disease_name
    if not mc.target_discovery_method and rec.evidence_role == EvidenceRole.TARGET_LINK:
        # Map source type to a discovery-method label the Writer understands.
        mc.target_discovery_method = {
            SourceType.GENETIC_ASSOCIATION: "genetic_association",
            SourceType.MECHANISM: "pharmacological_precedent",
            SourceType.PATHWAY: "pathway_neighbor",
        }.get(rec.source_type, mc.target_discovery_method)
    # ot_association_score from a genetic disease link measurement.
    if (rec.source_type == SourceType.GENETIC_ASSOCIATION
            and rec.measurement_type.lower() in ("ot_association", "association_score")
            and rec.measurement_value is not None):
        mc.ot_association_score = (
            rec.measurement_value if mc.ot_association_score is None
            else max(mc.ot_association_score, rec.measurement_value))

    # Fallback drug_name if still empty.
    if not mc.drug_name:
        mc.drug_name = rec.molecule_id or rec.molecule_name or mc.identity


__all__ = [
    "EvidenceRole", "SourceType", "Direction",
    "QualificationStatus", "ContradictionStatus",
    "NOT_APPLICABLE", "EvidenceRecordDict", "EvidenceRecord",
    "MergedCandidate",
    "normalize_name", "inchikey_block", "normalize_evidence",
    "evidence_quality", "efficacy_confidence", "safety_confidence",
    "candidate_identity", "merge_candidates",
]
