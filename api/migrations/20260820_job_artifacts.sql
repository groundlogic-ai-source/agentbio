-- Schema-managed development/production migration.  Do not execute from app startup.
CREATE TABLE job_artifacts (
  job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE RESTRICT,
  kind TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  content_sha256 TEXT NOT NULL,
  filename TEXT NOT NULL,
  content_type TEXT NOT NULL,
  size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
  payload BYTEA NOT NULL,
  report_sha256 TEXT NOT NULL,
  candidate_snapshot_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (job_id, kind, artifact_id),
  CHECK (artifact_id ~ '^[a-f0-9]{64}$'),
  CHECK (content_sha256 ~ '^[a-f0-9]{64}$')
);
CREATE UNIQUE INDEX job_artifacts_singleton_kind_uq ON job_artifacts(job_id, kind)
  WHERE kind IN ('report_md', 'report_pdf', 'evidence_zip');
CREATE FUNCTION job_artifacts_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'job_artifacts rows are immutable';
END;
$$;
CREATE TRIGGER job_artifacts_no_update BEFORE UPDATE ON job_artifacts
  FOR EACH ROW EXECUTE FUNCTION job_artifacts_immutable();
CREATE TRIGGER job_artifacts_no_delete BEFORE DELETE ON job_artifacts
  FOR EACH ROW EXECUTE FUNCTION job_artifacts_immutable();