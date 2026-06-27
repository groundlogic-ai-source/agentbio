// Pure-CSS hand-stamped outcome mark. "STRONG MATCH" when the case was approved
// at the human checkpoint, "REJECTED" when it was rejected.
export default function Stamp({ decision }) {
  const rejected = decision === "reject";
  const text = rejected ? "Rejected" : "Strong Match";
  const cls = rejected ? "stamp stamp--rejected" : "stamp stamp--match";
  return (
    <div
      className={cls}
      role="img"
      aria-label={rejected ? "Stamped: rejected" : "Stamped: strong match"}
    >
      <span>{text}</span>
    </div>
  );
}
