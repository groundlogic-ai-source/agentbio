-- AgentBio — core table DDL reconstruction
--
-- STATUS: DRAFT, PENDING VALIDATION.
-- Reconstructed 2026-09-19 from (a) a read-only production column/constraint
-- inspection relayed via Replit Agent and (b) the application code in
-- agentbio-private @ 0f7ebde. It has NOT yet been diffed against a real
-- `pg_dump --schema-only` of production.
--
-- KNOWN GAP: the production inspection did not report TRIGGERS or FUNCTIONS.
-- We know at least one such object exists (the job_artifacts immutability
-- triggers in api/migrations/20260820_job_artifacts.sql), which proves the
-- inspection was incomplete. Do NOT treat this file as authoritative until it
-- has been reconciled against the real schema dump.
--
-- SCOPE: only the tables that have NO self-bootstrapping DDL in the app.
--   Already self-creating at startup (do not duplicate here):
--     saved_reports   -> api/saved_reports_db.py
--     triage_runs     -> api/triage_db.py
--   Already has a committed migration (run it separately, AFTER this file):
--     job_artifacts   -> api/migrations/20260820_job_artifacts.sql
--
-- REMOVED 2026-09-21: hypothesis_log, bisociation_history, registry_reset_backup,
-- and research_jobs are gone — the beta research/hypothesis-generation module
-- (never benchmarked, never part of any citable claim) was removed from the
-- app entirely. See CLAUDE.md and docs/HOW_AGENTBIO_WORKS.md.
--
-- ORDER MATTERS: `jobs` must exist before job_artifacts / job_candidate_snapshots
-- because both carry a foreign key to it.

BEGIN;

-- ---------------------------------------------------------------------------
-- jobs — case/run metadata surfaced by the API (api/jobs_db.py)
--
-- NOTE: production carries five columns beyond the _COLUMNS allowlist in
-- jobs_db.py (report_sha256, candidate_snapshot_sha256,
-- reviewer_input_fingerprint, decision_contract_version,
-- report_contract_version). They are written by the artifact/contract layer
-- (api/policy_contracts.py, api/report_pdf.py), not by update_job_status().
--
-- NOTE: `status` is intentionally NOT constrained by a CHECK. Production holds
-- values beyond jobs_db.VALID_STATUSES (degraded_unscorable,
-- no_eligible_candidate, source_unavailable). Adding a CHECK here would reject
-- existing production rows on restore.
--
-- NOTE: timestamps are TEXT, not timestamptz — the app writes
-- time.strftime("%Y-%m-%dT%H:%M:%S") strings and count_jobs_today() does a
-- LIKE 'YYYY-MM-DD%' prefix match against them. Changing the type would break
-- the daily-cap guardrail. Preserved deliberately.
CREATE TABLE IF NOT EXISTS jobs (
    job_id                      TEXT             NOT NULL,
    thread_id                   TEXT             NOT NULL,
    disease_name                TEXT,
    status                      TEXT             NOT NULL,
    current_stage               TEXT,
    created_at                  TEXT             NOT NULL,
    updated_at                  TEXT             NOT NULL,
    error_message               TEXT,
    total_cost_usd              DOUBLE PRECISION NOT NULL DEFAULT 0,
    report_path                 TEXT,
    decision                    TEXT,
    review_notes                TEXT,
    repurposing_only            INTEGER,
    archived                    INTEGER          NOT NULL DEFAULT 0,
    decision_contract_version   TEXT,
    report_contract_version     TEXT,
    report_sha256               TEXT,
    candidate_snapshot_sha256   TEXT,
    reviewer_input_fingerprint  TEXT,
    PRIMARY KEY (job_id)
);

-- ---------------------------------------------------------------------------
-- explored_targets — blank-mode auto-explore ledger (api/jobs_db.py)
--
-- The composite primary key is load-bearing: claim_next_unexplored() relies on
-- INSERT ... ON CONFLICT DO NOTHING RETURNING against it for cross-instance
-- atomic claiming. Do not replace it with a surrogate key.
CREATE TABLE IF NOT EXISTS explored_targets (
    disease_key    TEXT NOT NULL,
    target_key     TEXT NOT NULL,
    disease_name   TEXT,
    target_symbol  TEXT,
    job_id         TEXT,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (disease_key, target_key)
);

-- ---------------------------------------------------------------------------
-- job_candidate_snapshots — per-job frozen reviewed-candidate pool
--
-- ON DELETE CASCADE (unlike job_artifacts' RESTRICT): a snapshot is derived
-- state, an artifact is an evidentiary record.
CREATE TABLE IF NOT EXISTS job_candidate_snapshots (
    job_id       TEXT         NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    payload_json JSONB        NOT NULL,
    created_at   TEXT         NOT NULL,
    updated_at   TEXT         NOT NULL,
    PRIMARY KEY (job_id)
);

COMMIT;

-- ---------------------------------------------------------------------------
-- AFTER this file, run:  api/migrations/20260820_job_artifacts.sql
-- (it creates job_artifacts + its immutability triggers, and FKs to jobs).
--
-- POST-RESTORE VERIFICATION — expected production row counts (pre-removal;
-- hypothesis_log/bisociation_history/registry_reset_backup/research_jobs no
-- longer apply since the research module was removed):
--   jobs 61 | explored_targets 80 | job_artifacts 16 | job_candidate_snapshots 12
--   saved_reports 13 | triage_runs 0
