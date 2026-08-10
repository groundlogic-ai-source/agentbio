# bioRxiv submission checklist (manual, for the author)

Posting is done by you, from your account, after your review. Nothing here is
automated and nothing posts without your consent.

## Before you start
- [ ] Read `publication/manuscript.md` end-to-end; replace every `[bracketed
      placeholder]` (author name, city/country, ORCID, email).
- [ ] Decide code availability (see `license_and_policy_checklist.md`) and
      update the manuscript's Code availability statement to match the
      decision.
- [ ] Regenerate the PDFs after any text change:
      `python3 publication/make_figures.py && python3 publication/build_pdf.py`
- [ ] Final files: `publication/dist/manuscript.pdf` (main) +
      `publication/dist/supplement.pdf`.

## Account & scope
- [ ] Register at bioRxiv (free). Verify email.
- [ ] Confirm scope fit: this is a computational-biology / bioinformatics
      methods + validation preprint with no clinical claims — within
      bioRxiv's life-sciences scope (submission guide verified 2026-08-10:
      https://www.biorxiv.org/submit-a-manuscript). All authors must consent
      to deposition; content must be unpublished.
- [ ] Category: **Bioinformatics** (alternative: Pharmacology and
      Therapeutics — check current category list at submission time).

## Submission form fields
- [ ] Title / abstract: copy from the manuscript exactly (title and abstract
      describe a research-prioritization / evidence-audit system — keep the
      scope sentences; do not let the abstract editor strip them).
- [ ] Authors: name, affiliation "Independent Researcher", ORCID.
- [ ] License: **CC-BY 4.0** recommended (maximizes journal compatibility;
      all shortlisted journals accept CC-BY preprints). Alternatives:
      CC-BY-NC-ND (more restrictive; some journals fine, some funders not).
- [ ] Competing interests: use the manuscript's declaration verbatim
      ("The author develops the evaluated system...").
- [ ] Funding: "No external funding."
- [ ] AI disclosure: bioRxiv and target journals require it — the manuscript
      Declarations section already contains it; copy if the form asks.
- [ ] Supplement: upload `supplement.pdf` as the single supplementary file.
- [ ] Preprint-to-journal transfer: if submitting to PLOS, bioRxiv offers a
      direct-transfer option at posting time; you may decline and submit
      separately.

## After posting
- [ ] Record the bioRxiv DOI in `submission_metadata.json` (`biorxiv_doi`).
- [ ] Add the DOI to the journal cover letter (template provided).
- [ ] Post the DOI in the project README / website if desired.
- [ ] Do NOT submit to more than one journal at a time (see journal
      checklist).
