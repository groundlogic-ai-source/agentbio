# Task 44 — Target Pursuit vs Compound Pool Coverage Diagnosis

**Date:** 2026-08-03  
**Status:** Offline diagnostic only — no ranking weights, source calls, or pipeline changes.  
**Source data:** `benchmark_results_v1_partial.json` (target considered sets), `task43_offline_report.md` (rank-context snapshots from stopped engineering run), `source_coverage_matrix.md` (ChEMBL/GtoPdb/openFDA lane audit), `v1-miss-forensics.md`.

---

## Question under investigation

The stopped engineering run shows that reviewer score-integrity fixes did **not** rescue the
worst-ranked fixtures: Phenobarbital and Lamotrigine sit at 217/223, Vincristine at 23/23,
Promazine at 152/227, and Mercaptopurine at 51/67.  Before changing ranking weights again, this
document separates two structural failure modes:

- **Mode A — Missing target:** the known drug's true mechanistic target was not selected or
  pursued, so the drug has no evidence pathway through a high-scoring target chain.
- **Mode B — Missing compound:** the known drug (or an active-moiety-equivalent) is absent from
  every target's candidate pool, regardless of which targets were pursued.

The two modes have different fixes.  Mode A calls for target selection changes (pathway-neighbor
augmentation, pathway-aware scoring).  Mode B calls for pool coverage changes (additional evidence
sources, assay-tier widening, species expansion).  Changing ranking weights before diagnosing the
mode wastes a benchmark run.

---

## Per-fixture diagnosis

### Fixture 1 & 2 — Phenobarbital and Lamotrigine / Lennox-Gastaut syndrome

**Targets considered (from v1 partial):** DNM1, CA2, CA4, CNR1, SCN1A, (+ GABRB3, GABRA1 via v2
pathway-neighbor expansion)

| Question | Finding |
|---|---|
| True mechanistic target pursued? | **Partial.** SCN1A was in the considered set (pursued). GABA-A subunits (GABRA1/GABRB3) entered via pathway-neighbor expansion. |
| Known drug in compound pool? | **Yes — both drugs are generated** (rank context shows Phenobarbital at 217/223, Lamotrigine at 217/223). Each appears via SCN1A or GABRA1 lane. |
| Rank | 217 / 223 (composite score 0.400) |
| Why ranked poorly | The flat score floor. All ~20 candidates at ranks 197–216 share the same 0.400 composite score. The drugs that outrank Phenobarbital/Lamotrigine (clobazam, secobarbital, flurazepam — all `qualified_directional` via GABRA1) carry an explicit direction badge (GABA-A potentiation/agonism). Phenobarbital and Lamotrigine arrive via SCN1A with only `qualified` (undirected) evidence and score identically to every other SCN1A compound in the flood. With 223 candidates and a scoring floor that resolves nothing at the bottom quartile, tie-breaking is effectively random. |
| Failure mode | **Mode A (target) × Mode B (evidence quality) compound effect.** The correct targets (SCN1A, GABRA1) were pursued, so the drug IS generated. The problem is that the evidence for these drugs via those targets is either non-directional (SCN1A) or arrives at the same score as >20 competitors (GABRA1 congeners). The large pool (223 candidates) amplifies the tie-flood problem: score resolution breaks down when many drugs share the same 0.400 floor. |
| Confounding scope note | Lamotrigine's primary mechanism (Naᵥ blockade) is a ChEMBL protein-family target with 0 direct single-protein activities (evidence lives under component SCN1A/SCN2A IDs). Its `qualified_directional` footprint is smaller than its pharmacological importance warrants. |

**Root cause (both cases):** Too many undifferentiated candidates land on the same composite
floor. Tie-breaking at the floor is not diagnostic — no scoring component separates
"sodium-channel blocker approved for LGS" from "tyrosine-kinase inhibitor that hits SCN1A
as an off-target."

---

### Fixture 3 — Mercaptopurine / Acute Promyelocytic Leukemia

**Targets considered (v1):** RARA, IDH1, IDH2, KIT, DNMT1 + downstream v2 pathway neighbors

