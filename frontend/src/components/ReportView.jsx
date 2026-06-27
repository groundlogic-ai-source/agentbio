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
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{report}</ReactMarkdown>
    </div>
  );
}
