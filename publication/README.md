# Publication package — AgentBio report & preprint

Everything here is generated from, or cites, **frozen committed artifacts**.
Nothing is a screenshot of a spreadsheet; every number is reproducible.

## Contents

| Path | What |
|---|---|
| `technical_report.md` | Complete technical validation report (all studies, failure analyses, limitations, reproducibility) |
| `manuscript.md` | Journal-neutral manuscript (bioRxiv-ready text) |
| `supplement.md` | Supplement (artifact inventory, LLM inventory, source versions; splices generated tables) |
| `make_figures.py` | Recomputes every headline metric from committed artifacts → `derived_metrics.json`, `tables.md`, `figures/fig1–5.png`, `generated/supplement_*.md`; **verifies** the frozen audit metrics from the raw archive |
| `build_pdf.py` | Renders `dist/manuscript.pdf` + `dist/supplement.pdf` |
| `submission/` | bioRxiv checklist, ranked journal shortlist, cover letter, response-to-reviewers template, submission metadata, license/policy checklist |

## Rebuild everything

```bash
python3 publication/make_figures.py   # metrics + figures + tables (+ audit recalc assertion)
python3 publication/build_pdf.py      # dist/manuscript.pdf, dist/supplement.pdf
python3 -m validation.run_audit_claimset --label audit_claimset_v1 --recalc-only  # independent audit re-verification
```

Dependencies: `matplotlib`, `fpdf2`, `scipy` (install: see
`requirements-publication` note below — on Replit these were installed via
the package manager into the Python 3.11 environment).

## Editorial rules (do not break)

1. **Frozen results never change.** If a defect fix or new study happens, it
   gets new artifacts and new pre-registrations; this package's numbers stay
   as published.
2. `derived_metrics.json` is the single source of numeric truth for the
   prose. If a number in the text disagrees with it, fix the text.
3. The audit study's FAIL and the chance-baseline saturation stay in the
   headline position they occupy now; they are not demotable.
4. Any analysis added in response to reviewers is labeled *post hoc* and
   never overwrites frozen artifacts (see
   `submission/response_to_reviewers_template.md`).
5. Author placeholders (`[Author Name]`, ORCID, email, bioRxiv DOI,
   code-availability decision) are filled by the author at submission time —
   the checklists in `submission/` walk through it.
