import { useCallback, useEffect, useRef, useState } from "react";
import Dashboard from "./components/Dashboard.jsx";
import CaseView from "./components/CaseView.jsx";
import NewCaseDialog from "./components/NewCaseDialog.jsx";
import ResearchTab from "./components/ResearchTab.jsx";
import SavedReportsTab from "./components/SavedReportsTab.jsx";
import AuditTab from "./components/AuditTab.jsx";
import {
  listRuns,
  getRun,
  getCost,
  openCase,
  resumeCase,
  archiveCase,
  startBatch,
  getBatch,
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

  const [showArchived, setShowArchived] = useState(false);
  const showArchivedRef = useRef(false);
  showArchivedRef.current = showArchived;

  const [batchState, setBatchState] = useState(null);
  const batchPollRef = useRef(null);

  const refreshList = useCallback(async () => {
    const list = await listRuns({ includeArchived: showArchivedRef.current });
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

  const handleArchive = useCallback(
    async (jobId) => {
      setError(null);
      try {
        await archiveCase(jobId);
        await refreshList();
      } catch (e) {
        setError(e.message);
      }
    },
    [refreshList],
  );

  const handleToggleArchived = useCallback(async () => {
    const next = !showArchivedRef.current;
    setShowArchived(next);
    try {
      const list = await listRuns({ includeArchived: next });
      setRuns(list);
    } catch (e) {
      setError(e.message);
    }
  }, []);

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

  const handleBatchScan = useCallback(
    async (n) => {
      setError(null);
      try {
        const result = await startBatch(n);
        setBatchState(result);
        await refreshList();

        // Poll batch progress until done, refreshing the case list each tick
        // so new cases appear as they complete.
        if (batchPollRef.current) clearInterval(batchPollRef.current);
        batchPollRef.current = setInterval(async () => {
          try {
            const progress = await getBatch(result.batch_id);
            setBatchState(progress);
            await refreshList();
            if (progress.status === "done") {
              clearInterval(batchPollRef.current);
              batchPollRef.current = null;
            }
          } catch {
            clearInterval(batchPollRef.current);
            batchPollRef.current = null;
          }
        }, POLL_MS);
      } catch (e) {
        setError(e.message);
      }
    },
    [refreshList],
  );

  const isTopLevel = view === "dashboard" || view === "research" || view === "saved" || view === "audit";

  return (
    <div className="min-h-full">
      {error && (
        <div
          className="px-4 py-2 text-center font-mono text-xs"
          style={{
            backgroundColor: "var(--oxide-glow)",
            color: "var(--oxide)",
            borderBottom: "1px solid var(--oxide-border)",
          }}
          role="alert"
        >
          {error}
        </div>
      )}

      {/* ── Tab navigation: only visible on top-level views ── */}
      {isTopLevel && (
        <nav style={{
          display: "flex", alignItems: "center", gap: "0",
          borderBottom: "1px solid var(--border)",
          backgroundColor: "var(--surface)",
          padding: "0 1.5rem",
        }}>
          {[
            // Two product surfaces, visually grouped: the case pipeline
            // (Case Files + Audit) and the research module (Research + Saved).
            { id: "dashboard", label: "Case Files", group: "case" },
            { id: "audit", label: "Audit", group: "case" },
            { id: "research", label: "Research", group: "research" },
            { id: "saved", label: "Saved Reports", group: "research" },
          ].map(({ id, label, group }, idx) => {
            const active = view === id;
            return (
              <span key={id} style={{ display: "inline-flex", alignItems: "center" }}>
                {idx === 2 && <span className="nav-group-divider" aria-hidden="true" />}
                <button
                  onClick={() => setView(id)}
                  className={`nav-tab nav-tab--${group}${active ? " nav-tab--active" : ""}`}
                >
                  {label}
                </button>
              </span>
            );
          })}
        </nav>
      )}

      {view === "dashboard" && (
        <Dashboard
          runs={runs}
          showArchived={showArchived}
          onOpenCase={handleOpenCase}
          onNewCase={() => setDialogOpen(true)}
          onArchive={handleArchive}
          onToggleArchived={handleToggleArchived}
          onBatchScan={handleBatchScan}
          batchState={batchState}
        />
      )}

      {view === "research" && (
        <ResearchTab />
      )}

      {view === "saved" && (
        <SavedReportsTab />
      )}

      {view === "audit" && (
        <AuditTab
          onNavigate={(target, opts) => {
            if (target === "dashboard") {
              setView("dashboard");
              if (opts?.prefill) setDialogOpen(true);
            }
          }}
        />
      )}

      {view === "case" && (
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
