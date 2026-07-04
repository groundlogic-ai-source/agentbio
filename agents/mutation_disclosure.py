"""
Mutation-specificity DISCLOSURE flag (Stage 2/3).

PURPOSE (read this before touching the regexes):
This module answers ONE narrow question — "does the approved / known indication
text for this drug explicitly NAME a specific genetic mutation?" (e.g.
"KRAS G12C-mutated NSCLC", "EGFR exon 19 deletions", "BRAF V600E").

It is a DISCLOSURE flag, NOT a variant-to-target mapping. It does NOT:
  - decide whether the pipeline's target carries that mutation,
  - map the mutation onto the repurposing disease,
  - change any score, rank, or STRONG_MATCH decision.

It exists so a human reviewer is TOLD, in the evidence table and an auto report
caveat, that the drug's precedent is mutation-scoped and therefore may not
transfer to the (unmutated / differently-mutated) repurposing indication. The
reviewer does the actual biological judgement; we only surface the fact.

Primary source of the text is the FDA label 'Indications and Usage' section,
because ChEMBL normalizes its structured indication terms to mutation-stripped
disease names.
"""

import re
from typing import Any

# Canonical protein point-mutation notation: <WT aa><position><mutant aa>,
# e.g. G12C, V600E, T790M, L858R. One-letter amino-acid codes only, 1-4 digit
# position. Bounded by word edges so "H2O2" or "COVID19" do not match.
_PROTEIN_VARIANT = re.compile(
    r"\b[ACDEFGHIKLMNPQRSTVWY]\d{1,4}[ACDEFGHIKLMNPQRSTVWY]\b"
)

# Exon-level lesions named in labels: "exon 19 deletion", "exon 20 insertion",
# "exon 14 skipping", "exon 21 L858R".
_EXON_LESION = re.compile(
    r"\bexon\s?\d{1,2}\b(?:[^.\n]{0,40}?"
    r"(?:deletion|insertion|skipping|mutation|substitution|L858R))?",
    re.IGNORECASE,
)

# "<GENE>-mutated", "<GENE> mutation-positive", "<GENE>-positive", "<GENE> mutant".
# The gene token is an uppercase-led alphanumeric symbol (KRAS, BRAF, EGFR,
# BCR-ABL, PIK3CA, FLT3, IDH1...). Requires the mutation/positive keyword so a
# bare gene name never trips the flag.
_GENE_MUTATION = re.compile(
    r"\b([A-Z][A-Z0-9]{1,6}(?:-[A-Z0-9]{1,6})?)"
    r"[ -]?(?:mutant|mutated|mutation[- ]positive|mutation|positive)\b"
)

# Explicit fusion notation, e.g. "BCR-ABL", "EML4-ALK", "NTRK fusion".
_FUSION = re.compile(
    r"\b[A-Z][A-Z0-9]{1,6}-[A-Z][A-Z0-9]{1,6}\b|\b[A-Z][A-Z0-9]{1,6}\s+fusion\b"
)

# Amino-acid full-name context that, combined with a position, indicates a
# variant even when one-letter codes are not used ("valine to glutamic acid").
_MUTATION_KEYWORD = re.compile(
    r"\b(?:mutation|mutated|mutant|substitution|deletion|insertion|"
    r"amplification|rearrangement|fusion|-positive|variant allele)\b",
    re.IGNORECASE,
)


def detect_mutation_specificity(indications_text: str,
                                extra_terms: list[str] | None = None) -> dict[str, Any]:
    """
    Scan indication text for explicit mutation naming and return a disclosure record.

    Args:
      indications_text: free-text indication section (primary: FDA label).
      extra_terms: additional indication strings (e.g. ChEMBL efo/mesh terms).

    Returns:
      {
        is_mutation_specific: bool,   # a specific mutation is named in the text
        matched_terms: [str],         # the exact substrings that tripped the flag
        source_excerpt: str,          # short quote around the first match (context)
        note: str,                    # fixed disclosure-scope reminder
      }

    A negative result (empty text or no mutation named) returns
    is_mutation_specific=False with empty matches — this is a disclosure flag, so
    "not detected" must never be read as "confirmed mutation-agnostic".
    """
    parts = [indications_text or ""]
    if extra_terms:
        parts.extend(t for t in extra_terms if t)
    text = "  ".join(parts).strip()

    result: dict[str, Any] = {
        "is_mutation_specific": False,
        "matched_terms": [],
        "source_excerpt": "",
        "note": ("DISCLOSURE ONLY: the drug's approved/known indication names a "
                 "specific mutation. This does NOT assert the repurposing target "
                 "carries that mutation and does NOT affect any score."),
    }
    if not text:
        return result

    matches: list[str] = []
    first_span: tuple[int, int] | None = None

    def _collect(pattern: re.Pattern) -> None:
        nonlocal first_span
        for m in pattern.finditer(text):
            frag = m.group(0).strip()
            if frag and frag not in matches:
                matches.append(frag)
                if first_span is None:
                    first_span = m.span()

    # A bare protein-variant token (G12C) is only meaningful near a mutation
    # keyword; otherwise it can be a coincidental letter-number-letter string.
    if _MUTATION_KEYWORD.search(text):
        _collect(_PROTEIN_VARIANT)
    _collect(_GENE_MUTATION)
    _collect(_EXON_LESION)
    _collect(_FUSION)

    if matches:
        result["is_mutation_specific"] = True
        # De-dup while preserving order; cap list so the record stays compact.
        result["matched_terms"] = matches[:10]
        if first_span:
            s = max(0, first_span[0] - 60)
            e = min(len(text), first_span[1] + 60)
            excerpt = text[s:e].strip()
            result["source_excerpt"] = ("…" + excerpt + "…") if excerpt else ""

    return result
