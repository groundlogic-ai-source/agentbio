# Silver Bullet — Final Canonical Validation

**Run date:** 2026-07-05  
**Settings:** `REPURPOSING_ONLY=True` · `K=5` · `PATHWAY_NEIGHBOR_MIN_APPROVED=3` · `TOP_K_FRACTION=0.0 (disabled)` · code frozen, no scoring/weight/threshold changes in this pass  

---

## Scored Cases

In-scope cases: formally approved repurposing pairs graded by what the pipeline surfaces with the above settings.

| # | Case | Expected target | Target rank | Status |
|---|------|----------------|-------------|--------|
| 1 | SILDENAFIL / pulmonary arterial hypertension | PDE5A | not surfaced | **MISS** |
| 2 | THALIDOMIDE / multiple myeloma | CRBN | #7 (outside K=5) | **MISS** |
| 3 | EVEROLIMUS / tuberous sclerosis | TSC1 | #4 (within K=5) | **MISS_MECHANISM_CLASS_MATCH** |

---

### Case 1 — SILDENAFIL / pulmonary arterial hypertension

**Status:** `MISS`

**Reason:** Stage 1 disease-query failure in two ways.

(a) The exact query "pulmonary arterial hypertension" is an Orphanet *Group of disorders* umbrella term. The pipeline rejects umbrella terms at Stage 1 to prevent false-positive scoring across heterogeneous subtypes.

(b) The correct specific subtype query "idiopathic pulmonary arterial hypertension" (Orphanet: *Idiopathic pulmonary arterial hypertension*) is in-universe and scoreable, but PDE5A does **not** appear in its scored target list. Open Targets does not link sildenafil/Revatio to this specific EFO ID — only to the broader umbrella EFO — so the pharmacological-precedent pathway does not add PDE5A for this disease. No pharmacological-precedent targets were added for idiopathic PAH.

**Stage 1 — top-5 targets for "idiopathic pulmonary arterial hypertension":**

| Rank | Symbol | UniProt | Tractability | Unmet need | Total | Method |
|------|--------|---------|-------------|------------|-------|--------|
| #1 | PTGIR | P43119 | — | — | 0.3474 | genetic_association |
| #2 | EIF2AK4 | Q9P2K8 | — | — | 0.3336 | genetic_association |
| #3 | BMPR2 | Q13873 | — | — | 0.3279 | genetic_association |
| #4 | SMAD9 | O15198 | — | — | 0.1544 | genetic_association |
| #5 | PPARGC1A | Q9UBK2 | — | — | 0.0778 | genetic_association |

PDE5A rank in full scored list: **not found**  
Pharmacological-precedent targets added for this EFO: **none**

**Stage 2 — PDE5A (O76074) compound pool (independently checked):**

Primary approved (repurposing_only=True): **7** · Expansion: skipped (≥3 threshold)

All approved drugs in PDE5A pool: `DIPYRIDAMOLE`, `MILRINONE`, `SILDENAFIL`, `SILDENAFIL CITRATE`, `TADALAFIL`, `VARDENAFIL`, `VARDENAFIL HYDROCHLORIDE`

Drug found in pool: **YES** — but PDE5A is not surfaced by Stage 1 for this disease.

