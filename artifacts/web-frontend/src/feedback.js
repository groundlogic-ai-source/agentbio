// ── Feedback form configuration ──────────────────────────────────────────────
//
// One place to wire the public feedback form. Everything in the UI that offers
// "send feedback" reads from here, so going live is a one-line change.
//
// SETUP
// -----
// Create a Google Form with a single required long-answer question, publish it,
// and paste the /viewform link below. Nothing else to configure — the link is
// used as-is, so the user lands on one empty box and can just start typing.
//
// If FEEDBACK_FORM_URL is empty, every feedback affordance hides itself — the
// app must never render a dead link.

export const FEEDBACK_FORM_URL = "";

export function isFeedbackEnabled() {
  return Boolean(FEEDBACK_FORM_URL);
}
