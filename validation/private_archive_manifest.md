# Private-archive manifest — oversized raw artifacts omitted from the public mirror

The public GitHub mirror omits a small number of oversized raw archives (large
LLM checkpoint/output dumps and historical runtime databases). These remain in
the private development archive, byte-identical, and are available to qualified
evaluators on request. SHA-256 hashes below bind the omitted content.

| File (path in private archive) | SHA-256 | What it is |
|---|---|---|
| `validation/triage_discrimination_studyb_checkpoint.jsonl` | `9460f99115ac779bcca985b6b1c0433c9ced7e43ad9f43659c68e98079c19fa9` | Raw per-claim checkpoint stream, triage discrimination Study B |
| `validation/audit_claimset_raw_outputs.json` | `6abde1eac02d8d630cb5b2b9e8ef067da62a5fa8f7a2e742ff58ccce471aabac` | Raw LLM judge outputs, audit claim-set v1 |
| `validation/audit_claimset_v2_raw_outputs.json` | `ef3027c5788954885081ae5abd37b448c3c5f0b9cb9798fb0e0a9233d24f5b3b` | Raw LLM judge outputs, audit claim-set v2 |
| `data_prep/raw/dc_dump.sql.gz` | `055904d152d6c8eef4ee872b25f6476019682df8b5f49bcdf7cc018204f3e04f` | Raw DrugCentral 2023 SQL dump (intermediate; the sha256-pinned processed snapshot `data_sources/drugcentral_2023_snapshot.sqlite` **is** in the mirror) |

Also omitted: historical snapshots of `cache/cache.db` and `checkpoints.db`
(runtime-local SQLite state with no evidentiary content), and the repository's
Git LFS configuration (all LFS-tracked paths are among the omissions above).

All *scored results of record* — the artifacts the freeze manifests pin by
SHA-256 (e.g. `benchmark_results_v2.json`, the audit claim-set and results
files) — **are** present in the public mirror, byte-identical.