**Notes:** Sildenafil (Revatio) is FDA-approved for PAH. The discovery failure is at Stage 1 (target not ranked via the specific subtype EFO), not at Stage 2 (drug IS in PDE5A's pool). The pipeline requires a non-umbrella Orphanet disease query, and even with the correct specific name, the OT EFO→approved-drug link for Revatio does not resolve to the idiopathic-PAH EFO.

---

### Case 2 — THALIDOMIDE / multiple myeloma

**Status:** `MISS`

**Reason:** CRBN (cereblon, the direct thalidomide-binding E3 ligase adaptor) ranks **#7** in the full scored target list for Multiple myeloma — one rank outside the K=5 window. The five pursued targets are all pharmacological-precedent targets whose compound pools do not contain thalidomide. At K=5 as specified, CRBN is not pursued and thalidomide is absent from the pooled candidate set.

Note: increasing K to ≥7 would include CRBN, and CRBN's repurposing-only pool contains `THALIDOMIDE`, `LENALIDOMIDE`, `POMALIDOMIDE`, and `DASABUVIR` — a **HIT** at that rank. The miss is a K-window issue, not a failure to identify CRBN or populate its pool.

**Stage 1 — top-5 targets for "multiple myeloma" (K=5 window):**

| Rank | Symbol | UniProt | Tractability | Unmet need | Total | Method |
|------|--------|---------|-------------|------------|-------|--------|
| #1 | FKBP1A | P62942 | 0.6632 | 0.0555 | 0.7187 | pharmacological_precedent |
| #2 | CD38 | P28907 | 0.6407 | 0.0555 | 0.6962 | pharmacological_precedent |
| #3 | FDPS | P14324 | 0.6289 | 0.0555 | 0.6844 | pharmacological_precedent |
| #4 | TOP2A | P11388 | 0.5875 | 0.0555 | 0.6430 | pharmacological_precedent |
| #5 | GSR | P00390 | 0.5613 | 0.0555 | 0.6168 | pharmacological_precedent |

CRBN rank in full scored list: **#7** · Within K=5: **No**

Pharmacological-precedent targets added: NR3C1, TOP2A, FDPS, FKBP1A, GSR, CD38, TNFSF11, SLAMF7, GPRC5D

**Stage 2 — CRBN (Q96SW2) compound pool (independently checked):**

Primary approved: **4** · Expansion: skipped (4 ≥ 3 threshold)

All approved drugs in CRBN pool: `DASABUVIR`, `LENALIDOMIDE`, `POMALIDOMIDE`, `THALIDOMIDE`

Drug found in pool: **YES** — but CRBN is not pursued at K=5.

Thalidomide in any K=5 target's pool: **No** (FKBP1A→rapalogs; CD38→no small-molecule IC50 records; FDPS→bisphosphonates; TOP2A→topoisomerase inhibitors; GSR→glutathione-reductase ligands)

**Notes:** CRBN was added via pharmacological precedent (thalidomide/lenalidomide/pomalidomide MOA → cereblon). It scores lower than FKBP1A/CD38/FDPS because CRBN's tractability score is pulled down by weaker ChEMBL bioactivity evidence (fewer high-confidence IC50/Ki records). K=5 is the proximate cause of the miss; the drug and target are both discoverable at K≥7.

---

### Case 3 — EVEROLIMUS / tuberous sclerosis

**Status:** `MISS_MECHANISM_CLASS_MATCH`

**Reason:** TSC1 is ranked **#4** in K=5 for Tuberous sclerosis complex (within the K=5 window). TSC1's primary approved-drug pool is empty (0 approved IC50/Ki compounds); the lazy pathway-neighbor expansion is triggered (0 < PATHWAY_NEIGHBOR_MIN_APPROVED=3). MTOR (P42345) is returned as a pathway neighbor; its approved pool contains SIROLIMUS (pChEMBL=9.075) and TEMSIROLIMUS (pChEMBL=5.75) — the correct mechanism (mTOR inhibition via the FKBP12–drug complex) and drug class (rapalogs). EVEROLIMUS (CHEMBL1908360, FDA-approved for TSC) is absent from the MTOR IC50/Ki pool: ChEMBL records everolimus activity against FKBP12 (its direct binding partner), not MTOR, so it does not appear in any MTOR bioactivity query regardless of confidence filters.

**Stage 1 — top-5 targets for "tuberous sclerosis" (K=5 window):**

| Rank | Symbol | UniProt | Tractability | Unmet need | Total | Method |
|------|--------|---------|-------------|------------|-------|--------|
| #1 | FKBP1A | P62942 | 0.4185 | 0.0410 | 0.4595 | genetic_association |
| #2 | VHL | P40337 | 0.2376 | 0.0410 | 0.2786 | genetic_association |
| #3 | TSC2 | P49815 | 0.2107 | 0.0410 | 0.2517 | genetic_association |
| #4 | **TSC1** ← expected | **Q92574** | **0.1886** | **0.0410** | **0.2296** | **genetic_association** |
| #5 | IFNG | P01579 | 0.1204 | 0.0410 | 0.1614 | genetic_association |

TSC1 rank: **#4** · Within K=5: **Yes**

**Stage 2 — TSC1 (Q92574) compound pool with lazy expansion:**

Primary approved: **0** · Expansion: **TRIGGERED** (0 < threshold 3)

Pathway neighbors with approved drugs:

| Neighbor | UniProt | Approved drugs in pool |
|----------|---------|----------------------|
| MTOR | P42345 | SIROLIMUS, TACROLIMUS ANHYDROUS, TEMSIROLIMUS, DASATINIB |
| PRKAA1 | Q13131 | SUNITINIB, ENTRECTINIB, PALBOCICLIB, UPADACITINIB |
| PRKAA2 | P54646 | EBASTINE, SUNITINIB, BERBERINE |

Full combined approved pool: `BERBERINE`, `DASATINIB`, `EBASTINE`, `ENTRECTINIB`, `PALBOCICLIB`, `SIROLIMUS`, `SUNITINIB`, `TACROLIMUS ANHYDROUS`, `TEMSIROLIMUS`, `UPADACITINIB`

Exact match for EVEROLIMUS: **No**  
Mechanism-class matches (rapalogs): **SIROLIMUS**, **TEMSIROLIMUS**

**Everolimus absence explained:** Everolimus (CHEMBL1908360) is FDA-approved for TSC but ChEMBL records its IC50/Ki bioactivity against FKBP12 (direct binding partner), not MTOR. The MTOR IC50/Ki pool therefore contains sirolimus (everolimus's parent compound, same mechanism) and temsirolimus (rapalog, same class) but not everolimus by name. This is a ChEMBL data-layer constraint, not a scoring or pipeline logic failure.

**Notes:** Correct mechanism (mTOR inhibition via FKBP12 complex) and drug class (rapalogs) are surfaced. The specific labeled compound (everolimus) is not. Classified `MISS_MECHANISM_CLASS_MATCH`.

---

## Excluded Cases

These cases are not scored. They fall outside what the pipeline's approved-drug discovery path can represent.

| # | Case | Reason |
|---|------|--------|
| 4 | METFORMIN / polycystic ovary syndrome | Off-label only; no FDA-approved indication for PCOS |
| 5 | PROPRANOLOL / infantile hemangioma | Disease outside Orphanet rare-disease / WHO-NTD universe |

---

### Case 4 — METFORMIN / polycystic ovary syndrome

**Status:** `EXCLUDED`

**Exclusion reason:** Metformin has no FDA-approved indication for PCOS. Its use in PCOS is off-label. Silver Bullet's approved-drug discovery path surfaces only formally approved repurposing; off-label use is outside scope and cannot form a valid scored pair.

**Universe probe:** PCOS IS in the Orphanet universe, matched as *"NON RARE IN EUROPE: Polycystic ovary syndrome"*. The exclusion is at the approved-indication level only — the disease entry exists but metformin's use for this indication is not formally approved, so no valid (drug, indication) pair exists for this pipeline to rediscover.

---

### Case 5 — PROPRANOLOL / infantile hemangioma

**Status:** `EXCLUDED`

**Exclusion reason:** Infantile hemangioma is not in the pipeline's candidate universe (Orphanet rare-disease list + WHO NTDs). The query *"infantile hemangioma"* returns `DiseaseNotInUniverse`. Hemangiomas are classified as congenital/developmental vascular anomalies in Orphanet rather than rare diseases in the scored universe; no EFO entry for this disease exists in the pipeline's universe and no (disease, target) pairs can be scored for it.

**Universe probe:** `not_in_universe` — confirmed `DiseaseNotInUniverse` raised for this query.

---

## Summary

| Metric | Count |
|--------|-------|
| Scored cases total | 3 |
| HIT | 0 |
| MISS_MECHANISM_CLASS_MATCH | 1 |
| MISS | 2 |
| Excluded (not scored) | 2 |

**Key findings:**

1. **PAH/sildenafil (MISS):** "pulmonary arterial hypertension" is an Orphanet umbrella group. The specific subtype query resolves correctly but Open Targets does not link Revatio to the idiopathic-PAH EFO, so PDE5A is not surfaced by Stage 1. PDE5A's compound pool independently contains sildenafil — the drug is mechanistically reachable if the target were surfaced.

2. **Myeloma/thalidomide (MISS):** CRBN ranks #7, one position outside the K=5 window. Thalidomide IS in CRBN's pool; K≥7 would produce a HIT. The miss is a K-window coverage parameter, not a discovery failure.

3. **TSC/everolimus (MISS_MECHANISM_CLASS_MATCH):** Correct mechanism (mTOR/FKBP12 rapalog pathway) and class (sirolimus, temsirolimus) are surfaced via lazy expansion from TSC1→MTOR. Everolimus is absent from the MTOR IC50/Ki pool due to ChEMBL recording its activity against FKBP12, not MTOR. The pipeline identifies the correct biology; the specific approved drug is not named.
