"""Audit/triage parity tests for the shared N1–N4 disclosure contract."""
from __future__ import annotations

import unittest
from unittest import mock

from api import audit
from api import triage


_CONTEXT = {
    "contract_version": "audit-context-v1",
    "purpose": "research_evidence_audit",
    "effect": "disclosure_only",
    "sources": {
        "regulatory_label": {"status": "ok"},
        "entity_linked_literature": {"status": "filtered_empty"},
    },
    "findings": [{
        "code": "N1",
        "status": "flagged",
        "effect": "disclosure_only",
    }],
}


class AuditContextWiringTest(unittest.TestCase):
    @mock.patch.object(audit, "build_audit_context", return_value=_CONTEXT)
    @mock.patch.object(audit, "_modality_payload", return_value={
        "chembl_molecule_type": "Small molecule",
        "chembl_oral": True,
        "modality_findings": [],
        "modality_status": "clear",
    })
    @mock.patch.object(audit.jobs_db, "find_completed_job_by_disease",
                       return_value=None)
    def test_no_case_still_exposes_structured_context(
        self, find_job, modality, build,
    ):
        result = audit.run_audit(
            "Development syndrome", "Velunadine", narrate=False)
        self.assertEqual(result["status"], "no_case")
        self.assertIs(result["audit_context"], _CONTEXT)
        build.assert_called_once_with(
            "Velunadine",
            mechanism_symbol="",
            claimed_route="",
            claimed_dose="",
            claimed_modality="",
            claimed_context="",
            deadline_monotonic=mock.ANY,
        )

    @mock.patch.object(audit, "build_audit_context", return_value=_CONTEXT)
    @mock.patch.object(audit, "_modality_payload", return_value={})
    @mock.patch.object(audit.jobs_db, "find_completed_job_by_disease",
                       return_value=None)
    def test_production_audit_path_forwards_claim_context(
        self, find_job, modality, build,
    ):
        audit.run_audit(
            "Development syndrome",
            "Velunadine",
            narrate=False,
            claimed_route="oral",
            claimed_dose="10 mg daily",
            claimed_modality="small molecule",
            claimed_context="systemic plasma exposure",
        )
        build.assert_called_once_with(
            "Velunadine",
            mechanism_symbol="",
            claimed_route="oral",
            claimed_dose="10 mg daily",
            claimed_modality="small molecule",
            claimed_context="systemic plasma exposure",
            deadline_monotonic=mock.ANY,
        )

    def test_triage_carries_exact_single_audit_object_and_source_states(self):
        single = {
            "status": "unresolved",
            "drug_name": "Velunadine",
            "audit_context": _CONTEXT,
        }
        saved = {"id": "development-run", "created_at": None}
        with mock.patch.object(triage, "run_audit", return_value=single), \
             mock.patch.object(
                 triage.triage_db, "save_triage_run", return_value=saved):
            result = triage.run_triage(
                "Development syndrome", ["Velunadine"])

        verdict = result["verdicts"][0]
        self.assertIs(verdict["audit_context"], _CONTEXT)
        self.assertEqual(
            result["summary"]["audit_context_contract_version"],
            "audit-context-v1",
        )
        self.assertEqual(
            result["summary"]["audit_context_source_states"]["Velunadine"],
            {
                "regulatory_label": "ok",
                "entity_linked_literature": "filtered_empty",
            },
        )
        self.assertEqual(verdict["status"], "unresolved")
        self.assertEqual(verdict["flags"], ["UNRESOLVED_NAME"])

    def test_triage_forwards_per_drug_context_and_shared_deadline(self):
        single = {
            "status": "unresolved",
            "drug_name": "Velunadine",
            "audit_context": _CONTEXT,
        }
        saved = {"id": "development-run", "created_at": None}
        with mock.patch.object(
                triage, "run_audit", return_value=single) as run_audit, \
             mock.patch.object(
                 triage.triage_db, "save_triage_run", return_value=saved):
            triage.run_triage(
                "Development syndrome",
                ["Velunadine"],
                claim_contexts={
                    "Velunadine": {
                        "route": "oral",
                        "dose": "10 mg daily",
                        "modality": "small molecule",
                        "context": "systemic plasma exposure",
                    },
                },
            )
        kwargs = run_audit.call_args.kwargs
        self.assertEqual(kwargs["claimed_route"], "oral")
        self.assertEqual(kwargs["claimed_dose"], "10 mg daily")
        self.assertEqual(kwargs["claimed_modality"], "small molecule")
        self.assertEqual(kwargs["claimed_context"], "systemic plasma exposure")
        self.assertIsInstance(kwargs["source_deadline_monotonic"], float)


if __name__ == "__main__":
    unittest.main()