import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import TherapeuticApplicabilityCaveat from "./TherapeuticApplicabilityCaveat.jsx";
import { hasTherapeuticApplicabilityDisclosure } from "../therapeuticApplicability.js";

function SafeLink({ href, children }) {
  const value = String(href || "");
  const isInternal =
    value.startsWith("/api/structures/") || value.startsWith("/api/runs/");
  const isWeb = /^https?:\/\//i.test(value);
  if (!isInternal && !isWeb) return <span>{children}</span>;
  return (
    <a
      href={value}
      {...(isWeb ? { target: "_blank", rel: "noopener noreferrer" } : {})}
    >
      {children}
    </a>
  );
}

export default function ReportView({ report }) {
  if (!report) {
    return (
      <p className="text-sm" style={{ color: "rgba(42,43,46,0.6)" }}>
        The compiled dossier is not available yet.
      </p>
    );
  }
  const showLegacyApplicabilityCaveat =
    !hasTherapeuticApplicabilityDisclosure(report);

  return (
    <div className="dossier">
      <p
        role="note"
        style={{
          fontSize: "0.78rem",
          color: "var(--brass-deep)",
          backgroundColor: "var(--brass-glow)",
          border: "1px solid var(--brass-border)",
          borderRadius: "4px",
          padding: "0.4rem 0.75rem",
          marginBottom: "1rem",
        }}
      >
        Every figure below is computed by deterministic code from the sources
        cited in the dossier. Section 3 lists the record identifiers.
      </p>
      {showLegacyApplicabilityCaveat && <TherapeuticApplicabilityCaveat />}
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ a: SafeLink }}>
        {report}
      </ReactMarkdown>
    </div>
  );
}
