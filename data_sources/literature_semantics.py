"""Mechanical vocabulary and identity checks for literature screening.

This module deliberately contains no scoring logic.  Related evidence is a
disclosure surface and must never be interpreted as candidate efficacy.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable


EXPLICIT_LIMITATION = "EXPLICIT_LIMITATION"
APPLICABLE_SUPPORT = "APPLICABLE_SUPPORT"
NOT_APPLICABLE_TO_EXACT_DRUG_USE = "NOT_APPLICABLE_TO_EXACT_DRUG_USE"
UNKNOWN_INTEGRITY_FAILED = "UNKNOWN/INTEGRITY_FAILED"

EXACT_DRUG = "EXACT_DRUG"
DRUG_ALIAS = "DRUG_ALIAS"
DRUG_CLASS = "DRUG_CLASS"
NO_INTERVENTION_MATCH = "NO_INTERVENTION_MATCH"

EVIDENCE_LEVELS = {"mechanistic", "disease_model", "case_report_clinical"}
EXACT_USE_LABELS = {
    EXPLICIT_LIMITATION,
    APPLICABLE_SUPPORT,
    NOT_APPLICABLE_TO_EXACT_DRUG_USE,
    UNKNOWN_INTEGRITY_FAILED,
}
INTERVENTION_IDENTITIES = {
    EXACT_DRUG, DRUG_ALIAS, DRUG_CLASS, NO_INTERVENTION_MATCH,
}


def normalized_phrase(value: Any) -> str:
    folded = unicodedata.normalize("NFKD", str(value or "")).encode(
        "ascii", "ignore"
    ).decode("ascii")
    return " ".join(re.findall(r"[A-Za-z0-9]+", folded)).casefold()


def phrase_present(text: Any, value: Any) -> bool:
    phrase = normalized_phrase(value)
    haystack = normalized_phrase(text)
    return bool(phrase and phrase in haystack)


def intervention_identity(
    text: Any,
    drug_name: str,
    aliases: Iterable[str] = (),
    drug_class: str = "",
) -> str:
    """Return the strongest intervention identity directly present in source text."""
    if phrase_present(text, drug_name):
        return EXACT_DRUG
    if any(phrase_present(text, alias) for alias in aliases if alias):
        return DRUG_ALIAS
    class_names = [
        part.strip()
        for part in re.split(r"\s*/\s*|\s*;\s*", drug_class)
        if normalized_phrase(part) not in {
            "", "activator", "agonist", "antagonist", "blocker", "inhibitor",
            "modulator",
        }
    ]
    source_terms = set(normalized_phrase(text).split())
    class_match = False
    for class_name in class_names:
        if phrase_present(text, class_name):
            class_match = True
            break
        wanted = set(normalized_phrase(class_name).split())
        # Permit small morphology differences such as inhibit/inhibitor and
        # channel/channels, while requiring multiple class-specific terms.
        overlap = sum(
            any(
                source == term
                or (
                    len(source) >= 5 and len(term) >= 5
                    and source[:5] == term[:5]
                )
                for source in source_terms
            )
            for term in wanted
        )
        if wanted and overlap >= 2 and overlap / len(wanted) >= 0.6:
            class_match = True
            break
    if class_match:
        return DRUG_CLASS
    return NO_INTERVENTION_MATCH


def canonical_exact_use_label(value: Any) -> str:
    """Canonicalize v2 labels while keeping IRRELEVANT as input-only legacy."""
    label = str(value or "").strip().upper()
    return {
        "SUPPORT": APPLICABLE_SUPPORT,
        "IRRELEVANT": NOT_APPLICABLE_TO_EXACT_DRUG_USE,
        "CAUTION": NOT_APPLICABLE_TO_EXACT_DRUG_USE,
        "UNKNOWN_INTEGRITY_FAILED": UNKNOWN_INTEGRITY_FAILED,
    }.get(label, label)


def legacy_projection(label: str) -> str:
    """Project canonical semantics for old report consumers only."""
    return {
        APPLICABLE_SUPPORT: "SUPPORT",
        NOT_APPLICABLE_TO_EXACT_DRUG_USE: "IRRELEVANT",
        UNKNOWN_INTEGRITY_FAILED: "INVALID",
    }.get(label, label)