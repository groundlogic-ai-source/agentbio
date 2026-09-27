import { FEEDBACK_FORM_URL, isFeedbackEnabled } from "../feedback.js";

// Feedback affordance, mounted at App root. Renders nothing when no form URL
// is configured in src/feedback.js.
//
// This replaced a global BETA disclaimer banner. The audience for a dossier
// verifies its numbers against the cited records, so a standing notice that
// the output is "not medical advice" told them nothing they would not already
// apply and asserted something the dossier does not claim.

export default function FeedbackBar() {
  if (!isFeedbackEnabled()) return null;
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
    </div>
  );
}
