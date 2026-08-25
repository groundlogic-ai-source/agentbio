import FeedbackLink from "./FeedbackLink.jsx";
import { PUBLICATIONS, hasPublications } from "../publications.js";

// ── Per-tab orientation copy ─────────────────────────────────────────────────
//
// Rendered *below* the working software on every top-level tab, so a researcher
// landing here for the first time can tell what a screen is for without asking.
// Each entry carries an explicit `notThis` line, because the tabs that get
// confused for each other (Case Files vs Audit, Candidates vs Audit) are only
// distinguishable by who supplies the drug names.

export const TAB_GUIDES = {
  dashboard: {
    title: "Case Files",
    role: "Discovery — the machine proposes the drugs",
    what:
      "Give it a disease and it runs the six-stage pipeline — target selection, biologist, " +
      "chemist, reviewer, structure validation, writer — then files a case: a ranked candidate " +
      "pool plus a written dossier for the leading hypotheses. Every case stops at a human " +
      "sign-off checkpoint rather than publishing itself.",
    give: "A disease name, or nothing at all — a batch scan picks unclaimed rare diseases on its own.",
    get:
      "A dossier with evidence, citations, score caps and stated limitations; a persisted, " +
      "auditable candidate pool; a per-run cost.",
    when: "You want new hypotheses for a disease.",
    notThis: "It will not evaluate a list you already have — that is Audit.",
  },
  audit: {
    title: "Audit",
    role: "Verification — you supply the drugs",
    what:
      "Takes drug names you provide and reports where each one already stands in a case the " +
      "machine built independently, before it ever saw your list. Three modes: triage a list of " +
      "up to 25, interrogate a single drug, or re-verify a saved dossier claim by claim.",
    give: "A disease that has a completed case, plus your own drug names.",
    get:
      "A per-drug verdict — in pool, absent, or name unresolved — with rank, composite and " +
      "pre-cap score, the reason for any cap, black-box advisories, XLogP and modality cautions, " +
      "and evidence coverage. Every triage run gets a run id so the exact verdict set can be " +
      "retrieved later.",
    when: "You have a shortlist and want an adversarial second opinion on it.",
    notThis:
      "It never generates candidates. 'Absent from pool' is a statement about the machine's " +
      "reasoning, not evidence that a drug does not work.",
  },
  candidates: {
    title: "Candidates",
    role: "The full pool behind a case",
    what:
      "Browse and filter every candidate a completed case ranked — not just the few that were " +
      "written up in the dossier. Open any drug's evidence ledger to see the normalized source " +
      "records behind it: identifiers, measurements, actions and stated limitations.",
    give: "Pick a case, then filter by safety cap, evidence coverage, XLogP, modality, or free text.",
    get: "The ranked table with caution flags, and a per-drug evidence ledger with source links.",
    when: "You want the long tail below the headline candidates, or want to see what got capped and why.",
    notThis:
      "Scores here are historical outputs of that run. XLogP and modality are disclosure flags — " +
      "they never change rank.",
  },
  research: {
    title: "Research",
    role: "What the system has learned about repurposing in general",
    what:
      "A separate module from the case pipeline. A language model proposes analogical hypotheses " +
      "about what makes drug repurposing succeed, each is compiled into a testable predicate, and " +
      "the predicate is tested against a held-out repoDB outcome dataset under cumulative " +
      "Benjamini–Hochberg false-discovery-rate control across every hypothesis ever tested. " +
      "Findings that survive both discovery and holdout confirmation become disclosure-only base " +
      "rates elsewhere in the app.",
    give: "Nothing — or your own hypothesis, written in plain English.",
    get:
      "A registry entry per hypothesis: effect size and confidence interval, raw and FDR-adjusted " +
      "p-values, confound checks, and a novelty tag.",
    when: "You want population-level context, not an answer about one disease.",
    notThis:
      "These are statistical associations in a retrospective dataset. A confirmed finding is " +
      "context for a reviewer, never a reason to move a candidate up or down.",
  },
  how: {
    title: "How It Works",
    role: "The engineering reference, served from the repo",
    what:
      "The complete architecture document: what each of the six stages computes, which data " +
      "source contributes what (and when it runs), the exact scoring formulas and caps, where " +
      "the AI is allowed to act, and the self-hosting configuration knobs.",
    give: "Nothing — this is the same docs/HOW_AGENTBIO_WORKS.md that ships in the open-source repo.",
    get: "The full pipeline reference, always in sync with the code.",
    when: "You want to know exactly how a number on any other tab was produced.",
    notThis: "It is documentation, not a runnable surface — it never changes data.",
  },
  saved: {
    title: "Saved Reports",
    role: "Pinned write-ups of registry findings",
    what:
      "Full narrative reports generated from a Research hypothesis and kept for later — the " +
      "statistics, the confound analysis, and the limitations in prose.",
    give: "Nothing; this is a shelf for reports you saved from the Research tab.",
    get: "The report as it read when you saved it.",
    when: "You want to revisit or share a finding without regenerating it.",
    notThis: "Reports re-check their gating on every read, so a finding that no longer clears FDR will say so.",
  },
};

