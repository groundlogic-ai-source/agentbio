# Audit claim-set v2 — construction and exclusion log

- Constructed: 2026-08-11T02:41:10.563133+00:00
- Seed: 20260811 · cutoff: 2026-08-10
- Protocol: validation/audit_claimset_v2_preregistration.md
- Predecessor: validation/audit_claim_set_v1.json (instances excluded: 114 names)
- Claims: 100 (control=40, existing_fix=1, novel=59)
- Classes: E2_boxed_warning_not_withdrawal=1, N1_combination_product_splitting=8, N2_biologic_modality_mis_scope=43, N4_dose_route_implausibility=8, none=40

## Construction events (every acceptance AND exclusion)

v1 instance exclusion set: 114 names
Pools loaded (reachability only, all safety-v2 stamped): Multiple myeloma n=20, Multiple endocrine neoplasia type 2A n=24, Autosomal recessive hereditary chronic pancreatitis n=29
E4 unresolved_name_honesty — accepted ONLY if raw ChEMBL search cannot resolve the brand (v1 assumption repaired)
  EXCLUDE E4 Toprol-XL: no cutoff-eligible FDA label naming the brand (unverifiable ground truth)
  EXCLUDE E4 Lopressor: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Tenormin: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Hemangeol: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Inderal: no cutoff-eligible FDA label naming the brand (unverifiable ground truth)
  EXCLUDE E4 Betapace: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Serevent: no cutoff-eligible FDA label naming the brand (unverifiable ground truth)
  EXCLUDE E4 Rythmol: no cutoff-eligible FDA label naming the brand (unverifiable ground truth)
  EXCLUDE E4 Levophed: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Pacerone: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Cordarone: no cutoff-eligible FDA label naming the brand (unverifiable ground truth)
  EXCLUDE E4 Votrient: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Caprelsa: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Impavido: no cutoff-eligible FDA label naming the brand (unverifiable ground truth)
  EXCLUDE E4 Korlym: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Tykerb: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Cytomel: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Xospata: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Inrebic: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
  EXCLUDE E4 Cabometyx: raw ChEMBL search RESOLVES the brand — v1's falsified assumption; not a valid unresolvable-name claim
E1 safety_withdrawal — refreshed-pool safety-flagged drugs verified against ChEMBL withdrawn_flag
E3 direction_incompatible — MM mechanism-capped drugs verified against ChEMBL action_type on NR3C1
  EXCLUDE E3 FLUTICASONE PROPIONATE: ChEMBL action_type AGONIST is not incompatibility-class for a glucocorticoid-activation indication (ground truth fails)
E2 boxed_warning_not_withdrawal — refreshed-pool black-box drugs verified against raw FDA labels
  EXCLUDE E2 ALBUTEROL: no cutoff-eligible FDA label with a boxed warning (ground truth unverifiable)
  EXCLUDE E2 CABOZANTINIB: no cutoff-eligible FDA label with a boxed warning (ground truth unverifiable)
  EXCLUDE E2 LEVOSALBUTAMOL: no cutoff-eligible FDA label with a boxed warning (ground truth unverifiable)
  EXCLUDE E2 METOPROLOL: no cutoff-eligible FDA label with a boxed warning (ground truth unverifiable)
  EXCLUDE E2 MILTEFOSINE: no cutoff-eligible FDA label with a boxed warning (ground truth unverifiable)
  EXCLUDE E2 NOREPINEPHRINE: no cutoff-eligible FDA label with a boxed warning (ground truth unverifiable)
  ACCEPT existing_fix/E2_boxed_warning_not_withdrawal: VANDETANIB (citation fda_label e5721cb8-4185-47b9-bbb3-1c587e558a03 dated 2026-04-23)
