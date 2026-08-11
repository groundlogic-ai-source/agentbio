"""Disease-blind redaction for the audit evidence lanes under holdout.

`data_sources/holdout.py` seals disease-side evidence on the DISCOVERY
side: Open Targets approved-drug name lists, the ChEMBL
``drug_indication`` EFO fallback, and the ``has_approved``/unmet-need
signal.  The audit layer is a separate code path over separate sources,
and neither audit source module consults holdout at all.

That gap is not theoretical.  Probing the production audit context for
five confirmed repurposings with the drug held out
(``validation/audit_lane_holdout_probe.py``) recovered the drug's own
approved indication in 5/5 cases — and every hit landed in exactly one
surface, the free-text label quote:

    lane                    leaking quotes / total
    clinical_pharmacology            62 / 190
    indications_and_usage            60 / 188
    mechanism_of_action              55 / 136
    dosage_and_administration        25 / 213
    description                       0 / 188

The structured regulatory fields the deterministic detectors actually
consume — routes, dosage forms, product modality, combination status,
active ingredients — leaked nothing.  So the audit layer can be made
disease-blind by dropping the narrative surfaces while keeping the
structured ones, at a bounded and disclosed cost.

Design constraints
------------------
* **Holdout-only.**  Production audit output is unchanged; redaction is
  applied only while a holdout is active.
* **Post-cache.**  This runs at the lane boundary in
  ``build_audit_context``, never inside the source modules.  Redacting
  before ``cache_set`` would write holdout-shaped payloads into the
  shared label cache and corrupt ordinary production runs.
* **Allowlist, not denylist.**  Records are rebuilt from an explicit set
  of structured keys, so a field added to a source later is dropped by
  default rather than silently leaking.  This is the fail-closed
  direction: over-redaction weakens the instrument visibly, while
  under-redaction invalidates the study silently.
* **Disclosed, not silent.**  Every redacted payload carries a marker
  recording what was removed, so a run can prove it was blind rather
  than assert it.
"""
from __future__ import annotations

from typing import Any

from data_sources import holdout

REDACTION_CONTRACT = "audit-holdout-redaction-v1"

# Structured, disease-blind keys retained on a label product record.
# `evidence` is deliberately absent: it is the sole leaking surface.
_PRODUCT_KEEP = (
    "identity", "regulatory", "spl", "citation_eligible", "source_url")

# Structured keys retained on a literature assertion.  `title`,
# `evidence_sentence`, `experimental_context` and `relation_span` are
# dropped: all four are free text drawn from abstracts that routinely
# name the indication.
_ASSERTION_KEEP = (
    "source_row_id", "pmid", "pmcid", "doi", "journal", "publication_types",
    "publication_date", "citation_eligible", "drug_entity",
    "mechanism_entity", "species", "organism", "experimental_setting",
    "relation", "action", "direction", "evidence_location",
    "primary_experiment", "publication_type_status", "source", "lineage_id",
)

_REDACTED = "[redacted: disease-side holdout active]"


def _pick(record: dict[str, Any], keep: tuple[str, ...]) -> dict[str, Any]:
    return {key: record[key] for key in keep if key in record}


def redact_label_lane(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop every free-text label quote, keeping structured regulatory facts.

    The detectors read `regulatory.combination` (N1),
    `regulatory.product_modality` (N2) and `regulatory.routes` (N4) from
    the structured sub-object, so N1/N2/N4 route logic is unaffected.
    The N4 *dose* comparison reads `dosage_and_administration` quotes and
    therefore degrades from `review` to `unresolved` under holdout; that
    is a disclosed loss of an unused signal, not a silent one.
    """
    if not isinstance(payload, dict):
        return payload
    products = []
    dropped_quotes = 0
    dropped_fields: set[str] = set()
    for product in payload.get("products") or []:
        if not isinstance(product, dict):
            continue
        for evidence in product.get("evidence") or []:
            if isinstance(evidence, dict):
                dropped_quotes += 1
                dropped_fields.add(str(evidence.get("field") or "unknown"))
        kept = _pick(product, _PRODUCT_KEEP)
        kept["evidence"] = []
        products.append(kept)
    out = dict(payload)
    out["products"] = products
    out["holdout_redaction"] = {
        "contract": REDACTION_CONTRACT,
        "applied": True,
        "surface": "products[].evidence[].quote",
        "dropped_quote_count": dropped_quotes,
        "dropped_fields": sorted(dropped_fields),
        "retained": "structured regulatory, identity, and SPL provenance",
    }
    return out


def redact_literature_lane(payload: dict[str, Any]) -> dict[str, Any]:
    """Blank free-text assertion surfaces, keeping structured context.

    N3 reads `experimental_setting` and `citation_eligible`, both of
    which are retained, so N3's verdict logic is unaffected.  The
    human-readable sentence it attaches as evidence becomes a redaction
    marker.
    """
    if not isinstance(payload, dict):
        return payload
    assertions = []
    dropped = 0
    for assertion in payload.get("assertions") or []:
        if not isinstance(assertion, dict):
            continue
        kept = _pick(assertion, _ASSERTION_KEEP)
        kept["title"] = _REDACTED
        kept["evidence_sentence"] = _REDACTED
        kept["experimental_context"] = _REDACTED
        dropped += 1
        assertions.append(kept)
    out = dict(payload)
    out["assertions"] = assertions
    out["holdout_redaction"] = {
        "contract": REDACTION_CONTRACT,
        "applied": True,
        "surface": "assertions[].{title,evidence_sentence,"
                   "experimental_context,relation_span}",
        "redacted_assertion_count": dropped,
        "retained": "pmid/doi provenance, species, experimental setting, "
                    "direction, citation eligibility",
    }
    return out


def redact_audit_lanes(
    regulatory: dict[str, Any],
    literature: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Apply disease-blind redaction when — and only when — holdout is active.

    Returns the two lane payloads plus a disclosure record describing
    whether redaction ran, suitable for embedding in the audit context so
    downstream consumers can verify blindness instead of trusting it.
    """
    if not holdout.is_active():
        return regulatory, literature, {
            "contract": REDACTION_CONTRACT,
            "applied": False,
            "reason": "no disease-side holdout active (production path)",
        }
    return (
        redact_label_lane(regulatory),
        redact_literature_lane(literature),
        {
            "contract": REDACTION_CONTRACT,
            "applied": True,
            "reason": "disease-side holdout active",
            "held_out_drugs": holdout.drugs(),
            "note": (
                "Free-text label quotes and literature sentences are removed "
                "because they state the drug's approved indication verbatim. "
                "Structured regulatory fields are retained; the N4 dose "
                "comparison is unavailable under redaction."
            ),
        },
    )
