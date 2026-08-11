# Frozen benchmark and external audit validation of an evidence-audited drug-repurposing prioritization pipeline for rare diseases

**[Author Name]**¹

¹ Independent Researcher, [City, Country] · ORCID: [0000-0000-0000-0000] · Correspondence: [email]

*Manuscript type: Research article (methods + validation). Journal-neutral format.
Preprint intended: bioRxiv (Bioinformatics / Computational Biology).*

---

## Abstract

**Background.** Computational drug-repurposing pipelines are rarely evaluated
under conditions that prevent silent leakage, and their accompanying
"trustworthiness" layers are rarer still to be tested against defects nobody
engineered for. We built AgentBio, a pipeline that ranks (disease, target)
pairs for rare and neglected diseases, generates approved-drug candidates
from a union of evidence sources (ChEMBL, DrugCentral, GtoPdb, regulatory
labels, entity-linked literature), applies deterministic scoring with
explicit caps, and audits drug→disease claims against externally checkable
ground truth.

**Methods.** We report two frozen, pre-registered studies. (1) A
retrospective rediscovery benchmark (v2): 50 mechanically selected,
pre-registered cases; a property-based screen admitted 32 funnel-feasible
cases; disease-side drug identity was redacted at every layer. (2) An
external audit validation: a frozen set of 100 drug–disease claims with
independent ground truth — 60 defect claims (half from four novel,
pre-registered defect classes with no prior defense) and 40 clean controls —
scored exactly once, with exact Clopper–Pearson bounds and pre-registered
PASS thresholds.

**Results.** On funnel-feasible diseases (screen pass rate 32/50, 64%), the
pipeline rediscovered the confirmed drug in 6/22 in-scope primary cases
(27.3%; 95% CI 10.7%–50.2%), all six within the top 10; performance was
concentrated in ultra-rare monogenic disease (6/9) and absent in rarer-
mechanism strata (0/12). The first audit
study (claim-set v1) **failed** its pre-registered thresholds: defect recall
0.533 (CP lower 0.420 vs required ≥0.80/≥0.65) and control false-flag rate
0.175 (CP upper 0.304 vs required ≤0.15/≤0.30); novel-class recall was 0.967
(29/30). Failure analysis isolated a stale-data disclosure defect (persisted
candidate pools carrying pre-fix safety labels), a falsified
claim-construction assumption (brand names resolve in ChEMBL), and a
detector precision bug (preclinical-only finding over-firing on approved
drugs). After registered fixes, a fresh frozen claim set (v2) was
constructed and scored exactly once under the same discipline: the fixed
layer **passed** all thresholds — defect recall 60/60 = 1.000 (CP lower
0.951), control false-flag rate 2/40 = 0.050 (CP upper 0.149), and
novel-class recall 59/59 = 1.000 (classes with no prior defense).

**Conclusions.** Pre-registration plus one-scored-run discipline first
exposed an audit-layer failure that internal regression suites had masked,
then verified — under a fresh frozen study — that the registered fixes
hold. The first study's failure is reported alongside the pass, never
replaced by it. The system is a research prioritization and evidence-audit
tool; nothing here is evidence of clinical efficacy, and no output
constitutes a treatment recommendation.

**Keywords:** drug repurposing; rare disease; benchmark; pre-registration;
audit; validation; negative results

---

## 1. Introduction

