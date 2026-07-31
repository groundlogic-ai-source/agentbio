export default function ErrorPanel({ message }) {
  return (
    <div
      className="rounded-lg border mb-6 overflow-hidden fade-in"
      style={{
        borderColor: "var(--oxide-border)",
        backgroundColor: "var(--surface)",
      }}
    >
      <div
        className="flex items-center gap-2 px-5 py-3 border-b"
        style={{
          backgroundColor: "var(--oxide-glow)",
          borderColor: "var(--oxide-border)",
        }}
      >
        <span style={{ color: "var(--oxide)", fontSize: "0.7rem" }}>▸</span>
        <div
          className="font-mono text-[0.62rem] uppercase tracking-[0.14em]"
          style={{ color: "var(--oxide)" }}
        >
          Pipeline error
        </div>
      </div>
      <div className="px-5 py-4">
        <p className="mb-3 text-sm leading-relaxed" style={{ color: "var(--ink-muted)" }}>
          The pipeline halted before producing a hypothesis. The raw error is
          recorded below for diagnosis.
        </p>
        <pre
          className="overflow-x-auto whitespace-pre-wrap rounded border p-3 font-mono text-xs leading-relaxed"
          style={{
            borderColor: "var(--oxide-border)",
            backgroundColor: "var(--oxide-glow)",
            color: "var(--oxide)",
          }}
        >
          {message || "No error detail was recorded."}
        </pre>
      </div>
    </div>
  );
}