E-group total: 1 (E1=0 E2=1 E3=0 E4=0)
N1 combination-product splitting — fixed combo list verified against raw FDA labels (>=2 active substances)
  ACCEPT novel/N1_combination_product_splitting: Amlodipine and benazepril (citation fda_label 27b0c628-820a-48bf-a411-86c0ebd4cc8d dated 2026-06-29)
  ACCEPT novel/N1_combination_product_splitting: Valsartan and hydrochlorothiazide (citation fda_label 167f49f0-2d39-49a2-9f9e-1359a7c7ba3d dated 2026-07-30)
  EXCLUDE N1 Olmesartan and amlodipine: label unverifiable or <2 substances (max substances seen: 0)
  EXCLUDE N1 Atorvastatin and amlodipine: label unverifiable or <2 substances (max substances seen: 0)
  ACCEPT novel/N1_combination_product_splitting: Sacubitril and valsartan (citation fda_label 000dc81d-ab91-450c-8eae-8eb74e72296f dated 2026-07-06)
  ACCEPT novel/N1_combination_product_splitting: Buprenorphine and naloxone (citation fda_label 713db2c6-0544-4633-b874-cfbeaf93db89 dated 2026-07-26)
  ACCEPT novel/N1_combination_product_splitting: Dorzolamide and timolol (citation fda_label c9ae08c5-275d-44b3-9d1e-5d8fedc21b96 dated 2024-05-25)
  ACCEPT novel/N1_combination_product_splitting: Emtricitabine and tenofovir alafenamide (citation fda_label 06f66e98-e6ee-4538-9506-6c1282cc14c1 dated 2026-07-07)
  EXCLUDE N1 Dolutegravir and lamivudine: label unverifiable or <2 substances (max substances seen: 0)
  EXCLUDE N1 Hydrocodone and acetaminophen: label unverifiable or <2 substances (max substances seen: 0)
  ACCEPT novel/N1_combination_product_splitting: Naproxen and esomeprazole (citation fda_label 65b892f2-8385-4bb0-8121-a58b01e8d13d dated 2026-07-23)
  ACCEPT novel/N1_combination_product_splitting: Glecaprevir and pibrentasvir (citation fda_label 7bf99777-0401-9095-8645-16c6e907fcc0 dated 2025-06-25)
N4 dose/route implausibility — local-only drugs claimed oral/systemic
  ACCEPT novel/N4_dose_route_implausibility: Netarsudil (citation fda_label 7d4f0e3a-5b86-4c43-982a-813b22ae7e22 dated 2026-01-20)
  ACCEPT novel/N4_dose_route_implausibility: Loteprednol etabonate (citation fda_label 36cfd5b8-c892-44e1-aaed-fb32d7aeca7c dated 2026-07-13)
  ACCEPT novel/N4_dose_route_implausibility: Nepafenac (citation fda_label 10f411d3-a81e-074a-e063-6294a90ab547 dated 2026-04-01)
  ACCEPT novel/N4_dose_route_implausibility: Difluprednate (citation fda_label 4a3b2649-ee3d-4648-91c8-912a8ba2e73c dated 2026-04-15)
  ACCEPT novel/N4_dose_route_implausibility: Bepotastine (citation fda_label cd6a061d-8ad7-4ad1-b039-1e3aa3435d32 dated 2024-10-10)
  ACCEPT novel/N4_dose_route_implausibility: Epinastine (citation fda_label 0d4ee45c-e58f-4b7c-b389-898f5c27f54d dated 2025-03-22)
  ACCEPT novel/N4_dose_route_implausibility: Alcaftadine (citation fda_label 22473109-dc9e-45b6-b188-d7d5773f5a14 dated 2024-10-07)
  EXCLUDE N4 Cromolyn sodium: labeled routes ['intrabronchial', 'nasal', 'ophthalmic', 'oral'] include a systemic route — not a local-only drug (ground truth fails)
  ACCEPT novel/N4_dose_route_implausibility: Fluorometholone (citation fda_label c9cbf06e-5413-4c3c-9ded-174c856a4ce1 dated 2026-05-12)
N3 species/preclinical-only — v1 gates PLUS no cutoff-eligible FDA label (label absence is part of the v2 defect definition)
  EXCLUDE N3 NVP-AST487: no cutoff-eligible primary paper (citation unverifiable)
  EXCLUDE N3 SPP86: no cutoff-eligible primary paper (citation unverifiable)
  EXCLUDE N3 AL082D06: no cutoff-eligible primary paper (citation unverifiable)
  EXCLUDE N3 CGP 20712A: Europe PMC clinical-trial hits = 2 (human clinical evidence may exist)
  EXCLUDE N3 SR 59230A: no cutoff-eligible primary paper (citation unverifiable)