| Question | Finding |
|---|---|
| True mechanistic target pursued? | **No.** Mercaptopurine is an antimetabolite; its registered mechanism target is PPAT (amidophosphoribosyltransferase). PPAT has **zero ChEMBL activities** and is GtoPdb-dark. PPAT was not in the considered set (OT genetic score near zero for APL). |
| Known drug in compound pool? | **Yes — generated at rank 51/67 (score 0.475).** Mercaptopurine enters the pool via marginal ChEMBL signal (28 activities, mostly Potency vs UNCHECKED assays). It is not recovered via its stated mechanism — it leaks in as a weak ChEMBL match against one of the considered targets (likely DNMT1 or a purine-metabolism neighbor). |
| Why ranked poorly | Score 0.475 vs 0.715 for top-ranked drugs. The top candidates (glasdegib, vimseltinib, acyclovir — via SMO, KIT, PNP respectively) have strong pChEMBL/directional evidence through genuinely high-OT targets. Mercaptopurine's evidence comes from weak ChEMBL assays with no mechanism endpoint, so its composite is capped by low pChEMBL and no `qualified_directional` badge. |
| Failure mode | **Mode A (dominant).** The drug IS in the pool, but only through a weak off-mechanism entry. Its true mechanism (de novo purine synthesis inhibition) is ChEMBL-dark AND GtoPdb-dark. The openFDA label contains the mechanism verbatim ("inhibitors of de novo purine synthesis") and Europe PMC has 368 anchored hits — neither source is connected to the current pipeline. |
| Scope note | Mercaptopurine's mechanism is fundamentally non-protein-binding (antimetabolite / pathway). Even if PPAT were added as a target, a pChEMBL-shaped scoring model cannot assign an honest score to this drug. The fix requires both the openFDA/EuroPMC lane AND a mechanism-class evidence type in the ledger. |

---

### Fixture 4 — Vincristine / Rhabdomyosarcoma

**Targets considered (v1):** TP53, NF1, DICER1, PAX3, PAX7 (genetic drivers of rhabdomyosarcoma)

| Question | Finding |
|---|---|
| True mechanistic target pursued? | **No.** Vincristine targets tubulin (TUBB1/TUBA1A). Tubulin genes are not OT genetic-association targets for rhabdomyosarcoma (cytotoxic chemotherapy targets structural biology, not the driver mutation). TUBB entered only via v2 pathway-neighbor expansion or multi-source union. |
| Known drug in compound pool? | **Yes — generated at rank 23/23 (last place, score 0.275).** It entered the pool (likely via the TUBB multi-source lane since v2 has GtoPdb curated tubulin interactions), but with minimal supporting evidence. |
| Why ranked last | Score 0.275 vs colchicine 0.675 and other vinca alkaloids (vinblastine, vinorelbine 0.375–0.425). Vincristine likely receives a safety or mechanism cap: it is a known cytotoxic with black-box warning, and the reviewer's safety cap can suppress its composite score independent of pharmacological strength. Additionally, tubulin binding is structurally identical for the whole vinca/taxane class — vincristine has no differentiated evidence that would place it above its class analogues. |
| Failure mode | **Mode A + safety cap.** The true target (TUBB) was not in the primary selection; TUBB entered only as a pathway neighbor. The drug IS in the pool but is scored last because (a) its OT association through TUBB→rhabdomyosarcoma is near zero, (b) it may carry a safety cap, and (c) its class analogues score better on the same TUBB evidence because they lack vincristine's specific cytotoxicity profile. |
| Scope note | Vincristine for rhabdomyosarcoma is a standard-of-care cytotoxic, not a mechanism-driven repurpose. This may be a legitimate scope boundary for the pipeline (which is designed for target-based mechanism repurposing, not empirical cytotoxics). If classified correctly as out-of-scope, this case is not a scoring failure — it is a taxonomy failure. |

---

### Fixture 5 — Promazine / Acute Intermittent Porphyria

**Targets considered (v1):** HMBS, ACO2

