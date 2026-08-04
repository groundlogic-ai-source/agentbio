// ── Papers, preprints, and benchmark releases ────────────────────────────────
//
// Populate this list as write-ups land. While it is empty, the "Methods and
// evidence" section on the landing page renders nothing at all — an empty
// publications shelf looks worse than no shelf.
//
// Each entry:
//   {
//     kind:    "paper" | "preprint" | "benchmark" | "dataset",
//     title:   string,
//     summary: string,        // one or two sentences, plain language
//     url:     string,        // DOI, arXiv, or in-app route
//     date:    "YYYY-MM-DD",  // publication or release date
//     venue:   string,        // optional: journal, server, or "this repository"
//   }

export const PUBLICATIONS = [];

export function hasPublications() {
  return PUBLICATIONS.length > 0;
}
