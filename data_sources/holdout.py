"""Benchmark holdout (leave-one-out) state for retrospective validation.

When active, discovery-side data sources redact the held-out drug(s) so a
retrospective benchmark measures genuine de-novo discovery instead of
precedent leakage (the pipeline "finding" a target only because ChEMBL/Open
Targets already record the drug as approved for the disease).

Deliberate scope:
- SEALED (disease-side leakage): approved-drug name lists from Open Targets
  (specific + parent-umbrella EFOs), the ChEMBL drug_indication EFO fallback,
  and the has_approved/unmet-need signal those lists feed.
- NOT sealed (legitimate discovery): the target's ChEMBL bioactivity
  candidate pool. A drug surfacing in an honestly-selected target's
  IC50/Ki pool IS the rediscovery moment the benchmark exists to measure.
  LLM parametric world knowledge is likewise out of scope (a human expert
  would have it too).

State is process-global and deliberately tiny; the validation harnesses
activate it around each case. ThreadPoolExecutor-based top-K pursuit shares
the same process state, which is the correct per-case semantics.
"""

from contextlib import contextmanager
from typing import Any

_DRUGS: list[str] = []
_MOL_IDS: set[str] = set()
_PARENTS: set[str] = set()
_UNRESOLVED: list[str] = []
_RESOLVED: bool = False


def _norm(s: Any) -> str:
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def is_active() -> bool:
    return bool(_DRUGS)


def drugs() -> list[str]:
    return list(_DRUGS)


def unresolved() -> list[str]:
    """Held-out names that could not be resolved to ChEMBL molecules.

    Name-level redaction still applies to these; molecule-level (salt-form)
    redaction does not. Recorded on the benchmark result for transparency.
    """
    return list(_UNRESOLVED)


def ids_resolved() -> bool:
    return _RESOLVED


def activate(drug_names: list[str]) -> None:
    global _DRUGS, _RESOLVED
    _DRUGS = [d for d in (drug_names or []) if d]
    _MOL_IDS.clear()
    _PARENTS.clear()
    _UNRESOLVED.clear()
    _RESOLVED = False


def deactivate() -> None:
    global _DRUGS, _RESOLVED
    _DRUGS = []
    _MOL_IDS.clear()
    _PARENTS.clear()
    _UNRESOLVED.clear()
    _RESOLVED = False


@contextmanager
def holdout_active(drug_names: list[str]):
    activate(drug_names)
    try:
        yield
    finally:
        deactivate()


def mark_resolved() -> None:
    global _RESOLVED
    _RESOLVED = True


def mark_unresolved(name: str) -> None:
    if name not in _UNRESOLVED:
        _UNRESOLVED.append(name)


def register_molecules(mol_ids: set[str], parents: set[str]) -> None:
    _MOL_IDS.update(m for m in mol_ids if m)
    _PARENTS.update(p for p in parents if p)


def matches_name(name: str) -> bool:
    """Exact normalized-name match (catches OT's uppercase INN spellings).

    Salt/ester variants (e.g. 'SILDENAFIL CITRATE' vs holdout 'sildenafil')
    intentionally do NOT match here — those are caught at the molecule layer
    via shared parent ChEMBL ID.
    """
    n = _norm(name)
    return bool(n) and any(n == _norm(d) for d in _DRUGS)


def matches_molecule(mol_id: str | None, parent_id: str | None = None) -> bool:
    return bool(
        (mol_id and mol_id in _MOL_IDS)
        or (parent_id and parent_id in _PARENTS)
    )
