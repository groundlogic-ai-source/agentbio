# Step 1 gate — does disease-side holdout reach the audit layer?

**Question.** The triage discrimination benchmark asks whether the audit
layer can tell a confirmed repurposing apart from the pipeline's own top
picks. That question is only meaningful if the audit envelope does not
already contain the answer. Before building anything downstream, the
holdout had to be *proven*, not assumed.

**Outcome.** The gate **failed as the code stood**, was repaired, and now
**passes**. The repair is a real change to the audit layer, not a
reinterpretation, and it carries a disclosed cost.

Reproduce either state:

```bash
python3 -m validation.audit_lane_holdout_probe --unredacted   # pre-fix: 5/5 leak
python3 -m validation.audit_lane_holdout_probe                # current:  0/5 leak
```

---

## 1. The audit lanes were never covered by holdout

`data_sources/holdout.py` scopes itself, in its own docstring, to the
**discovery** side: Open Targets approved-drug name lists, the ChEMBL
`drug_indication` EFO fallback, and the `has_approved` / unmet-need
signal they feed. It is imported by the agent and discovery modules
only.

Neither audit-lane source module references it:

| Source module | References `holdout` |
| --- | --- |
| `data_sources/openfda.py` | **no** |
| `data_sources/pubtator_assertions.py` | **no** |

A lane that never consults the holdout cannot be redacted by it. This is
a coverage gap, not a bug in holdout: the audit layer was built after it,
over different sources, for a different purpose.

## 2. The leak was real and total

Five confirmed drug→disease repurposings were probed through the
production audit-context builder with the drug held out. Leakage is
detected by mechanical token matching against the disease name and its
registered aliases — never by judgment.

| Drug | Disease | Indication leaked |
| --- | --- | --- |
| Sildenafil | Pulmonary arterial hypertension | **yes** (129 quotes) |
| Thalidomide | Multiple myeloma | **yes** (4) |
| Tretinoin | Acute promyelocytic leukemia | **yes** (15) |
| Everolimus | Tuberous sclerosis complex | **yes** (50) |
| Anakinra | Cryopyrin-associated periodic syndromes | **yes** (4) |

**5/5 pairs leaked.** For a confirmed repurposing this is close to
unavoidable by construction: the drug is approved for the disease, so the
drug's own FDA label states the answer in plain text.

## 3. But the leak was confined to one surface

Every one of the 202 hits landed on the same path,
`products[].evidence[].quote`. Broken out by label field:

| Label field | Leaking quotes | Total quotes |
| --- | --- | --- |
| `dosage_and_administration` | 25 | 213 |
| `clinical_pharmacology` | 62 | 190 |
| `indications_and_usage` | 60 | 188 |
| `description` | 0 | 188 |
| `mechanism_of_action` | 55 | 136 |

Two measurements make the fix possible:

* **Structured regulatory fields leaked nothing** — 0 hits across routes,
  dosage forms, product modality, combination status, and active
  ingredients. These are exactly the fields the deterministic detectors
  consume.
* **Zero leaks reached `findings`** in the unprobed default configuration,
  and zero reached the literature lane in these five pairs.

The literature lane's clean result is *weaker evidence* than the label
lane's: `title`, `evidence_sentence`, `experimental_context` and
`relation_span` are free text drawn from abstracts, so they are the same
hazard class and simply did not happen to fire on five pairs. They are
redacted anyway.

## 4. The repair

`data_sources/audit_redaction.py` applies disease-blind redaction at the
lane boundary in `build_audit_context`, under four constraints:

* **Holdout-only.** Production audit output is unchanged.
* **Post-cache.** The label lane caches payloads for 30 days; redacting
  upstream of `cache_set` would write holdout-shaped records into the
  shared cache and corrupt ordinary runs.
* **Allowlist, not denylist.** Records are rebuilt from an explicit set
  of structured keys, so a field added to a source later is dropped by
  default. Over-redaction weakens the instrument visibly; under-redaction
  invalidates the study silently.
* **Disclosed.** Each payload carries a `holdout_redaction` marker
  recording what was dropped, so a run can *prove* it was blind.

Post-fix: **0/5 pairs leak.**

## 5. What the repair costs

Redaction must buy blindness without changing what the audit measures,
or the benchmark would score a different instrument than the one the
product ships. Verified in `validation/test_audit_holdout_redaction.py`:

* N1 (combination), N2 (modality) and N4 (route/context) read structured
  fields and are **bit-identical** under redaction.
* N3 reads `experimental_setting` and `citation_eligible`, both retained,
  so its verdict logic is **unaffected**.
* **One real loss:** the N4 *dose* comparison reads
  `dosage_and_administration` quotes. Under redaction it degrades from
  `review` to `unresolved` — an explicit "not measured", never a silent
  pass. The discrimination profile does not consume dose, so this does
  not bias the study, but it must not be reported as a clean instrument.

## 6. Residual limitations

* **PMIDs and DOIs are retained** in the literature lane for provenance
  and citation-eligibility accounting. They are externally resolvable, so
  blindness holds against the deterministic profile (which never fetches
  them) but would *not* hold against a narrating LLM given tool access.
  The benchmark must therefore run with `narrate=False`.
* **Drug identity is retained** — brand and generic names, ingredients,
  application numbers. A reader who recognises the drug may infer its
  indication. This is irreducible: the study is *about* the drug, so the
  drug cannot be hidden. Blindness here means the pipeline is not *handed*
  the answer in text, not that the answer is unguessable.
* **Five pairs is a small probe.** It establishes that the leak existed
  and that redaction closes it on these cases; the unit guard, not the
  probe, is what prevents regression.

## 7. Bearing on earlier work

No retroactive impact on audit claim-set v1 or v2. Those studies scored
claim verification against labels with **no holdout active** — disease
blindness was never part of their design or their claims. The gap
mattered only once a study proposed to hold a disease out and still use
the audit lanes, which is what this benchmark does.
