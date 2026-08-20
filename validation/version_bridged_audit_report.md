# Version-Bridged Audit Acceptance

**Study:** `version_bridged_audit_acceptance_v1`  
**Base commit at run:** `2dc02d2b17fecb8cfbf10c216676695927269e7e`  
**Implementation fingerprint:** `6786366c9b605c60df567d5c13e88ed820882c1e0a5b1bd69a96d6be6b6a7cae`  
**Result:** **PASS**

## Endpoint results (kept separate)

- Historical discovery recovery: **0/16** (unchanged frozen contract)
- Stable-identity universe overlap: **7/16**
- Production top-5 overlap: **2/16**
- Mechanism-pool recovery after approval-first repair: **2/16**
- Paired mechanism-pool gains versus legacy cap: **1**
- Supplied-drug audit coverage: **16/16**

Audit coverage is not discovery recall. Stable-identity overlap is not a rediscovery hit. The immutable machine-v2 result remains 0/16 under its original exact-string contract.

## Controls

- PASS — found-by-discovery: Prednisone / Lupus Erythematosus, Systemic: found state remains distinct from supplied-only audit
- PASS — found-by-discovery: Lenalidomide / Multiple Myeloma: found state remains distinct from supplied-only audit
- PASS — unrelated supplied-only: Infliximab / Giant Cell Arteritis: remains absent, unranked, unscored, and outside target coverage
- PASS — unrelated supplied-only: Mycophenolate mofetil / Interstitial Cystitis: remains absent, unranked, unscored, and outside target coverage
- PASS — unresolved synthetic drug name: unresolved identity is not treated as biological absence
- PASS — degraded mechanism source: transport failure does not become NO_MECHANISM_DATA

## Run bounds and provenance

- Retrieval date: 2026-08-20
- Runtime: 10.37 seconds
- LLM calls: 0
- Mechanism source: ChEMBL live REST API; the endpoint does not expose a release identifier, so the retrieval date and frozen output are recorded.
- Enabled mechanism-pool repair: at most 200 source rows inspected per resolved target, at most 100 approved outputs returned; overflow and incomplete metadata fail closed.
- Disease-side target selection and holdout code remained byte-identical to the preregistered versions.

## Promotion decision

Republish with the version-bridged supplied-drug audit and the bounded approval-first mechanism completeness repair enabled; do not describe this as a replacement Study C result or as a change to the historical 0/16 rediscovery result. The repair's kill switch is `AGENTBIO_DISABLE_MECHANISM_COMPLETENESS_REPAIR=1`.

## Per-case bridge

| Disease | Drug | Stable identity | Top-5 | Legacy pool | Repaired pool | Audit scope |
|---|---|---:|---:|---:|---:|---|
| Giant Cell Arteritis | Triamcinolone | no | no | no | no | auditable_only_because_supplied |
| Interstitial Cystitis | Pentosan Polysulfate | no | no | no | no | auditable_only_because_supplied |
| Lupus Erythematosus, Systemic | Betamethasone | yes | yes | no | yes | auditable_only_because_supplied |
| Lupus Erythematosus, Systemic | Chloroquine | no | no | no | no | auditable_only_because_supplied |
| Lupus Erythematosus, Systemic | Hydroxychloroquine | yes | yes | yes | yes | auditable_only_because_supplied |
| Multiple Myeloma | Bortezomib | no | no | no | no | auditable_only_because_supplied |
| Multiple Myeloma | Carmustine | yes | no | no | no | auditable_only_because_supplied |
| Multiple Myeloma | Cyclophosphamide | no | no | no | no | auditable_only_because_supplied |
| Neuroblastoma | Cyclophosphamide | no | no | no | no | auditable_only_because_supplied |
| Neuroblastoma | Vincristine | no | no | no | no | auditable_only_because_supplied |
| Ulcerative Colitis | Adalimumab | yes | no | no | no | auditable_only_because_supplied |
| Ulcerative Colitis | Cortisone acetate | yes | no | no | no | auditable_only_because_supplied |
| Ulcerative Colitis | golimumab | yes | no | no | no | auditable_only_because_supplied |
| Ulcerative Colitis | Infliximab | yes | no | no | no | auditable_only_because_supplied |
| Ulcerative Colitis | Sulfadiazine | no | no | no | no | auditable_only_because_supplied |
| Ulcerative Colitis | Vedolizumab | no | no | no | no | auditable_only_because_supplied |