N2 biologic modality mis-scope — enriched-dataset non-small-molecule rows verified against raw FDA labels (BLA)
  ACCEPT novel/N2_biologic_modality_mis_scope: Becaplermin (citation fda_label 377b3021-13d7-96d5-e063-6394a90a8ca3 dated 2026-05-08)
  ACCEPT novel/N2_biologic_modality_mis_scope: Belatacept (citation fda_label c16ac648-d5d2-9f7d-8637-e2328572754e dated 2021-07-28)
  ACCEPT novel/N2_biologic_modality_mis_scope: Belimumab (citation fda_label 2fa3c528-1777-4628-8a55-a69dae2381a3 dated 2025-06-20)
  ACCEPT novel/N2_biologic_modality_mis_scope: Beractant (citation fda_label 7ef9e3a5-fc39-4ae1-0dad-6b47a1684635 dated 2020-10-15)
  ACCEPT novel/N2_biologic_modality_mis_scope: Bevacizumab (citation fda_label aa27acbd-d117-4350-aeee-17bc2e2c0ca4 dated 2026-07-21)
  ACCEPT novel/N2_biologic_modality_mis_scope: Blinatumomab (citation fda_label 38b482a8-960b-4591-9857-5031ecb830aa dated 2026-04-14)
  ACCEPT novel/N2_biologic_modality_mis_scope: Brentuximab vedotin (citation fda_label 3904f8dd-1aef-3490-e48f-bd55f32ed67f dated 2025-11-11)
