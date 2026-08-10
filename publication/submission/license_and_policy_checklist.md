# License & journal-policy checklist (author decisions before posting)

## Preprint license
- [ ] **CC-BY 4.0 (recommended)** — compatible with all five shortlisted
      journals; required by some funders (none here); maximizes reuse.
- [ ] Alternative CC-BY-NC-ND 4.0 — acceptable at the shortlisted venues but
      restricts reuse; choose deliberately.

## Code availability decision (blocks the manuscript statement)
- [ ] Choose one and update the manuscript's Code availability line:
  - **Public GitHub repository** (repo is currently private), tagged
    `preprint-v1`, plus a Zenodo archive DOI for permanence; or
  - **Zenodo snapshot only** (code + validation artifacts, DOI, no ongoing
    public repo).
- [ ] Pick a code license (MIT or Apache-2.0 recommended; Apache-2.0 adds
      explicit patent grant — relevant for a pipeline others may build on).
- [ ] Before making anything public: re-run the secret scan history check —
      memory notes Boltz output artifacts once embedded third-party AWS
      presigned credentials (expired, stripped; repo private). Verify with
      `git log -p | grep -i ASIA | head` returns nothing sensitive, and that
      no API keys appear in committed files.

## Third-party data licenses (what the manuscript may say)
- ChEMBL — CC BY-SA 3.0 (data). Cite; share-alike applies to adapted
  *databases*, not to papers reporting results computed from queries.
- Open Targets — CC BY 4.0. Orphanet — free for research with attribution
  (registration for bulk). Reactome — CC BY 4.0.
- DrugCentral — CC BY-SA 4.0; the pinned 2023 snapshot is used for
  benchmarking (research use); redistribution of the snapshot itself is a
  share-alike question — if releasing artifacts publicly, ship the build
  script + dump hash rather than the derived SQLite if in doubt.
- GtoPdb — ODbL / CC BY-SA 4.0 content; **commercial use requires paid
  access** — this is a go-live dependency for any commercial product, noted
  in the manuscript; academic preprint reporting of benchmark results is
  unaffected.
- PubChem — public domain (NIH) with contributor-level constraints noted;
  Europe PMC / PubTator3 — APIs public, respect article-level licenses;
  ClinicalTrials.gov — public with attribution; openFDA — CC0/public with
  disclaimer; DailyMed — NLM public.
- No content above is legal advice; it is the recorded reading of each
  source's published terms on 2026-08-10.

## Journal policy confirmations (re-verify at submission time)
- [ ] Target journal accepts preprints (all five shortlisted do — policy
      pages recorded in `journal_shortlist.md`, checked 2026-08-10).
- [ ] Target journal accepts single independent author without institutional
      affiliation (all five do; affiliation line is "Independent Researcher").
- [ ] AI-use disclosure requirements met (Declarations section; J
      Cheminform/BMC, PLOS, PeerJ, F1000 all require disclosure of AI use in
      writing and prohibit AI authorship — ours complies).
- [ ] APC/waiver: BMC waiver application (unfunded independent author)
      before submission if needed.

## Manuscript self-checks
- [ ] Title/abstract contain no diagnostic/treatment claims (verify after
      any edit).
- [ ] Every number in abstract/results matches `derived_metrics.json`
      (re-run `make_figures.py` and diff mentally against §4).
- [ ] Placeholders all replaced: author name, ORCID, email, bioRxiv DOI,
      code-availability statement.
