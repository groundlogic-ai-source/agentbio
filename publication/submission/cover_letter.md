# Cover letter — Journal of Cheminformatics (first submission)

[Date]

Dear Editors,

We submit "Frozen benchmark and external audit validation of an
evidence-audited drug-repurposing prioritization pipeline for rare diseases"
for consideration as a Research article.

Computational repurposing pipelines are typically validated by retrospective
rediscovery, and their "trust" layers — where they exist — are validated on
defect classes their own developers already fixed. This manuscript reports a
different evaluation design, and we believe its *instruments* are the primary
contribution:

1. A rediscovery benchmark with mechanical, offline-first case selection, a
   pre-registered funnel-feasibility screen whose pass rate is reported in
   the same sentence as the hit rate, and full disease-side identity holdout
   across every evidence lane.
2. To our knowledge, the first **external validation of a repurposing
   pipeline's audit layer** against a frozen claim set with independent
   ground truth — including four pre-registered defect classes with no prior
   fix, test, or trap — executed as exactly one scored run with published
   thresholds.
3. A complete, reproducible evidentiary chain: committed case lists, claim
   set (sha256-pinned), raw per-claim outputs, freeze manifests with
   code-drift and results-hash binding, and scripts that regenerate every
   figure and table.

We report the results as measured, including where they are unfavorable: the
discovery core rediscovered 6/22 in-scope primary cases (27.3%; 95% CI
10.7–50.2%) with performance concentrated in ultra-rare monogenic disease,
and the audit study **failed** its pre-registered thresholds (defect recall
0.533; control false-flag rate 0.175) — a failure invisible to the internal
regression suite, which passed 12/12 traps. The failure analysis (stale-data
disclosure, a falsified construction assumption, a detector precision bug) is
reported in full, with thresholds unmoved.

The manuscript explicitly disclaims clinical validation: the system is a
research-prioritization and evidence-audit tool, and no output constitutes a
treatment recommendation.

This work is not under consideration elsewhere. A preprint has been posted on
bioRxiv (DOI: [to add after posting]). LLM use within the evaluated system
and in manuscript preparation is disclosed in the Declarations. The author
develops the evaluated system; mitigation measures (pre-registration before
code, frozen artifacts, mechanical scoring, external ground truth) are
described in the Limitations.

Suggested reviewers: [2–3 names with expertise in cheminformatics validation,
benchmarking methodology, or drug repurposing — author to fill; exclude
anyone with conflicts].

Sincerely,
[Author Name]
Independent Researcher · [ORCID] · [email]
