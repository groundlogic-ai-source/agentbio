import { feedbackUrl, isFeedbackEnabled } from "../feedback.js";

/**
 * Contextual "report an issue" link. Renders nothing until a form URL is
 * configured, so there is never a dead link in the UI.
 *
 * @param {string}  surface  key of FEEDBACK_SURFACES — pre-fills the form
 * @param {string} [context] reference id (job/run/hypothesis) — pre-fills too
 * @param {string} [label]
 */
export default function FeedbackLink({ surface, context, label = "Report an issue with this screen" }) {
  if (!isFeedbackEnabled()) return null;
  return (
    <a
      className="feedback-link"
      href={feedbackUrl({ surface, context })}
      target="_blank"
      rel="noopener noreferrer"
    >
      {label} →
    </a>
  );
}