| Question | Finding |
|---|---|
| True mechanistic target pursued? | **No.** Promazine is a phenothiazine antipsychotic; its therapeutic use in AIP is **symptomatic** (DRD2-mediated sedation for pain/nausea), not disease-modifying. DRD2 has no OT genetic association with AIP. HMBS (heme synthesis pathway) is the correct disease target, but promazine does not target HMBS. |
| Known drug in compound pool? | **Yes — generated at rank 152/227 (score 0.525).** Promazine enters the pool via DRD2 through multi-source expansion (it has 500+ ChEMBL activities including 149 IC50 and 133 Ki records). DRD2 was absent from the primary pipeline pursued set but is apparently included in the broader v2 run. |
| Why ranked in the middle | Score 0.525 vs 0.625 for top competitors (hydroxyzine/DRD2 at rank 135, triclofos/GABRA1 at 132). The drugs ahead of promazine have the same DRD2/GABRA1 landing but stronger pChEMBL or `qualified_directional` badges. Promazine scores competitively for its mechanistic class but sits behind more evidence-rich DRD2 drugs. |
| Failure mode | **Mode A (weak) + scope issue.** Target DRD2 was not in the primary considered set, but the drug IS generated via DRD2 through multi-source. The primary failure is a scope classification problem: promazine's indication for AIP is symptomatic, not disease-modifying. The pipeline treats it as a target-based repurpose when it is not. This case may not be fixable by ranking changes — it is a scope-classification problem. |

---

## Two-axis summary

| Fixture | True target in considered set? | Drug in compound pool? | Primary failure mode | Fixable by ranking change alone? |
|---|---|---|---|---|
| Phenobarbital / LGS | Partial (SCN1A ✓, GABRA1 via neighbor) | **Yes** (rank 217/223) | Score floor tie-flood; no directional signal | **No** — need directional evidence or score resolution |
| Lamotrigine / LGS | Partial (same) | **Yes** (rank 217/223) | Same as above | **No** |
| Mercaptopurine / APL | No (PPAT absent, zero ChEMBL) | **Yes** — weakly (rank 51/67) | Mechanism-class evidence absent from pipeline | **No** — need openFDA/EuroPMC lane + mechanism-class ledger type |
| Vincristine / Rhabdo | No (TUBB via neighbor only) | **Yes** — barely (rank 23/23) | Cytotoxic scope; safety cap; no OT signal for TUBB | Likely not — cytotoxic scope boundary |
| Promazine / AIP | No (DRD2 absent from primary) | **Yes** — via multi-source (rank 152/227) | Symptomatic scope; DRD2 not disease-modifying | Likely not — scope-classification problem |

**The critical finding: in all 5 cases, the drug IS generated. The bottleneck is not pool absence —
it is evidence quality, evidence type, and pool size.** Changing pool inclusion thresholds will
not materially improve rankings for these cases.

---

## Comparison with non-fixture controls

To guard against overfitting the fix to the five known drugs, this section compares the
fixture failure modes against the repoDB small-molecule development corpus (13 cases).

### The one hit: Ibrutinib / Waldenström macroglobulinemia (rank 2)

- **BTK** was the top-ranked target in the considered set (OT genetic association, directly
  druggable).
- Ibrutinib has strong pChEMBL evidence (IC50 ≤ 1 nM) via multiple BTK assays.
- Multiple evidence sources confirm the BTK mechanism with directional badge.
- **Mode**: neither A nor B — correct target pursued, strong evidence, small pool (few
  competing BTK inhibitors outrank it for this indication).

This is the template for a well-functioning case: genetic driver target selected, drug has
high-confidence directional pChEMBL evidence through that target, pool is not flooded with
same-score competitors.

### Structural misses in the non-fixture corpus