N-group before reallocation: 23/59 (N1=8 N2=7 N3=0 N4=8)
  REALLOCATING 36 novel shortfall per fixed order N1 -> N4 -> N2
  ACCEPT novel/N2_biologic_modality_mis_scope: Calfactant (citation fda_label 315c128a-272d-4c57-bfbe-1a8b3402af08 dated 2026-03-31)
  ACCEPT novel/N2_biologic_modality_mis_scope: Certolizumab pegol (citation fda_label b4c2c9dc-a0bb-4d64-a667-a67ebe88392d dated 2026-02-24)
  ACCEPT novel/N2_biologic_modality_mis_scope: Cetuximab (citation fda_label 8bc6397e-4bd8-4d37-a007-a327e4da34d9 dated 2026-04-16)
  ACCEPT novel/N2_biologic_modality_mis_scope: Collagenase clostridium histolyticum (citation fda_label 805cecd0-fd1f-11dd-87af-0800200c9a66 dated 2026-04-22)
  ACCEPT novel/N2_biologic_modality_mis_scope: Daratumumab (citation fda_label a4d0efe9-5e54-467e-9eb4-56fa7d53b60b dated 2026-06-05)
  ACCEPT novel/N2_biologic_modality_mis_scope: Denileukin diftitox (citation fda_label ac3613d7-4304-40e7-a3f5-adb8a8adca5d dated 2025-01-23)
  ACCEPT novel/N2_biologic_modality_mis_scope: Denosumab (citation fda_label e15dfe74-1575-4a2a-895e-323c05613362 dated 2026-07-14)
  ACCEPT novel/N2_biologic_modality_mis_scope: Dinutuximab (citation fda_label d66bdf0d-9d65-45de-ae5b-a58617c27492 dated 2026-07-06)
  ACCEPT novel/N2_biologic_modality_mis_scope: Dornase alfa (citation fda_label d8c78a7e-ff99-48f3-8952-643ec2ea0f86 dated 2025-12-17)
  ACCEPT novel/N2_biologic_modality_mis_scope: Dulaglutide (citation fda_label 463050bd-2b1c-40f5-b3c3-0a04bb433309 dated 2026-03-12)
  ACCEPT novel/N2_biologic_modality_mis_scope: Ecallantide (citation fda_label f56aec67-c662-477c-b866-bfc23e8809cf dated 2025-07-01)
  ACCEPT novel/N2_biologic_modality_mis_scope: Eculizumab (citation fda_label ebcd67fa-b4d1-4a22-b33d-ee8bf6b9c722 dated 2026-07-08)
  ACCEPT novel/N2_biologic_modality_mis_scope: Elosulfase alfa (citation fda_label 0caa2565-12b2-0ad0-1f9a-273e81c3d4cc dated 2025-10-30)
  ACCEPT novel/N2_biologic_modality_mis_scope: Elotuzumab (citation fda_label 80686b7e-f6f4-4154-b5c0-c846425e2d91 dated 2022-03-22)
  ACCEPT novel/N2_biologic_modality_mis_scope: Etanercept (citation fda_label a002b40c-097d-47a5-957f-7a7b1807af7f dated 2026-05-11)
  ACCEPT novel/N2_biologic_modality_mis_scope: Evolocumab (citation fda_label cd61e902-166d-4aa6-9f3c-a18c1008d07e dated 2026-07-16)
  ACCEPT novel/N2_biologic_modality_mis_scope: Filgrastim (citation fda_label 97cc73cc-b5b7-458a-a933-77b00523e193 dated 2026-07-23)
  ACCEPT novel/N2_biologic_modality_mis_scope: Galsulfase (citation fda_label 59341250-deac-ed71-3823-a4f5d64dbd77 dated 2024-09-18)
  ACCEPT novel/N2_biologic_modality_mis_scope: Gemtuzumab ozogamicin (citation fda_label 32fd2bb2-1cfa-4250-feb8-d7956c794e05 dated 2025-12-19)
  ACCEPT novel/N2_biologic_modality_mis_scope: Ibritumomab tiuxetan (citation fda_label 25d367dc-da65-44c9-a844-1bf15339c285 dated 2023-04-25)
  ACCEPT novel/N2_biologic_modality_mis_scope: Idarucizumab (citation fda_label c7400f8a-dcf4-a6df-6d07-983081b1bf34 dated 2024-01-23)
  ACCEPT novel/N2_biologic_modality_mis_scope: Idursulfase (citation fda_label 60cba843-5aab-4dd7-96dc-66648d413be3 dated 2025-04-15)
  ACCEPT novel/N2_biologic_modality_mis_scope: Imiglucerase (citation fda_label df60f030-866b-4374-a31f-8ae3f6b45c38 dated 2025-12-22)
  ACCEPT novel/N2_biologic_modality_mis_scope: Infliximab (citation fda_label 37eaca10-d812-48bc-8b8e-b836bfa9968f dated 2026-07-28)
  ACCEPT novel/N2_biologic_modality_mis_scope: Insulin Aspart (citation fda_label 30c64769-d2f7-4604-ac9e-2a6bc09506c9 dated 2026-07-31)
  ACCEPT novel/N2_biologic_modality_mis_scope: Insulin Degludec (citation fda_label 21335fe4-d395-4501-ac2a-2f20d7520da9 dated 2025-10-14)
  ACCEPT novel/N2_biologic_modality_mis_scope: Insulin Detemir (citation fda_label 82192527-99aa-4b53-8ce9-9173668d309c dated 2024-10-09)
  ACCEPT novel/N2_biologic_modality_mis_scope: Insulin Glargine (citation fda_label 5328761e-59d3-ca7a-e063-6394a90ae810 dated 2026-06-07)
  ACCEPT novel/N2_biologic_modality_mis_scope: Insulin Glulisine (citation fda_label e7af6a7a-8046-4fb4-9979-4ec4230b23aa dated 2025-11-25)
  ACCEPT novel/N2_biologic_modality_mis_scope: Insulin Human (citation fda_label 29f4637b-e204-425b-b89c-7238008d8c10 dated 2026-05-30)
  ACCEPT novel/N2_biologic_modality_mis_scope: Insulin Lispro (citation fda_label 616daea1-0b79-4970-a141-6f99f2072f02 dated 2026-04-13)
  ACCEPT novel/N2_biologic_modality_mis_scope: Interferon beta-1a (citation fda_label d70a39cc-de15-4c12-a1ec-8063b69ea0e1 dated 2025-11-19)
  ACCEPT novel/N2_biologic_modality_mis_scope: Interferon beta-1b (citation fda_label 66311f74-0472-4fa3-848a-06002ca0def5 dated 2026-03-03)
  ACCEPT novel/N2_biologic_modality_mis_scope: Ipilimumab (citation fda_label 2265ef30-253e-11df-8a39-0800200c9a66 dated 2026-06-12)
  ACCEPT novel/N2_biologic_modality_mis_scope: Ixekizumab (citation fda_label ac96658a-d7dc-4c7c-8928-2adcdf4318b2 dated 2026-01-22)
  ACCEPT novel/N2_biologic_modality_mis_scope: Laronidase (citation fda_label a80ac249-cae4-41f3-88bb-344088b20e60 dated 2026-07-22)
