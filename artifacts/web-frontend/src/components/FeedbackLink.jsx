import { FEEDBACK_FORM_URL, isFeedbackEnabled } from "../feedback.js";

/**
 * "Send feedback" link. Renders nothing until a form URL is configured, so
 * there is never a dead link in the UI.
 */
export default function FeedbackLink({ label = "Send feedback" }) {
  if (!isFeedbackEnabled()) return null;
  return (
    <a
      className="feedback-link"
      href={FEEDBACK_FORM_URL}
      target="_blank"
      rel="noopener noreferrer"
    >
      {label} →
    </a>
  );
}
