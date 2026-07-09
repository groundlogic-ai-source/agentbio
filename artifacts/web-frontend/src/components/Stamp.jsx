export default function Stamp({ decision }) {
  const rejected = decision === "reject";
  const text = rejected ? "Rejected" : "Strong Match";
  const cls = rejected ? "stamp stamp--rejected" : "stamp stamp--match";
  return (
    <span
      className={cls}
      role="img"
      aria-label={rejected ? "Outcome: rejected" : "Outcome: strong match"}
    >
      {text}
    </span>
  );
}