Drug repurposing — finding new indications for approved drugs — is especially
attractive for rare and neglected diseases, where small patient populations
make de novo development economically fragile. A growing ecosystem of
computational prioritization tools exists, from knowledge-graph link
prediction (e.g. TxGNN [1]) to large-scale systematic efforts (e.g. Every
Cure's MATRIX [2]) and curated gold standards such as repoDB [3]. Yet two
evaluation problems recur. First, retrospective rediscovery benchmarks leak:
the confirmed drug's identity is available to disease-side reasoning unless
explicitly redacted, and "we rediscovered X" claims often cannot be audited
for that leakage. Second, pipelines increasingly ship with self-audit or
trust layers, but those layers are evaluated — if at all — on defect classes
the developers already fixed, a circular design that cannot fail.

We built AgentBio, a drug-repurposing research-prioritization and
evidence-audit system, and subjected it to both disciplines simultaneously:
a pre-registered, frozen rediscovery benchmark with mechanical case selection
and full identity holdout, and a pre-registered, frozen *external* audit
study whose claim set includes defect classes with no prior fix, test, or
trap in the codebase, with exactly one scored run and published thresholds.
We report both instruments' results — including the audit study's failure
and its fixed layer's pre-registered re-validation —
plus the engineering regression suite, the development history, and every
freeze control, so that the evidentiary chain is reproducible end to end.

**Scope.** AgentBio produces research hypotheses and audits evidence chains.
It is not a diagnostic, prescribing, or treatment-recommendation tool, and
nothing in this manuscript is evidence of clinical efficacy.

## 2. System overview

Given a rare or neglected disease, AgentBio: (i) resolves the disease to a
specific, non-umbrella ontology term and ranks genetically and mechanistically
supported targets by tractability and unmet need (two separate, never-blended
scores); (ii) generates approved-drug candidates for a selected target as a
union over evidence lanes — ChEMBL bioactivity, DrugCentral curated
mechanisms (sha256-pinned local snapshot of the official 2023 dump),
GtoPdb/IUPHAR expert-curated interactions, regulatory-label mechanisms
(openFDA/DailyMed), and bounded entity-linked literature (PubTator3 → Europe
PMC); (iii) scores candidates deterministically (affinity, structural
similarity, disease association, prior-trial and safety signals) with
explicit, disclosed caps (mechanism-direction incompatibility, confirmed
withdrawal, unapproved-compound, safety) rather than hidden filtering; and
(iv) writes an auditable dossier disclosing which caps and caveats applied.
Pathway-neighbor targets (Reactome) augment the genetically-associated set
with provenance flags. A separate audit layer answers the inverse question —
*given a specific drug→disease claim, what is wrong with it?* — using
deterministic detectors over the same evidence chain plus regulatory-label
and bounded-literature lanes.

LLMs are used at bounded, enumerated touchpoints. Four classifiers can affect
scoring inputs (mechanism-direction compatibility; withdrawal/black-box
safety classification over web evidence; clinical-trial stop-reason
classification; literature relationship extraction), and three are
constrained narration of precomputed numbers. All benchmark and audit scoring
paths run with narration disabled and are fully deterministic given the
frozen sources; every LLM query during retrospective evaluation is
holdout-redacted by deterministic filters upstream of the model. Full
disclosure, including model identifiers, is in the Supplement.

## 3. Methods

### 3.1 Pre-registration and freeze discipline

Both studies were pre-registered in the repository before the relevant code
or runs existed (benchmark v2: 2026-08-01 with six amendments through
2026-08-08, each committed before the event it governed; audit study:
2026-08-10 with three amendments). Frozen artifacts include the case lists,
claim set (sha256-pinned), raw per-claim audit outputs, scoring code
(code-commit ancestry and file-drift checks), and results (hash-bound so
silent replacement is refused). The benchmark's deployment attestation and
screened case list are held in the immutable published deployment, which
ships without git (a disclosed provenance gap); the executed primary set
recorded in the committed results artifact is the screened list. Each study
allowed exactly one scored run;
the audit study's single pre-registered harness-defect rerun allowance was
exercised once (a plumbing crash *before any metric was computed*), recorded
as Amendment 3, and permanently consumed. All metrics are independently
recomputable from the committed raw archives; we verified exact reproduction.

### 3.2 Benchmark v2: retrospective rediscovery

**Case selection (mechanical, offline-first).** From 9,057 repoDB/DrugCentral
rows: approved, repurposed-success, small-molecule entries (4,866);
development-suite drugs excluded at drug level (4,823); indication in the
Orphanet rare-disease universe (237); one case per drug (111); EFO-resolvable
(103); ≥1 target in the discovery universe (96). Fifty cases were sampled
(seed 20260731, prevalence-stratified, stratum cap 0.4). A pre-registered
property-based screen (`v2-screen-1`: ontology re-resolution with umbrella
guard, ≥1 gated Open Targets target, non-empty production union pool, with
indeterminate-on-lookup-failure semantics) admitted 32 of 50 (64%).
Development-suite cases (n=15) were executed as a clearly labeled development
subset and are never pooled with the primary endpoint.

**Holdout.** Each case's confirmed drug — names, synonyms, salt forms,
active-moiety identifiers, source IDs — is redacted from disease-side inputs
across every lane; matching occurs only after ranking. Errors and out-of-scope
cases are classified, never silently counted as misses.

**Endpoints.** Primary: rediscovery anywhere in the final ranked list, with
95% Clopper–Pearson CI, reported together with the funnel-feasibility (screen
pass) rate. Secondary: top-10 recovery, pre-registered STRONG_MATCH rate,
rank distribution, mechanical miss taxonomy, and the pre-registered
mechanical chance baseline with exact Poisson-binomial test.

### 3.3 Audit claim-set study: external audit validation

**Claim set (frozen).** 100 claims with external, checkable ground truth
predating a 2026-08-10 citation cutoff (mechanical validity rule; pinned
database releases; unverifiable dates excluded at construction): 30 defect
claims from four classes with existing defenses (E1 safety withdrawal, E2
boxed-warning-not-withdrawal, E3 direction incompatibility, E4
unresolved-name honesty), 30 from four novel pre-registered classes (N1
combination-product splitting, N2 biologic modality mis-scope, N3
preclinical-only evidence, N4 dose/route implausibility — no prior fix, test,
or trap; detectors developed against synthetic fixtures only, real instances
sealed until the scored run), and 40 clean controls (32 pool-free, 8
pool-context). N3 closed at zero claims after all externally verifiable
candidates failed construction verification — a registered limitation; its
shortfall was reallocated by the fixed order in the construction protocol.

**Scoring.** Exactly one scored run, gated on four-source health probes
before execution and again before scoring; lane failure states abstain rather
than fabricate; citations revalidated at score time; exact Clopper–Pearson
one-sided 95% bounds. Pre-registered PASS thresholds: defect recall ≥ 0.80
with lower bound ≥ 0.65; control false-flag rate ≤ 0.15 with upper bound
≤ 0.30; novel-class recall reported with CI and no threshold (first
measurement of an unknown quantity). INVALID-DATA if abstention exceeded 10%
of any group.

A second frozen claim set (v2: 100 claims — 60 defect = 1 existing-fix +
59 novel [N1 8, N2 43, N4 8; N3 0, untested under v2's tightened gates] +
40 clean controls) was constructed after the v1 failure, under its own
pre-registration (Amendments 1–4, including registered composition
amendments after two fail-closed construction aborts and a documented
freeze-loss/rebuild event before any scoring) with identical thresholds,
one-scored-run discipline, and scoring code.

### 3.4 Engineering regression suite (separate instrument)

An offline trap benchmark (12 planted defect traps covering the twelve
previously fixed failure classes; 4 clean controls; thresholds ≥0.90 trap
recall, ≤0.25 control false-flag) is run as engineering acceptance only and
is never pooled with the external study.

### 3.5 Development instruments (disclosed, not pooled)

We also report, clearly labeled: the terminated v1 benchmark partial
(14/50 cases); the five-miss engineering acceptance (the archived v1 genuine
misses re-run through production code as regression fixtures); the
pre-freeze source-ablation control (13 development cases × 4 source
conditions, 52 arms); and the earlier repoDB development suites that
motivated the v2 redesign. None of these estimate discovery accuracy; the
benchmark v2 primary endpoint is the only such estimate.

## 4. Results

### 4.1 Benchmark v2 — discovery accuracy

**On funnel-feasible diseases (32 of 50 selected cases passed the screen,
64%), the pipeline rediscovered the confirmed drug in 6 of 22 in-scope
primary cases — 27.3% (95% CI 10.7%–50.2%).** All six rediscoveries ranked
in the top 10 (ranks 3, 4, 4, 5, 5, 8); 2/6 reached STRONG_MATCH. Ten
screened cases were out of scope at runtime (umbrella-term guard),
a screen/runtime disagreement we report as a finding rather than filter
away. The labeled development subset: 2/12 in-scope rediscovered
(ranks 7 and 254), 1 error.

Performance was stratified by prevalence in a way that constitutes a scope
finding: ultra-rare (<1 per million) 6/9; rare (1–10/M) 0/6; less rare
(>10/M) 0/6. The genetic-association core works where monogenic biology is
cleanest and fails elsewhere. The mechanical miss taxonomy assigns all 16
primary misses: 13 wrong-target (in 8, the true target *was* considered but
ranked 4th–10th — a ranking failure, not coverage) and 3
unresolved-no-mechanism (no curated mechanism record exists for the drug).
The 10 development-subset misses are dominated by biologic modality
(enzyme replacements, antibodies) — a structural boundary of the
small-molecule contract.

The pre-registered mechanical chance baseline saturated (per-case probability
1.0, because union-lane reviewed lists cover the recorded per-target pools),
rendering the Poisson-binomial test uninformative; we report it as computed
and identify the baseline construction (pool denominators pre-union) as a
limitation rather than recomputing post hoc.

Against the terminated v1 partial (1/11 scorable, 9.1%), v2's improvement is
consistent with the registered changes — multi-source union candidate
generation (which superseded tiered ChEMBL pools; Amendment 3 item 16) and
pathway-aware target augmentation — but the case sets and denominators
differ, so we report the association, not an attribution. The pre-freeze
ablation control supports the union design on the development corpus:
generated+valid 5/13 (ChEMBL only), 8/13 (+GtoPdb), 10/13 (+DrugCentral),
10/13 (all three). The five-miss engineering acceptance passed 5/5 generated
and mechanistically valid (Top-10 0/5, reported separately) before the
freeze; the artifact of record is the complete five-fixture run (the results
file is a living artifact regenerated by the acceptance workflow).

### 4.2 Audit claim-set study — FAIL, reported as measured

The external audit study **failed both thresholded metrics**: defect recall
32/60 = 0.533 (CP lower 0.420) against ≥0.80/≥0.65; control false-flag rate
7/40 = 0.175 (CP upper 0.304) against ≤0.15/≤0.30. Novel-class recall was
29/30 = 0.967 (CP lower 0.851; no threshold, per registration). Zero claims
abstained or were excluded; no caught defect carried a contradicted safety
disclosure.

Per class (Fig. 4): E1 2/2, E3 1/1; **E2 0/19** — every marketed
boxed-warning drug carried a wrong "withdrawn from market" badge from
persisted candidate pools that predate the classifier fix: a *stale-data
disclosure defect* the audit faithfully surfaced; **E4 0/8** — ChEMBL's
synonym index resolved all eight brand names, falsifying the construction
assumption that they would be unresolvable (a claim-design lesson, not a
product defect); novel classes N1 8/8, N2 13/13, N4 8/9; N3 untested (zero
claims, registered). Of the seven control false-flags, five are the N3
"preclinical-only" finding firing on *approved* drugs in pool-context
controls (a detector precision bug: the bounded literature window is
dominated by preclinical assertions even for approved drugs), and two are N1
multi-ingredient flags on single-ingredient labels.

The contrast with the engineering suite is the point of running both: the
trap benchmark passed 12/12 traps with 1/4 control flags (0.25, at its ≤0.25
boundary) — yet the external study failed. Internal regression suites measure
whether yesterday's fixes still work; they cannot measure whether the audit
catches what nobody anticipated.

### 4.3 Audit claim-set v2 — pre-registered re-validation, PASS

After the v1 failure, the fixes (pool safety refresh, N3 detector
precision, and pre-freeze robustness work: evidence-record boundary
coercion, LLM provider failover under rate-limit pressure) were registered,
and a NEW claim set was constructed, frozen, and scored exactly once under
the same discipline (v2 pre-registration, Amendments 1–4). The fixed layer
**passed both thresholded metrics**: defect recall 60/60 = 1.000 (CP lower
0.951) against ≥0.80/≥0.65; control false-flag rate 2/40 = 0.050 (CP upper
0.149) against ≤0.15/≤0.30. Novel-class recall was 59/59 = 1.000 (CP lower
0.950). Zero claims abstained or were excluded; no caught defect carried a
contradicted safety disclosure. Composition is construction-determined and
disclosed: E = 1 (E2 only; E4 = 0 is a registered finding — brand names
resolve in raw ChEMBL), novel = 59 (N1 8, N2 43, N4 8; N3 0 — untested
under v2's tightened gates), controls = 40. Freeze #1 was destroyed by an
environment restart before any scoring (no results produced or seen; both
allowances unconsumed); the scored set is the registered rebuild under
identical rules (Amendment 3). v1's FAIL is reported alongside this PASS,
never replaced by it.

### 4.4 Development history (labeled; not pooled)

Reviewer pilot (present-day data, pool unredacted, not pre-registered):
1/9 rediscovered, true target considered 8/9. repoDB development suites under
the v1-era pipeline: 1/13 (7.7%) small-molecule top-1. These instruments
motivated and steered the v2 redesign and are reported for provenance only.

## 5. Discussion

Three findings matter beyond this system. **First, audit layers need external
validation as much as discovery cores do.** Our internal suite implied a
strong audit (12/12 traps); the frozen external study measured 0.533 defect
recall and failed. The gap came from places an internal suite cannot reach:
data freshness (stale pools carrying superseded safety labels), construction
assumptions about external resources (brand-name resolvability), and detector
precision on real approved-drug literature windows. **Second, rediscovery
benchmarks should publish their funnel.** A 27.3% hit rate on funnel-feasible
diseases and a 64% screen pass rate are one result, not two; quoting either
alone misleads. The prevalence stratification (6/9 ultra-rare, 0/12
otherwise) is exactly the kind of scope statement that makes a rediscovery
number actionable. **Third, one-scored-run discipline is operationally
expensive but epistemically cheap.** Every freeze control we built was
exercised for real: a health-gate refusal during a 25+ hour upstream outage
(leading to a pinned local snapshot), a harness crash absorbed by the
pre-registered single allowance, and hash-bound results that make the
published outcome immutable.

The honest reading of the audit FAIL is not that the audit layer is useless —
novel-class recall of 0.967 on sealed real instances shows the class-level
defenses generalize — but that its current deployment fails on data freshness
and on one detector's precision. Both are fixable, and both fixes require new
pre-registered studies, not post-hoc re-scoring of this one.

## 6. Limitations

Retrospective rediscovery with disease-side (not pool-side) holdout;
funnel-feasibility selection underrepresenting sparse-data diseases; wide CIs
at n=22 in-scope; a saturated chance baseline; concentration of hits in
ultra-rare monogenic disease; biologics outside the scoring contract; a
first audit study that failed (reported unedited) with its fixed-layer
successor passing — N3 untested in both, E4 measuring a falsified
assumption; single-universe disease scope; DrugCentral pinned to the 2023
release; GtoPdb commercial terms a production dependency; and self-evaluation
by the system's developer, mitigated but not eliminated by pre-registration,
frozen artifacts, mechanical scoring, and external ground truth. No clinical,
experimental, or prospective validation is claimed.

## 7. Conclusions

A pre-registered, frozen evaluation of a drug-repurposing prioritization
pipeline found a functional discovery core on funnel-feasible ultra-rare
disease (27.3% rediscovery on the funnel-feasible subset admitted by the
screen — 32/50, 64%; the pre-registered mechanical chance baseline saturated
and is uninformative, so absolute rates and CIs are the evidence) and — more
importantly — an audit layer whose first frozen external study **failed**
(recall 0.533, false-flag 0.175 — a failure internal regression suites had
masked) and whose registered fixes then **passed** a fresh pre-registered
frozen study (v2: defect recall 60/60 = 1.000, control false-flag
2/40 = 0.050, novel-class 59/59 = 1.000). We publish the full evidentiary
chain for both studies: case lists, claim
sets, raw outputs, freeze manifests, and recomputation scripts. We hope the
instrument designs (screened funnels, novel-class audit claims, one-scored-run
allowances, hash-bound results) are reusable by any team evaluating
trustworthiness claims for computational pipelines.

## Declarations

**Data availability.** All frozen artifacts (benchmark results, case lists,
claim set with sha256, raw audit outputs, freeze manifests, ablation and
engineering-acceptance results, pre-registrations and amendments) are
committed in the project repository; see Supplement for the complete
inventory with hashes. Every figure and table regenerates from the artifacts
via `publication/make_figures.py`; the audit metrics re-verify via
`--recalc-only`.

**Code availability.** [Author decision pending — see publication/README.md
release checklist: public repository or archived snapshot with DOI.]

**AI use.** LLMs are components of the evaluated system at the enumerated
touchpoints (Methods §2, Supplement); all scored paths are deterministic and
narration-free. AI assistance was used in software development and in the
preparation of this manuscript; the author takes full responsibility for the
content.

**Competing interests.** The author develops the evaluated system. No
external funding. No other competing interests.

**Author contributions.** [Author Name]: conceptualization, methodology,
software, validation, formal analysis, writing. (Single author.)

**Ethics.** No human or animal subjects; all data are public databases and
published literature.

## References

1. Huang K, et al. Zero-shot prediction of therapeutic use with geometric deep
   learning (TxGNN). *Nat Med* 2024;30:175–185.
2. Every Cure. MATRIX / REP-KG systematic repurposing platform.
   everycure.org (accessed 2026).
3. Brown AS, Patel CJ. A standard database for drug repositioning (repoDB).
   *Sci Data* 2017;4:170029.
4. Zdrazil B, et al. The ChEMBL Database in 2023. *Nucleic Acids Res* 2024;
   52:D1180–D1192.
5. Avram S, et al. DrugCentral 2023 extends human clinical data. *Nucleic
   Acids Res* 2023;51:D1276–D1287.
6. Harding SD, et al. The IUPHAR/BPS Guide to Pharmacology in 2024. *Nucleic
   Acids Res* 2024;52:D1438–D1449.
7. Ochoa D, et al. Open Targets Platform: supporting systematic drug–target
   identification. *Nucleic Acids Res* 2023;51:D1353–D1359.
8. Orphanet: an online rare disease and orphan drug database.
   orphanet.fr (accessed 2026).
9. Milacic M, et al. The Reactome Pathway Knowledgebase 2024. *Nucleic Acids
   Res* 2024;52:D672–D678.
10. Wei CH, et al. PubTator 3.0. *Nucleic Acids Res* 2024;52:W540–W546.
11. Europe PMC Consortium. Europe PMC in 2023. *Nucleic Acids Res* 2023;
    51:D1526–D1534.
12. ClinicalTrials.gov API v2. clinicaltrials.gov (accessed 2026).
13. openFDA / DailyMed SPL. open.fda.gov; dailymed.nlm.nih.gov (accessed 2026).
14. Clopper CJ, Pearson ES. The use of confidence intervals illustrated.
    *Biometrika* 1934;26:404–413.
15. Wishart DS, et al. DrugBank 6.0. *Nucleic Acids Res* 2025. [cited as
    ground-truth reference only; not ingested]

*Full provenance, per-case and per-claim tables, LLM touchpoint inventory,
source versions, and the complete reproducibility checklist: Supplement.*
