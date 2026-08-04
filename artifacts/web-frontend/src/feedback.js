// ── Feedback form configuration ──────────────────────────────────────────────
//
// One place to wire the public feedback form. Everything in the UI that offers
// "report an issue" reads from here, so going live is a one-line change.
//
// SETUP
// -----
// 1. Publish the Google Form and paste its /viewform link into FEEDBACK_FORM_URL.
// 2. (Optional but recommended) open the form's ⋮ menu → "Get pre-filled link",
//    type a dummy answer into the *Surface*, *Reference id* and *Build* fields,
//    submit, and copy the generated link. It contains `entry.<numeric-id>=dummy`
//    for each field. Paste those numeric entry ids below.
//
// If FEEDBACK_FORM_URL is empty, every feedback affordance hides itself — the
// app must never render a dead link.

export const FEEDBACK_FORM_URL = "";

// Map of logical field name → Google Forms entry id (e.g. "entry.1234567890").
// Any left blank is simply not pre-filled; the form still works.
export const FEEDBACK_FORM_ENTRIES = {
  surface: "",   // which screen the user was on
  context: "",   // free-form reference: job id, run id, hypothesis id, drug…
  build: "",     // app build/version string, for correlating regressions
};

// Human-readable surface names. Keep these in sync with the form's dropdown
// options so responses group cleanly instead of fragmenting into free text.
export const FEEDBACK_SURFACES = {
  dashboard: "Case Files",
  case: "Case dossier",
  audit_triage: "Audit — triage a list",
  audit_single: "Audit — single drug",
  audit_dossier: "Audit — dossiers",
  candidates: "Candidates",
  research: "Research",
  saved: "Saved Reports",
  other: "Other / not sure",
};

export const APP_BUILD = "beta";

export function isFeedbackEnabled() {
  return Boolean(FEEDBACK_FORM_URL);
}

/**
 * Build a feedback URL, pre-filling whichever fields have a configured entry id.
 *
 * @param {object}  opts
 * @param {string} [opts.surface]  key of FEEDBACK_SURFACES
 * @param {string} [opts.context]  reference id or short context string
 * @returns {string} the URL, or "" when no form is configured
 */
export function feedbackUrl({ surface, context } = {}) {
  if (!FEEDBACK_FORM_URL) return "";

  const params = new URLSearchParams();
  const add = (entryId, value) => {
    if (entryId && value) params.set(entryId, String(value));
  };

  add(FEEDBACK_FORM_ENTRIES.surface, surface ? FEEDBACK_SURFACES[surface] || surface : "");
  add(FEEDBACK_FORM_ENTRIES.context, context);
  add(FEEDBACK_FORM_ENTRIES.build, APP_BUILD);

  const query = params.toString();
  if (!query) return FEEDBACK_FORM_URL;

  // Google Forms requires usp=pp_url alongside prefilled entries.
  const joiner = FEEDBACK_FORM_URL.includes("?") ? "&" : "?";
  return `${FEEDBACK_FORM_URL}${joiner}usp=pp_url&${query}`;
}
