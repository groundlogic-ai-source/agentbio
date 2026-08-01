// Global beta disclaimer. Shown on every view (mounted at App root).
// The "Report an issue" link appears once the Google Form URL is set —
// replace the empty string with the published form link.
export const BETA_FEEDBACK_FORM_URL = "";

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
      {BETA_FEEDBACK_FORM_URL && (
        <a
          href={BETA_FEEDBACK_FORM_URL}
          target="_blank"
          rel="noopener noreferrer"
          style={{
            color: "var(--brass-deep)",
            fontWeight: 600,
            textDecoration: "underline",
            textUnderlineOffset: "2px",
          }}
        >
          Report an issue →
        </a>
      )}
    </div>
  );
}
