import { FEEDBACK_FORM_URL, isFeedbackEnabled } from "../feedback.js";

// Global beta disclaimer. Shown on every view (mounted at App root).
// The "Send feedback" link appears once the form URL is set in src/feedback.js.

export default function BetaBanner() {
  return (
    <div
      role="note"
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap: "0.75rem",
        flexWrap: "wrap",
        padding: "0.45rem 1rem",
        backgroundColor: "var(--brass-glow)",
        borderBottom: "1px solid var(--brass-border)",
        fontSize: "0.78rem",
        lineHeight: 1.4,
        color: "var(--ink-base)",
        textAlign: "center",
      }}
    >
      <span>
        <strong style={{ color: "var(--brass-deep)", letterSpacing: "0.04em" }}>
          BETA
        </strong>
        {" — "}AgentBio is a research preview. Dossiers are machine-generated
        hypotheses for expert review, not medical advice.
      </span>
      {isFeedbackEnabled() && (
        <a
          href={FEEDBACK_FORM_URL}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            color: "var(--brass-deep)",
            fontWeight: 600,
            textDecoration: "underline",
            textUnderlineOffset: "2px",
          }}
        >
          Send feedback →
        </a>
      )}
    </div>
  );
}
