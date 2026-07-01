# Silver Bullet — Retrospective Validation Results

_Generated: 2026-07-01 19:11:48_

## What this tests

For each confirmed real-world drug-repurposing success, we ran the existing Biologist -> Chemist -> Reviewer pipeline on the disease (with no knowledge of the confirmed drug) and checked whether the confirmed drug was surfaced in the Reviewer's ranked candidate list.

## Honest limitation (read first)

> This harness tests the SCORING AND RANKING LOGIC on TODAY's live data, not the historical data available at the time of each discovery. The confirmed drug is intentionally NOT removed from ChEMBL/PubChem results — reconstructing pre-approval data availability is infeasible with current public APIs. A 'hit' means the existing scoring logic ranks the correct compound highly among real candidates for the disease's top target; it does NOT claim the pipeline would have made the discovery blind to history. The pipeline also pursues only the single top OT-associated target per disease (exactly as the live graph does), so a confirmed drug whose molecular target is not that top target cannot be surfaced — this is a real, reported limitation, not a scoring failure.

> **On LLM usage:** the recorded metrics (rank, composite_score, strong_match) are produced by fully deterministic numeric scoring — the Chemist ranks by (is_approved_drug, pchembl_value, tanimoto) and the Reviewer by a fixed weighted composite; neither uses an LLM. The pipeline's only LLM calls write prose rationale text, which this harness does not record. Rationale generation was therefore disabled for the harness run (via environment, with no code change) so the deterministic scoring path runs faster. Enabling it would not change a single number below.

## Overall summary

- **1/5** confirmed pairs recovered in the top 10 candidates.
- **1/5** confirmed pairs reached STRONG_MATCH (composite_score >= 0.7).
- **1/5** confirmed pairs appeared anywhere in the ranked list (any rank).
- **1/5** diseases were outside the rare/NTD universe the system covers.
- **1/5** cases errored before producing a ranked list.

## Per-case results

| Drug | Disease | Confirmed | Status | Top target pursued | Rank | Composite | Top 10 | STRONG_MATCH |
|---|---|---|---|---|---|---|---|---|
| Sildenafil | pulmonary arterial hypertension | 2005 | hit | PDE5A | 3 | 0.7133 | ✓ | ✓ |
| Thalidomide | multiple myeloma | 2006 | miss | FKBP1A | — | — | — | — |
| Metformin | polycystic ovary syndrome | 1994 | error | — | — | — | — | — |
| Everolimus | tuberous sclerosis complex | 2010 | miss | FKBP1A | — | — | — | — |
| Propranolol | infantile hemangioma | 2014 | out_of_scope | — | — | — | — | — |

## Per-case detail

### Sildenafil — pulmonary arterial hypertension (confirmed 2005)

- **Status:** hit
- **Known target (context only):** Acts on PDE5 (PDE5A) — a phosphodiesterase, not the typical top OT-associated target for PAH.
- **Top target pursued:** PDE5A (O76074), OT association 0.9
- **All targets considered for the disease:** PDE5A (0.9), PTGIR (0.9), EIF2AK4 (0.8096), KCNK3 (0.7857), BMPR2 (0.8544), INHBA (0.9), SMAD9 (0.7834), ATP13A3 (0.7547)
- **Candidate pool:** 32 chemist candidates -> 32 reviewed.
- **Found at rank 3** — composite_score 0.7133, STRONG_MATCH=True, is_approved_drug=True, matched by inchikey/chembl_id.
- **Interpretation:** HIT — Sildenafil appears at rank 3/32 (composite_score=0.7133, within top 10, reached STRONG_MATCH) against target PDE5A, matched by inchikey/chembl_id.

### Thalidomide — multiple myeloma (confirmed 2006)

