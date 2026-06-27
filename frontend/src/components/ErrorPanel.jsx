export default function ErrorPanel({ message }) {
  return (
    <div
      className="rounded-md border p-5"
      style={{
        borderColor: "var(--oxide)",
        backgroundColor: "rgba(155, 74, 63, 0.12)",
      }}
    >
      <div
        className="mb-2 font-display text-base font-semibold"
        style={{ color: "var(--oxide)" }}
      >
        This case could not be completed
      </div>
      <p className="mb-3 text-sm" style={{ color: "var(--ink)" }}>
        The pipeline halted before producing a hypothesis. The raw error is
        recorded below for diagnosis.
      </p>
      <pre
        className="overflow-x-auto whitespace-pre-wrap rounded border p-3 font-mono text-xs"
        style={{
          borderColor: "rgba(155, 74, 63, 0.4)",
          backgroundColor: "rgba(155, 74, 63, 0.08)",
          color: "var(--oxide)",
        }}
      >
        {message || "No error detail was recorded."}
      </pre>
    </div>
  );
}
