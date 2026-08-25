import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getHowItWorks } from "../api.js";

// Renders docs/HOW_AGENTBIO_WORKS.md served by the API, so the in-app
// engineering reference and the repo document are always the same file.
export default function HowItWorksTab() {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    getHowItWorks()
      .then((text) => {
        if (live) setDoc(text);
      })
      .catch((e) => {
        if (live) setError(e.message);
      });
    return () => {
      live = false;
    };
  }, []);

  return (
    <div
      className="dossier"
      style={{ maxWidth: "62rem", margin: "0 auto", padding: "1.5rem 1.5rem 3rem" }}
    >
      {error && (
        <p role="alert" style={{ color: "var(--oxide)" }}>
          Could not load the engineering reference: {error}
        </p>
      )}
      {!error && !doc && (
        <p style={{ color: "rgba(42,43,46,0.6)" }}>Loading…</p>
      )}
      {doc && <ReactMarkdown remarkPlugins={[remarkGfm]}>{doc}</ReactMarkdown>}
    </div>
  );
}
