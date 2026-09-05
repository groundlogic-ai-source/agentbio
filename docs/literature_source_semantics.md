# Literature source semantics

The literature limitation source separates the candidate gate from related
evidence disclosure.

## Exact-use assessment

`exact_use_label` uses only:

- `EXPLICIT_LIMITATION`
- `APPLICABLE_SUPPORT`
- `NOT_APPLICABLE_TO_EXACT_DRUG_USE`
- `UNKNOWN/INTEGRITY_FAILED`

`IRRELEVANT` is not a source assessment. It is emitted only in the legacy
`label` projection for old report consumers. Source/classifier failures,
malformed batches, duplicate or invented PMIDs, citation failures, and
non-verbatim quotes remain fail-closed.

Intervention identity is verified independently as `EXACT_DRUG`, `DRUG_ALIAS`,
`DRUG_CLASS`, or `NO_INTERVENTION_MATCH`. Alias support can qualify as exact
only when the caller supplies that alias and it occurs in the source. Class
support cannot become exact-drug support.

## Related support

`related_support` is a separate disclosure-only object. Each entry retains its
verified PMID, PubMed URL, verbatim quote, intervention identity,
disease/subtype/use applicability, and an evidence level of `mechanistic`,
`disease_model`, or `case_report_clinical`.

The object always declares `efficacy_score_boost: 0`. It must not alter the
candidate efficacy score or cancel an exact-use limitation. For example, PMID
26392140 is relevant Cantú/KATP-class mechanistic support, but does not mention
repaglinide and therefore is not exact repaglinide support.