| Drug / Disease | Considered targets | True mechanism | Mode | Notes |
|---|---|---|---|---|
| Sapropterin / PKU | PAH, NSUN2, COL1A1 | PAH (cofactor chaperone) | **Neither A nor B** | PAH IS in the considered set. Miss is assay-type: cofactor chaperone activity has no IC50/Ki; Sapropterin won't appear in pChEMBL pool for PAH. |
| Pyridostigmine / MG | ACHE, FCGRT, C5 | ACHE (cholinesterase inhibitor) | **Mode B** | ACHE was pursued. Pyridostigmine has a quaternary ammonium structure and its ChEMBL ACHE activities are carbamate-class compounds, not IC50/Ki binding assays. |
| Imatinib / HES | IL5, PDGFRA, KIT | PDGFRA (FIP1L1-PDGFRA fusion) | **Mode A (precedent hijack)** | IL5 wins via precedent score (mepolizumab approval); PDGFRA is in the list but demoted. Documented in target_selection_diagnosis.md. |
| Anagrelide / ET | PDE3B, PDE3A, JAK2, MPL | PDE3A (phosphodiesterase) | Borderline A | PDE3A was considered. Anagrelide may not have IC50/Ki evidence in ChEMBL for PDE3A (it's an older drug with in-vivo characterization). |
| Lorazepam / LGS | CA2, CA4, CNR1, DNM1, SCN1A, GABRB3 | GABRA/GABRB | **Same as Phenobarbital/LGS** | Same score-floor tie-flood expected; same directional-evidence gap. |
| Everolimus / TSC | FKBP1A, TSC1, TSC2 | FKBP1A→mTOR | **Mode A (precedent hijack + pool poison)** | TSC + MTOR documented case; everolimus entered pool but MTOR was cache-poisoned during v1 run. |

### What the controls establish

1. **The score-floor problem is NOT fixture-specific.** Lorazepam/LGS (a non-fixture control) will
   hit exactly the same SCN1A/GABRA1 flood if run through the current pipeline. Any fix for
   Phenobarbital/Lamotrigine must be verified against Lorazepam and other GABA-acting drugs or it
   is overfit.

2. **Mode B (assay-type exclusion) affects multiple non-fixture cases** (Sapropterin, Pyridostigmine).
   A tiered pool fix (EC50/Kd/Potency tier) would help these cases too — good generalization signal.
   But it will NOT rescue Phenobarbital/Lamotrigine (their issue is directional evidence, not assay
   tier) or Mercaptopurine (antimetabolite, no binding assay exists for any tier).

3. **The precedent-hijack failure (Mode A, Imatinib)** is independent of the pool and is unaffected
   by tiered pools or additional sources. It requires the target-selection fix documented in
   target_selection_diagnosis.md (F2: precedent calibration).

4. **Cytotoxic/symptomatic scope cases** (Vincristine, Promazine) are not fixable by either pool
   or target improvements. A scope-classification gate (pre-register the expected drug-mechanism
   modality class and classify cytotoxics/symptomatics before scoring) is the appropriate fix.

---

## Recommendation for the next engineering change

### Priority 1 — Score resolution at the pool floor (addresses LGS cases + Lorazepam control)

**Problem:** With 200+ candidates, the composite scoring formula does not differentiate
drugs that carry only weak undirected evidence (pChEMBL ~5, qualified but not directional)
from each other. A flat 0.400 floor means tie-breaking is positional, not diagnostic.

**Fix (general rule, not fixture-specific):** Introduce a pool-size normalization term or
a score-resolution bonus for `qualified_directional` evidence that is currently not carried
into the final composite. This is already partially in the ledger (`qualified_directional`
badge exists) but does not feed the composite formula. Augmenting the composite with a small
directional bonus (e.g. +0.05) would differentiate phenobarbital/GABRA1 `qualified_directional`
drugs from untargeted SCN1A bystanders without changing the relative order of well-evidenced
candidates.

**Controls needed before re-run:** Verify that Lorazepam/LGS improves under this change
(should rise from equivalent floor), and that well-ranked Ibrutinib/BTK is unaffected (it
already scores above the floor via strong pChEMBL).

**Expected impact:** LGS cases improve from rank 217/223 toward the 130–160 range (where
the GABRA1 `qualified_directional` drugs currently sit). Does not flip them to Top-10.

### Priority 2 — Mechanism-class evidence type for antimetabolites (addresses Mercaptopurine)

**Problem:** Mercaptopurine's mechanism is not a binding event — it is an antimetabolite
that inhibits de novo purine synthesis. The evidence ledger has no slot for mechanism-class
evidence (pathway/enzyme-process assertion); it can only represent drug→protein binding.
The openFDA label states the mechanism verbatim; Europe PMC has 368 anchored co-occurrence
hits. Neither source is wired.

**Fix:** Add a `mechanism_class` evidence type to the assertion ledger and wire the openFDA
label mechanism-class extraction lane (already researched in source_coverage_matrix.md).
This is a general rule: any drug whose mechanism is classified as antimetabolite,
pro-drug-activation, or pathway-inhibitor (not receptor/enzyme binding) should use
mechanism-class evidence, not pChEMBL.

**Controls needed:** Verify that Sapropterin/PKU (cofactor chaperone — also non-binding
mechanism) benefits from the same lane, and that high-confidence binding drugs (Ibrutinib)
are not degraded by the change.

**Expected impact:** Mercaptopurine moves from rank 51/67 → near top of pool if mechanism-class
evidence scores comparably to strong-pChEMBL evidence. Does not affect the LGS cases.

### Priority 3 — Scope classification gate (addresses Vincristine + Promazine, and controls)

**Problem:** The pipeline receives Vincristine/rhabdomyosarcoma and Promazine/AIP as
target-based mechanism repurposing problems. Vincristine is a cytotoxic agent for which the
"target" (tubulin) is shared across all cancers; Promazine's role in AIP is symptomatic
sedation, not disease-modification. There is no fix in the target or compound layers that
converts these into meaningful mechanism-driven rediscoveries.

**Fix (general rule):** Pre-classify each disease–drug pair by the expected drug-action modality
before the pipeline runs:
- Cytotoxic/antimitotic (vincristine, paclitaxel class): mark `scope_limitation:
  cytotoxic_chemo`; report as out-of-expected-scope rather than a failure.
- Symptomatic/supportive care (promazine for AIP, benzodiazepines for procedure): mark
  `scope_limitation: symptomatic`; report separately.

This is a classification, not an exclusion. The pipeline can still run these cases and
report them, but they should not inflate the failure rate or drive scoring changes.

**Controls needed:** Verify the classification gate correctly labels Pyridostigmine/MG
(cholinesterase inhibitor for neuromuscular symptoms — symptomatic, not disease-modifying)
and does NOT label BTK inhibitors or RARA agonists (which ARE mechanism-driven).

---

## Rerun gate

The engineering acceptance run should be rerun (`--fresh`) only after **all three** of the
following pass:

| Gate | Condition |
|---|---|
| **G1 — Score resolution** | The `qualified_directional` bonus is implemented, unit-tested against ≥1 positive (GABRA1 drug rises relative to SCN1A-only drug) and ≥1 negative (Ibrutinib composite unchanged). |
| **G2 — Mechanism-class evidence** | openFDA label lane wired and returning mechanism-class assertions for ≥3 test drugs (including mercaptopurine). Evidence ledger `mechanism_class` type accepted without schema error. Unit test: mercaptopurine label extraction returns the correct mechanism. |
| **G3 — Scope classifier** | A pre-classification step correctly labels Vincristine/rhabdomyosarcoma as `cytotoxic_chemo` and Promazine/AIP as `symptomatic`. Gate must also NOT mis-classify BTK inhibitors, RARA agonists, or PDE inhibitors as cytotoxics or symptomatic. |

All three gates must pass before a `--fresh` rerun is justified. Running the full pipeline on
all five fixtures costs significant LLM inference budget; partial gate passage should be
checked via the existing unit test suite and the offline diagnostic script only.

**Do not change ranking weights or thresholds to improve fixture ranks.** The fixtures are
regression tests for structural coverage; rank improvements must come from evidence-quality
improvements verified on the broader non-fixture corpus.

---

## Provenance note

All ranks in this document come from `validation/task43_offline_report.md`, which was
computed from a prior stopped engineering acceptance run.  Target considered sets come from
`validation/benchmark_results_v1_partial.json`.  Source coverage audits come from
`validation/source_coverage_matrix.md` and `.agents/memory/v1-miss-forensics.md`.
No pipeline was re-run to produce this document.
