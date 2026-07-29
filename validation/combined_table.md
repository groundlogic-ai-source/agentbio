# AgentBio Retrospective Validation — Combined Hit/Miss Table

Generated: 2026-07-29  
Harness: retrospective retrospective audit against repoDB "Approved" drug-indication pairs  
Pipeline: single top OT-associated target per disease → Chemist (ChEMBL IC50/Ki, confidence ≥ 8) → Reviewer ranking

---

## Summary

| Set | Cases run | Hits | Misses | Out-of-scope / Error |
|-----|-----------|------|--------|----------------------|
| Original 3 (Part A) | 3 | **1** | 2 | 0 |
| New 10 (Part B) | 10 | **0** | 9 | 1 |
| **Total** | **13** | **1 (8%)** | **11 (85%)** | **1 (8%)** |

---

## Combined Table (3 original + 10 new)

| # | Disease | Approved Drug | Drug Class | Top Target (selected) | AgentBio Rank | Status | Absence Reason |
|---|---------|--------------|------------|----------------------|---------------|--------|----------------|
| **O1** | Idiopathic pulmonary arterial hypertension | Sildenafil | Small molecule | PDE5A (O76074) | **3 / 32** ✅ | **HIT** | — composite 0.74, strong match |
| **O2** | Multiple myeloma | Thalidomide | Small molecule / IMiD | FKBP1A (P62942) | — | MISS | Thalidomide's true target is CRBN (cereblon); FKBP1A is the top OT-scored target so the wrong pool is searched |
| **O3** | Tuberous sclerosis complex | Everolimus | mTOR inhibitor | FKBP1A (P62942) | — | MISS | Everolimus acts on MTOR/FKBP12 complex; FKBP1A pool has no Everolimus ChEMBL record at conf ≥ 8 |
| **N1** | Cystic Fibrosis | Dornase alfa | Biologic (DNase) | VCP (P55072) | — | MISS | Dornase alfa is a recombinant human DNase I — targets extracellular DNA/RNA, not VCP; no ChEMBL small-mol record; CFTR was candidate #2 (OT 0.92) but not selected |
| **N2** | Cryopyrin-Associated Periodic Syndromes | Anakinra | Biologic (IL-1Ra) | N/A | — | OUT-OF-SCOPE | Disease group name not in Orphanet; canonical subtypes are CINCA/NOMID, Muckle-Wells, FCAS — each is a separate Orphanet entry |
| **N3** | Chronic Infantile Neurological, Cutaneous, and Articular Syndrome (CINCA/NOMID) | Anakinra | Biologic (IL-1Ra) | IL1B (P01584) | — | MISS | Anakinra is a recombinant IL-1 receptor antagonist — binds IL1R1, not IL1B directly; excluded from ChEMBL small-molecule IC50/Ki pool |
| **N4** | Hemophilia A | Desmopressin | Peptide (vasopressin analogue) | PLG (P00747) | — | MISS | Desmopressin releases vWF from endothelium via AVPR2/AVPR1 — not a PLG inhibitor; biologic peptide absent from small-mol IC50/Ki pool |
| **N5** | von Willebrand Disease | Desmopressin | Peptide (vasopressin analogue) | PLG (P00747) | — | MISS | Same as N4; VWF was in candidate targets (OT 0.86) but PLG scored highest overall; Desmopressin is not a PLG ligand |
| **N6** | Hemophilia B | Coagulation factor VIIa Recombinant Human | Biologic (recombinant protein) | KLKB1 (P03952) | — | MISS | Factor VIIa is a recombinant serine protease — has no ChEMBL small-mol IC50/Ki record against KLKB1 or any target |
| **N7** | Prader-Willi Syndrome | Somatropin recombinant | Biologic (recombinant hGH) | OXTR (P30559) | — | MISS | Somatropin is recombinant human growth hormone — acts through GHR (candidate #3, OT 0.58); excluded from small-mol IC50/Ki pool |
| **N8** | Turner Syndrome | Somatropin recombinant | Biologic (recombinant hGH) | ESR1 (P03372) | — | MISS | Same as N7; GHR was candidate #4 (OT 0.61); Somatropin has no small-mol ChEMBL record |
| **N9** | Gaucher Disease | Imiglucerase | Biologic (enzyme replacement) | GBA1 (P04062) | — | MISS | Imiglucerase is recombinant glucocerebrosidase — the correct target (GBA1) was selected but the drug is an ERT protein, absent from small-mol IC50/Ki pool |
| **N10** | Mucopolysaccharidosis I | Laronidase | Biologic (enzyme replacement) | IDUA (P35475) | — | MISS | Laronidase is recombinant α-L-iduronidase — the correct target (IDUA) was selected but it is an ERT protein, absent from small-mol IC50/Ki pool |

---

## Key Findings

### 1. Hit rate: 1 / 13 (8%)
The one hit — Sildenafil for IPAH — is the only small-molecule drug in the set. Every miss involves either a **biologic drug** (recombinant protein, enzyme, antibody/receptor antagonist, peptide) or a **small molecule whose true target diverges from the top OT-scored target**.

### 2. Systematic miss type: biologic drugs (10 / 11 misses)
The ChEMBL compound pool is filtered to **Homo sapiens IC50/Ki at assay confidence ≥ 8**. Biologic drugs — recombinant proteins (Dornase alfa, Somatropin, Imiglucerase, Laronidase, Coagulation factor VIIa), peptides (Desmopressin), and protein biologics (Anakinra) — essentially never appear in this pool. This is a structural gap: the pipeline is scoped to **small-molecule drug discovery**.

### 3. Correct target identified but wrong drug class
For N9 (Gaucher / GBA1) and N10 (MPS-I / IDUA), the pipeline correctly selected the disease-causal target as #1. The miss is purely because the approved therapy is enzyme replacement, not a small molecule.

### 4. Target ranking accuracy (among misses)
Even in miss cases, the correct biological target often appears in the candidate list:
- **Cystic Fibrosis**: CFTR (OT 0.92) ranked #2 in OT score behind VCP after tractability scoring
- **Prader-Willi / Turner**: GHR was candidate #3-4 (Somatropin acts through GHR)
- **Hemophilia B**: F9 / F7 were in the candidate pool; the confirmed drug (Factor VIIa recombinant) is itself a coagulation factor

### 5. Small-molecule miss (O2, O3): top-target mismatch
Thalidomide (CRBN) and Everolimus (MTOR) both miss because the pipeline pursues FKBP1A — which scored highest on OT association + tractability but is not the confirmed drug target. This class of miss would be addressed by running all top-K targets instead of just the top-1.

---

## Repodb Selection Rule

Source: `data_prep/output/enriched_dataset.csv`, status == "Approved", ind_id matching Orphanet ORPHA: or WHO-NTD MONDO codes, ascending row index, excluding IPAH/MM/TSC.

Selected rows: 8, 36, 37, 46, 47, 50, 78, 80, 81, 110.

One case (row 36, CAPS) was out-of-scope because the CSV uses a disease-group name that Orphanet resolves only as individual ORPHA subtypes.

---

## Files
- `validation/repodb_results.json` — machine-readable 10-case results
- `validation/results.json` — original 3+2 case results  
- `validation/run_repodb_cases.py` — Part B harness script
