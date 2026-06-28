import { useCallback, useEffect, useRef, useState } from "react";
import Dashboard from "./components/Dashboard.jsx";
import CaseView from "./components/CaseView.jsx";
import NewCaseDialog from "./components/NewCaseDialog.jsx";
import {
  listRuns,
  getRun,
  getCost,
  openCase,
  resumeCase,
} from "./api.js";
import { isTerminal } from "./lib/stages.js";

const POLL_MS = 4000;

export default function App() {
  const [runs, setRuns] = useState([]);
  const [view, setView] = useState("dashboard"); // "dashboard" | "case"
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [cost, setCost] = useState(null);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [resuming, setResuming] = useState(false);
  const [error, setError] = useState(null);

  // Refs so the polling interval always reads current values without
  // re-subscribing on every state change.
  const selectedRef = useRef(null);
  const detailRef = useRef(null);
  const runsRef = useRef([]);
  selectedRef.current = selectedId;
  detailRef.current = detail;
  runsRef.current = runs;

  const refreshList = useCallback(async () => {
    const list = await listRuns();
    setRuns(list);
    return list;
  }, []);

  const loadDetail = useCallback(async (jobId) => {
    const [job, costResp] = await Promise.all([
      getRun(jobId),
      getCost(jobId),
    ]);
    setDetail(job);
    setCost(costResp.total_cost_usd);
    return job;
  }, []);

  // Initial load.
  useEffect(() => {
    refreshList().catch((e) => setError(e.message));
  }, [refreshList]);

  // Single polling loop. Only hits the network when something is non-terminal,
  // so a job is no longer polled once it reaches completed/error.
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const sel = selectedRef.current;
        const det = detailRef.current;
        const anyActive = runsRef.current.some((j) => !isTerminal(j.status));
        const detailNeedsPoll =
          sel && (!det || det.job_id !== sel || !isTerminal(det.status));

        if (!anyActive && !detailNeedsPoll) return;

        if (anyActive) await refreshList();
        if (detailNeedsPoll) await loadDetail(sel);
      } catch (e) {
        setError(e.message);
      }
    }, POLL_MS);
    return () => clearInterval(id);
  }, [refreshList, loadDetail]);

  const handleOpenCase = useCallback(
    async (jobId) => {
      setError(null);
      setSelectedId(jobId);
      setDetail(null);
      setCost(null);
      setView("case");
      try {
        await loadDetail(jobId);
      } catch (e) {
        setError(e.message);
      }
    },
    [loadDetail],
  );

  const handleBack = useCallback(() => {
    setView("dashboard");
    setSelectedId(null);
    setDetail(null);
    setCost(null);
    refreshList().catch((e) => setError(e.message));
  }, [refreshList]);

  const handleNewCase = useCallback(
    async (disease) => {
      setBusy(true);
      setError(null);
      try {
        const { job_id } = await openCase(disease);
        setDialogOpen(false);
        await refreshList();
        await handleOpenCase(job_id);
      } catch (e) {
        setError(e.message);
      } finally {
        setBusy(false);
      }
    },
    [refreshList, handleOpenCase],
  );

  const handleResume = useCallback(
    async (action, notes) => {
      if (!selectedRef.current) return;
      setResuming(true);
      setError(null);
      try {
        await resumeCase(selectedRef.current, action, notes);
        await loadDetail(selectedRef.current);
        await refreshList();
      } catch (e) {
        setError(e.message);
      } finally {
        setResuming(false);
      }
    },
    [loadDetail, refreshList],
  );

  return (
    <div className="min-h-full">
      {error && (
        <div
          className="px-4 py-2 text-center font-mono text-xs"
          style={{
            backgroundColor: "rgba(155,74,63,0.18)",
            color: "var(--oxide)",
          }}
          role="alert"
        >
          {error}
        </div>
      )}

      {view === "dashboard" ? (
        <Dashboard
          runs={runs}
          onOpenCase={handleOpenCase}
          onNewCase={() => setDialogOpen(true)}
        />
      ) : (
        <CaseView
          job={detail}
          cost={cost}
          onBack={handleBack}
          onResume={handleResume}
          resuming={resuming}
        />
      )}

      <NewCaseDialog
        open={dialogOpen}
        busy={busy}
        onClose={() => setDialogOpen(false)}
        onOpen={handleNewCase}
      />
    </div>
  );
}
