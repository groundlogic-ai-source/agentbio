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
      "machine built independently, before it ever saw your list. Two modes: triage a list of " +
      "up to 25, or interrogate a single drug.",
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
    role: "The frozen validation evidence, in one place",
    what:
      "Read-only summaries of AgentBio's completed, frozen benchmark studies — engineering " +
      "acceptance, retrospective repurposing-recovery runs, and the audit-trap detection study — " +
      "each with its methodology, limitations, and (where applicable) provenance verification.",
    give: "Nothing — this is a fixed, historical record, not an interactive tool.",
    get: "Summary cards per study: what was tested, the result, and stated limitations.",
    when: "You want the evidence behind AgentBio's validation claims before trusting a case.",
    notThis:
      "These are retrospective, frozen results — not a live re-run, and not evidence about any " +
      "specific disease or candidate.",
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
    group: "Validation evidence",
    blurb: "The frozen, retrospective studies AgentBio's validation claims are based on.",
    items: [
      ["Research", "Read-only summaries of completed benchmark studies, with methodology and limitations."],
    ],
  },
];

function TabMap() {
  return (
    <section className="tab-map" aria-label="What each tab does">
      <div className="eyebrow">New here</div>
      <h3>Case pipeline, plus the evidence behind it</h3>
      <p className="tab-map-intro">
        AgentBio is a drug-repurposing research system. It generates hypotheses and lets you audit
        them or your own; the Research tab holds the frozen validation studies behind those
        claims. Nothing here is a clinical recommendation. Qualified organizations decide whether
        experiments, translational work, regulatory review, or clinical study are appropriate.
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
