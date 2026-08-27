import { useState } from "react";
import { useModalityMode } from "../modalityMode";

// Toggle for the disengageable modality base-rate mode, with an ⓘ explainer
// panel (what it is, why it exists, the statistical basis of the finding, and
// what it never does). Disclosure-only: switching it never changes scores,
// ranks, caps, or persisted data — only whether the non-oral-biologic caution
// is shown. The explainer text is static on purpose: it describes the finding
// and the process, while the live q-value (recomputed at read time) lives on
// the Research tab, which the panel links to.
export default function ModalityModeToggle() {
  const [engaged, setEngaged] = useModalityMode();
  const [showInfo, setShowInfo] = useState(false);
  return (
    <div style={{ display: "inline-block", maxWidth: "36rem" }}>
      <div style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem" }}>
        <label style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem", cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={engaged}
            onChange={(e) => setEngaged(e.target.checked)}
          />
          <span
            style={{
              fontFamily: "monospace", fontSize: "0.61rem", textTransform: "uppercase",
              letterSpacing: "0.11em", color: "var(--ink-dim)",
            }}
          >
            Modality base-rate mode
          </span>
        </label>
        <button
          type="button"
          onClick={() => setShowInfo((v) => !v)}
          aria-expanded={showInfo}
          aria-label="What is modality base-rate mode?"
          title="What is this mode, and where does the caution come from?"
          style={{
            fontFamily: "monospace", fontSize: "0.6rem", lineHeight: 1,
            width: "1.05rem", height: "1.05rem", borderRadius: "50%",
            border: "1px solid rgba(184,151,90,0.45)", backgroundColor: "transparent",
            color: "var(--ink-dim)", cursor: "pointer", padding: 0,
          }}
        >
          i
        </button>
      </div>
      {showInfo && (
        <div
          style={{
            marginTop: "0.5rem", padding: "0.7rem 0.85rem",
            border: "1px solid rgba(184,151,90,0.3)", borderRadius: "6px",
            fontSize: "0.72rem", lineHeight: 1.6, color: "var(--ink-muted)",
            backgroundColor: "rgba(184,151,90,0.05)",
          }}
        >
          <p style={{ margin: 0 }}>
            <strong style={{ color: "var(--ink)" }}>What it does.</strong> When on, candidates that are{" "}
            <em>non-oral biologics</em> (antibodies, peptides, and other injectables) carry a caution flag
            on the Candidates, Triage, and Audit surfaces.
          </p>
          <p style={{ margin: "0.55rem 0 0" }}>
            <strong style={{ color: "var(--ink)" }}>Why it exists.</strong> AgentBio's research module
            found, in a retrospective dataset of real-world repurposing outcomes (repoDB), that non-oral
            biologics were repurposed at roughly <strong>0.3&times; the odds</strong> of other drugs.
            The historical record is dominated by oral small molecules, and injectable biologics face
            harder adoption barriers in new indications. It is a base rate about history — not a judgment
            about any specific drug.
          </p>
          <p style={{ margin: "0.55rem 0 0" }}>
            <strong style={{ color: "var(--ink)" }}>How the finding earned its place.</strong> The
            hypothesis was proposed by a model but tested entirely in code: first on a discovery half of
            the dataset, with the p-value FDR-corrected (Benjamini&ndash;Hochberg) against the cumulative
            family of every hypothesis ever tested; then again on a held-out confirmation half; then
            through an automated label-confounding check. It passed all three (finding{" "}
            <code>run-704c0cb4-H05</code>, &ldquo;double pass&rdquo;). Because the correction is
            recomputed over the whole family at read time, the q-value can drift as new hypotheses are
            tested — the live numbers are on the <strong>Research</strong> tab.
          </p>
          <p style={{ margin: "0.55rem 0 0" }}>
            <strong style={{ color: "var(--ink)" }}>What it never does.</strong> The flag never changes a
            score, rank, cap, or verdict. It is disclosure-only context. Uncheck the box to hide it.
          </p>
        </div>
      )}
    </div>
  );
}
