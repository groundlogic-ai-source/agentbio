import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export default function ReportView({ report }) {
  if (!report) {
    return (
      <p className="text-sm" style={{ color: "rgba(42,43,46,0.6)" }}>
        The compiled dossier is not available yet.
      </p>
    );
  }
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
        Beta research preview — this dossier is a machine-generated hypothesis
        for expert review, not medical advice or a treatment recommendation.
      </p>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{report}</ReactMarkdown>
    </div>
  );
}