- **Status:** miss
- **Known target (context only):** Binds CRBN (cereblon); also anti-angiogenic / anti-TNF. Molecular target only elucidated years after clinical use.
- **Top target pursued:** FKBP1A (P62942), OT association 0.85
- **All targets considered for the disease:** FKBP1A (0.85), FDPS (0.85), TOP2A (0.85), GSR (0.85), NR3C1 (0.85), CD38 (0.85), TNFSF11 (0.85), CRBN (0.6846), NRAS (0.642), CXCR4 (0.6588), XPO1 (0.6416), SLAMF7 (0.85), GPRC5D (0.85), TNFRSF17 (0.6801)
- **Candidate pool:** 25 chemist candidates -> 25 reviewed.
- **Reason:** 'Thalidomide' did not appear among the 25 ChEMBL candidate compound(s) for the selected top target FKBP1A (P62942). The Chemist only admits compounds with Homo sapiens IC50/Ki bioactivity at assay confidence >= 8 against THIS target. The most likely reason is that the confirmed drug's molecular target is not FKBP1A (the top OT-associated target for this disease), so it is out of the pursued target's candidate pool — or it lacks qualifying high-confidence bioactivity records there.
- **Interpretation:** MISS — Thalidomide was not surfaced. 'Thalidomide' did not appear among the 25 ChEMBL candidate compound(s) for the selected top target FKBP1A (P62942). The Chemist only admits compounds with Homo sapiens IC50/Ki bioactivity at assay confidence >= 8 against THIS target. The most likely reason is that the confirmed drug's molecular target is not FKBP1A (the top OT-associated target for this disease), so it is out of the pursued target's candidate pool — or it lacks qualifying high-confidence bioactivity records there.

### Metformin — polycystic ovary syndrome (confirmed 1994)

- **Status:** error
- **Known target (context only):** Insulin-sensitizer (AMPK / mitochondrial complex I); no single clean protein target. Used OFF-LABEL for PCOS.
- **Reason:** target_selection failed: 'NON RARE IN EUROPE: Polycystic ovary syndrome' is in the rare/NTD universe but has no Open Targets EFO mapping, so its targets cannot be scored.
- **Interpretation:** The disease is in-universe but target selection raised an error (e.g. no Open Targets EFO mapping or no associated targets), so no candidates could be scored.

### Everolimus — tuberous sclerosis complex (confirmed 2010)

- **Status:** miss
- **Known target (context only):** mTOR inhibitor (MTOR); TSC1/TSC2 loss drives mTOR hyperactivation, so MTOR is a strong disease-mechanistic target.
- **Top target pursued:** FKBP1A (P62942), OT association 0.5679
- **All targets considered for the disease:** FKBP1A (0.5679), VHL (0.3483), IFNG (0.4031), TSC2 (0.8867), TSC1 (0.8682)
- **Candidate pool:** 25 chemist candidates -> 25 reviewed.
- **Reason:** 'Everolimus' did not appear among the 25 ChEMBL candidate compound(s) for the selected top target FKBP1A (P62942). The Chemist only admits compounds with Homo sapiens IC50/Ki bioactivity at assay confidence >= 8 against THIS target. The most likely reason is that the confirmed drug's molecular target is not FKBP1A (the top OT-associated target for this disease), so it is out of the pursued target's candidate pool — or it lacks qualifying high-confidence bioactivity records there.
- **Interpretation:** MISS — Everolimus was not surfaced. 'Everolimus' did not appear among the 25 ChEMBL candidate compound(s) for the selected top target FKBP1A (P62942). The Chemist only admits compounds with Homo sapiens IC50/Ki bioactivity at assay confidence >= 8 against THIS target. The most likely reason is that the confirmed drug's molecular target is not FKBP1A (the top OT-associated target for this disease), so it is out of the pursued target's candidate pool — or it lacks qualifying high-confidence bioactivity records there.

### Propranolol — infantile hemangioma (confirmed 2014)

- **Status:** out_of_scope
- **Known target (context only):** Non-selective beta-blocker (ADRB1/ADRB2). Effect on hemangioma discovered serendipitously.
- **Reason:** 'infantile hemangioma' was not found in the rare-disease / neglected-tropical-disease universe this system covers (Orphanet rare diseases + WHO NTDs). Silver Bullet is scoped to rare and neglected diseases. Check the spelling, try the disease's Orphanet name, or leave the field blank to auto-explore the ranked candidate list.
- **Interpretation:** 'infantile hemangioma' is outside Silver Bullet's rare-disease / neglected-tropical-disease scope, so the pipeline never evaluates it. This is a scope boundary, not a scoring failure — the harness correctly refuses to auto-pick an unrelated disease.