function GuideBlock({ guide }) {
  return (
    <section className="tab-guide" aria-label={`About the ${guide.title} tab`}>
      <div className="tab-guide-head">
        <div>
          <div className="eyebrow">About this tab</div>
          <h3>{guide.title}</h3>
          <p className="tab-guide-role">{guide.role}</p>
        </div>
        <FeedbackLink />
      </div>

      <p className="tab-guide-what">{guide.what}</p>

      <dl className="tab-guide-facts">
        <div>
          <dt>You provide</dt>
          <dd>{guide.give}</dd>
        </div>
        <div>
          <dt>You get back</dt>
          <dd>{guide.get}</dd>
        </div>
        <div>
          <dt>Reach for it when</dt>
          <dd>{guide.when}</dd>
        </div>
        <div>
          <dt>What it is not</dt>
          <dd>{guide.notThis}</dd>
        </div>
      </dl>
    </section>
  );
}

// ── Landing-page orientation: how the five tabs relate ───────────────────────

const TAB_MAP = [
  {
    group: "The case pipeline",
    blurb: "One disease at a time. Who names the drugs is what separates these three.",
    items: [
      ["Case Files", "The machine names the drugs. Full pipeline run → dossier."],
      ["Audit", "You name the drugs. Verdicts against a pool built without seeing your list."],
      ["Candidates", "Nobody names the drugs — you browse the entire ranked pool a case produced."],
    ],
  },
  {
    group: "The research module",
    blurb: "Population-level, disease-agnostic. Statistics over thousands of past repurposing outcomes.",
    items: [
      ["Research", "Hypotheses about what makes repurposing succeed, FDR-gated against held-out data."],
      ["Saved Reports", "Write-ups of those findings, pinned for later."],
    ],
  },
];

function TabMap() {
  return (
    <section className="tab-map" aria-label="What each tab does">
      <div className="eyebrow">New here</div>
      <h3>Five tabs, two systems</h3>
      <p className="tab-map-intro">
        AgentBio is a drug-repurposing research system. It generates hypotheses, lets you audit
        them or your own, and separately studies what makes repurposing succeed at all. Nothing
        here is a clinical recommendation; every candidate needs wet-lab validation.
      </p>
      <div className="tab-map-groups">
        {TAB_MAP.map((g) => (
          <div className="tab-map-group" key={g.group}>
            <h4>{g.group}</h4>
            <p className="tab-map-blurb">{g.blurb}</p>
            <dl>
              {g.items.map(([name, desc]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{desc}</dd>
                </div>
              ))}
            </dl>
          </div>
        ))}
      </div>
    </section>
  );
}

// ── Methods and evidence shelf (renders only once populated) ─────────────────

function Publications() {
  if (!hasPublications()) return null;
  return (
    <section className="tab-pubs" aria-label="Methods and evidence">
      <div className="eyebrow">Methods and evidence</div>
      <h3>Papers, benchmarks, and pilot data</h3>
      <ul>
        {PUBLICATIONS.map((p) => (
          <li key={p.url}>
            <a href={p.url} target="_blank" rel="noopener noreferrer">{p.title}</a>
            <span className="tab-pubs-meta">
              {[p.kind, p.venue, p.date].filter(Boolean).join(" · ")}
            </span>
            <p>{p.summary}</p>
          </li>
        ))}
      </ul>
    </section>
  );
}

/**
 * Orientation footer for a top-level tab. Renders below the working UI.
 */
export default function TabGuide({ id }) {
  const guide = TAB_GUIDES[id];
  if (!guide) return null;
  return (
    <div className="tab-guide-wrap no-print">
      {id === "dashboard" && <TabMap />}
      <GuideBlock guide={guide} />
      {id === "dashboard" && <Publications />}
    </div>
  );
}