Controls (pool-free) — seeded sample of approved single-ingredient oral small molecules; label verifies cleanliness
  ACCEPT control/none: Teriflunomide (citation fda_label 2d2724f6-8812-4a26-b2ec-936b71f868e1 dated 2026-05-06)
  ACCEPT control/none: Perindopril (citation fda_label 87768fbf-7c63-47da-8925-0316f343d6ef dated 2024-03-13)
  ACCEPT control/none: Meloxicam (citation fda_label 385fd779-1be1-49ae-8213-750b96ecc997 dated 2026-07-16)
  ACCEPT control/none: Citalopram (citation fda_label 25b0d184-2a4c-42b0-9a57-7a00fa3e0cd7 dated 2026-07-15)
  ACCEPT control/none: Desipramine (citation fda_label dc1601be-e16a-411f-b9b8-01c51abb2441 dated 2026-04-28)
  ACCEPT control/none: Sodium phenylbutyrate (citation fda_label d15f2cbf-4d22-40b5-8ef9-16b9d323a7f9 dated 2026-07-17)
  ACCEPT control/none: Carisoprodol (citation fda_label 26e466be-8066-e562-e063-6394a90a9f81 dated 2026-06-22)
  ACCEPT control/none: Cladribine (citation fda_label e0b363cb-db83-44d6-89d0-1f7176be7459 dated 2026-06-19)
  ACCEPT control/none: Cycloserine (citation fda_label 8e7e2665-7a3d-3f54-9f92-5fe845f02ef9 dated 2024-07-30)
  ACCEPT control/none: Pyrimethamine (citation fda_label fdeff3ed-e783-411a-8317-57e8b0e931e0 dated 2025-04-17)
  ACCEPT control/none: Mirtazapine (citation fda_label 10ea9d83-19c3-4f3b-a1cc-cfc48b42507f dated 2026-07-29)
  ACCEPT control/none: Levetiracetam (citation fda_label 1e3cfafe-5784-4ec6-91dc-cd74b01d0bb4 dated 2026-08-03)
  ACCEPT control/none: Nilutamide (citation fda_label d5740b8f-fbb3-4023-9133-9e359a9ab980 dated 2026-06-26)
  ACCEPT control/none: Cefaclor (citation fda_label 2f61354e-bcb0-41aa-955f-51eabc11387b dated 2025-05-01)
  ACCEPT control/none: Oxaprozin (citation fda_label e881dca6-004e-1c3e-e053-2a95a90a02ab dated 2026-05-22)
  ACCEPT control/none: Chlorzoxazone (citation fda_label 6c77ca6b-fda6-43ab-8179-3e0bde6aa65f dated 2026-04-13)
  ACCEPT control/none: Eliglustat (citation fda_label 819f828a-b888-4e46-83fc-94d774a28a83 dated 2024-03-03)
  ACCEPT control/none: Raltegravir (citation fda_label 46ef8e2e-ae63-6a3c-e054-00144ff8d46c dated 2026-07-13)
  ACCEPT control/none: Selegiline (citation fda_label 040a97e5-efd1-4a2f-b737-2f13e5366d70 dated 2025-11-24)
  ACCEPT control/none: Sertraline (citation fda_label 1fcd3f6d-a71f-b131-e063-6394a90a4b26 dated 2026-05-14)
  ACCEPT control/none: Mercaptopurine (citation fda_label 9c6fc66b-522f-499d-961a-cde6ca0ec2e5 dated 2026-07-20)
  ACCEPT control/none: Fluvoxamine (citation fda_label 1f172569-9110-44df-acc5-0367e37ed784 dated 2026-07-22)
  ACCEPT control/none: Avanafil (citation fda_label 1d04e17d-7399-430a-a8bb-fa1912b155e9 dated 2025-12-17)
  ACCEPT control/none: Pravastatin (citation fda_label 0c4b59f1-92a8-44f8-8b4c-691f35777460 dated 2026-07-30)
  ACCEPT control/none: Benzonatate (citation fda_label 07018b47-76f5-49d3-b7d3-081f82d382b2 dated 2026-08-04)
  ACCEPT control/none: Erythromycin (citation fda_label 49b3ca93-43cc-439c-8f5d-e3ea2e87ad2f dated 2026-06-23)
  ACCEPT control/none: Eplerenone (citation fda_label bcd7595f-700f-48ba-bf83-05a995ce0731 dated 2026-06-24)
  ACCEPT control/none: Flurbiprofen (citation fda_label c27f2d57-4b9d-4b78-8d85-3bac84f733b1 dated 2026-04-14)
  ACCEPT control/none: Lisdexamfetamine (citation fda_label c32b5dce-2de4-4db0-a4ba-d2ce3b0a32fc dated 2026-07-13)
  ACCEPT control/none: Vortioxetine (citation fda_label 239215eb-be32-4286-9677-1e5556c6cccf dated 2026-04-23)
  ACCEPT control/none: Mexiletine (citation fda_label a61a07f6-0b48-4dcd-ad42-5d90f6e69ab1 dated 2026-05-14)
  ACCEPT control/none: Mecamylamine (citation fda_label 0b149023-f7a8-442d-ac69-295df8e66ed3 dated 2025-02-20)
  pool-free controls: 32/32 (104 attempts)
