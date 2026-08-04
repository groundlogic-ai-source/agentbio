// Disengageable modality base-rate mode (registry finding run-704c0cb4-H05:
// non-oral biologics have ~0.30x odds of repurposing success). The mode is
// disclosure-only — engaging/disengaging never changes scores, ranks, caps,
// or persisted data, only whether modality cautions are displayed.
import { useEffect, useState } from "react";

const KEY = "agentbio.modality-mode";
const EVENT = "agentbio:modality-mode-changed";

export function isModalityModeEngaged() {
  try {
    const v = window.localStorage.getItem(KEY);
    return v === null ? true : v === "engaged";
  } catch {
    return true; // storage unavailable — default to engaged
  }
}

export function setModalityModeEngaged(engaged) {
  try {
    window.localStorage.setItem(KEY, engaged ? "engaged" : "disengaged");
  } catch {
    /* storage unavailable — session-only toggle */
  }
  window.dispatchEvent(new CustomEvent(EVENT, { detail: { engaged } }));
}

export function useModalityMode() {
  const [engaged, setEngaged] = useState(isModalityModeEngaged);
  useEffect(() => {
    const onChange = () => setEngaged(isModalityModeEngaged());
    window.addEventListener(EVENT, onChange);
    return () => window.removeEventListener(EVENT, onChange);
  }, []);
  return [engaged, setModalityModeEngaged];
}
