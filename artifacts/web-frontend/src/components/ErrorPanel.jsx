export default function ErrorPanel({ message }) {
  return (
    <div
      className="rounded-lg border mb-6 overflow-hidden fade-in"
      style={{
        borderColor: "rgba(155, 74, 63, 0.4)",
        backgroundColor: "var(--graphite-raised)",
      }}
    >
      <div
        className="flex items-center gap-2 px-5 py-3 border-b"
        style={{
          backgroundColor: "var(--oxide-glow)",
          borderColor: "rgba(155,74,63,0.25)",
        }}
      >
        <span style={{ color: "var(--oxide)", fontSize: "0.7rem" }}>▸</span>
        <div
          className="font-mono text-[0.62rem] uppercase tracking-[0.2em]"
          style={{ color: "var(--oxide)" }}
        >
          Pipeline error
        </div>
      </div>
      <div className="px-5 py-4">
        <p className="mb-3 text-sm leading-relaxed" style={{ color: "var(--silver)" }}>
          The pipeline halted before producing a hypothesis. The raw error is
          recorded below for diagnosis.
        </p>
        <pre
          className="overflow-x-auto whitespace-pre-wrap rounded border p-3 font-mono text-xs leading-relaxed"
          style={{
            borderColor: "rgba(155, 74, 63, 0.25)",
            backgroundColor: "rgba(155, 74, 63, 0.06)",
            color: "var(--oxide)",
          }}
        >
          {message || "No error detail was recorded."}
        </pre>
      </div>
    </div>
  );
}