Controls (pool-context) — approved drugs absent from the pooled case
  EXCLUDE control TIOTROPIUM: label unverifiable or not single-ingredient
  EXCLUDE control INDACATEROL: present in pool cddaa8e1
  ACCEPT control/none: GLYCOPYRRONIUM (citation fda_label 5b372650-e56e-47a5-93e2-c0c292017059 dated 2026-05-21)
  EXCLUDE control VILANTEROL: label unverifiable or not single-ingredient
  EXCLUDE control ACLIDINIUM: label unverifiable or not single-ingredient
  EXCLUDE control PREDNISOLONE: present in pool 2de0698b
  EXCLUDE control TRIAMCINOLONE ACETONIDE: label unverifiable or not single-ingredient
  EXCLUDE control FLUTICASONE FUROATE: label unverifiable or not single-ingredient
  EXCLUDE control UMECLIDINIUM: label unverifiable or not single-ingredient
  ACCEPT control/none: REVEFENACIN (citation fda_label 6dfebf04-7c90-436a-9b16-750d3c1ee0a6 dated 2022-05-03)
  ACCEPT control/none: METHYLPREDNISOLONE (citation fda_label 0cb0ba76-067f-46c4-a5d8-b585d5ecafe3 dated 2026-07-23)
  EXCLUDE control DEXAMETHASONE: present in pool 2de0698b
  pool-context controls: 3/8
  pool-context dynamic fill (Amendment 4): seeded sample of approved single-ingredient small molecules absent from the assigned pooled case
  ACCEPT control/none: Bicalutamide (citation fda_label 26e30155-a50b-df14-e063-6394a90a9be4 dated 2026-04-23)
  ACCEPT control/none: Naloxegol (citation fda_label 28596788-b73f-4247-85fc-3105f51fff5a dated 2025-12-22)
  ACCEPT control/none: Hydromorphone (citation fda_label 4c5c1cc8-c42b-46e3-ad68-8e22f57101f2 dated 2026-07-02)
  ACCEPT control/none: Zoledronic acid (citation fda_label 5de65a7f-a219-4b22-9155-61fedc84433b dated 2026-04-13)
  ACCEPT control/none: Disulfiram (citation fda_label d3832a1a-a140-49c1-a7df-134fa81341e3 dated 2026-05-27)
  pool-context controls after dynamic fill: 8/8 (8 attempts)
