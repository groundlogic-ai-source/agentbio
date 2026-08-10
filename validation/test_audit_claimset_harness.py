"""
Synthetic-only tests for the sealed audit claim-set harness
(validation/run_audit_claimset.py).

Every test uses hand-built claims/outputs and mocked I/O — no live network,
no real claim-set data, no pool files. Run via:
    python3 -m unittest validation.test_audit_claimset_harness -v
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from unittest import mock

import validation.run_audit_claimset as H


def _claim(group, defect_class, expected, cid="X-01", drug="TESTDRUG",
           citation=None):
    return {
        "claim_id": cid,
        "group": group,
        "defect_class": defect_class,
        "input": {"disease_name": "Some Disease", "drug_name": drug,
                  "claim": {"route": "oral"}},
        "truth": {"expected": expected,
                  "citation": citation or {
                      "source": "fda_label", "identifier": "abc-123",
                      "artifact_date": "2026-01-01"},
                  "note": "synthetic"},
    }


def _ctx(findings=None, reg_status="ok", lit_status="empty"):
    return {"audit_context": {
        "sources": {"regulatory_label": {"status": reg_status},
                    "entity_linked_literature": {"status": lit_status}},
        "findings": findings or []}}


# --------------------------------------------------------------------------- #
# Clopper-Pearson anchors (hand-computed via scipy.stats.beta identities)
# --------------------------------------------------------------------------- #

class TestClopperPearson(unittest.TestCase):
    def test_degenerate_edges(self):
        self.assertEqual(H.cp_lower_95(0, 40), 0.0)
        self.assertEqual(H.cp_upper_95(40, 40), 1.0)
        self.assertAlmostEqual(H.cp_lower_95(60, 60), 0.05 ** (1 / 60),
                               places=12)
        self.assertAlmostEqual(H.cp_upper_95(0, 40), 1 - 0.05 ** (1 / 40),
                               places=12)

    def test_hand_computed_midpoints(self):
        # Values computed independently via scipy beta quantiles.
        self.assertAlmostEqual(H.cp_lower_95(48, 60), 0.6961902699846472,
                               places=12)
        self.assertAlmostEqual(H.cp_upper_95(6, 40), 0.27474450501397046,
                               places=12)
        self.assertAlmostEqual(H.cp_lower_95(24, 30), 0.6429908854383468,
                               places=12)

    def test_empty_denominator_guards(self):
        self.assertEqual(H.cp_lower_95(0, 0), 0.0)
        self.assertEqual(H.cp_upper_95(0, 0), 1.0)


# --------------------------------------------------------------------------- #
# Mechanical scoring rules (protocol §6)
# --------------------------------------------------------------------------- #

class TestClassifyClaim(unittest.TestCase):
    def test_e1_caught(self):
        claim = _claim("existing_fix", "E1_safety_withdrawal",
                       {"status": "found", "cap_applied": True,
                        "cap_reason_contains": "Safety cap"})
        out = {"status": "found", "cap_applied": True,
               "cap_reason": "Safety cap (hard gate, max 0.400) — WITHDRAWN"}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "caught")

    def test_e1_miss_when_cap_reason_wrong(self):
        claim = _claim("existing_fix", "E1_safety_withdrawal",
                       {"status": "found", "cap_applied": True,
                        "cap_reason_contains": "Safety cap"})
        out = {"status": "found", "cap_applied": True,
               "cap_reason": "Mechanism-direction cap (max 0.400)"}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "miss")

    def test_e2_caught(self):
        claim = _claim("existing_fix", "E2_boxed_warning_not_withdrawal",
                       {"status": "found", "black_box_advisory": True,
                        "safety_cap_applied": False})
        out = {"status": "found",
               "candidate": {"black_box_advisory": True,
                             "safety_cap_applied": False}}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "caught")

    def test_e2_miss_when_safety_capped(self):
        # The stale-pool failure mode: boxed-warning drug wrongly safety-capped
        claim = _claim("existing_fix", "E2_boxed_warning_not_withdrawal",
                       {"status": "found", "black_box_advisory": True,
                        "safety_cap_applied": False})
        out = {"status": "found",
               "candidate": {"black_box_advisory": True,
                             "safety_cap_applied": True}}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "miss")

    def test_e3_caught(self):
        claim = _claim("existing_fix", "E3_direction_incompatible",
                       {"status": "found",
                        "cap_reason_contains": "Mechanism-direction cap"})
        out = {"status": "found",
               "cap_reason": "Mechanism-direction cap "
                             "(DIRECTIONALLY_INCOMPATIBLE, max 0.400)"}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "caught")

    def test_e4_unresolved_honesty(self):
        claim = _claim("existing_fix", "E4_unresolved_name_honesty",
                       {"status": "unresolved"})
        self.assertEqual(
            H.classify_claim(claim, {"status": "unresolved"})["outcome"],
            "caught")
        # the defect: brand name falsely reported ABSENT
        self.assertEqual(
            H.classify_claim(claim, {"status": "absent"})["outcome"], "miss")

    def test_n_class_flagged_caught(self):
        claim = _claim("novel", "N1_combination_product_splitting",
                       {"finding": {"code": "N1", "status": "flagged"}})
        out = {"status": "no_case",
               **_ctx(findings=[{"code": "N1", "status": "flagged"}])}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "caught")

    def test_n_class_review_is_never_a_flag(self):
        claim = _claim("novel", "N4_dose_route_implausibility",
                       {"finding": {"code": "N4", "status": "flagged"}})
        out = {"status": "no_case",
               **_ctx(findings=[{"code": "N4", "status": "review"}])}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "miss")

    def test_n_class_lane_failure_abstains(self):
        claim = _claim("novel", "N1_combination_product_splitting",
                       {"finding": {"code": "N1", "status": "flagged"}})
        out = {"status": "no_case", **_ctx(reg_status="degraded")}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "abstain")

    def test_n3_abstains_on_literature_lane_failure(self):
        claim = _claim("novel", "N3_species_preclinical_only",
                       {"finding": {"code": "N3", "status": "flagged"}})
        out = {"status": "found",
               **_ctx(lit_status="parse_failed")}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "abstain")

    def test_harness_exception_abstains(self):
        claim = _claim("novel", "N2_biologic_modality_mis_scope",
                       {"finding": {"code": "N2", "status": "flagged"}})
        out = {"status": "__harness_exception__", "error": "TimeoutError"}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "abstain")

    def test_control_clean(self):
        claim = _claim("control", "none", {"no_finding_flagged": True})
        out = {"status": "no_case",
               **_ctx(findings=[{"code": "N4", "status": "review"},
                                {"code": "N1", "status": "unresolved"}])}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "clean")

    def test_control_false_flag(self):
        claim = _claim("control", "none", {"no_finding_flagged": True})
        out = {"status": "no_case",
               **_ctx(findings=[{"code": "N2", "status": "flagged"}])}
        self.assertEqual(H.classify_claim(claim, out)["outcome"],
                         "false_flag")

    def test_control_abstain_on_lane_failure(self):
        claim = _claim("control", "none", {"no_finding_flagged": True})
        out = {"status": "no_case", **_ctx(reg_status="unavailable")}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "abstain")

    def test_control_empty_literature_is_not_a_failure(self):
        # pool-free controls never query literature; status "empty" is the
        # healthy not-queried envelope and must NOT trigger abstention.
        claim = _claim("control", "none", {"no_finding_flagged": True})
        out = {"status": "no_case", **_ctx(lit_status="empty")}
        self.assertEqual(H.classify_claim(claim, out)["outcome"], "clean")


# --------------------------------------------------------------------------- #
# Disclosure annotation (non-scored, protocol §7)
# --------------------------------------------------------------------------- #

class TestDisclosureAnnotation(unittest.TestCase):
    def test_e2_contradicted_by_stale_withdrawn_badge(self):
        claim = _claim("existing_fix", "E2_boxed_warning_not_withdrawal",
                       {"status": "found", "black_box_advisory": True,
                        "safety_cap_applied": False})
        out = {"status": "found",
               "candidate": {"black_box_advisory": True,
                             "safety_cap_applied": False,
                             "status_badge": "WITHDRAWN FROM MARKET"}}
        ann = H.annotate_disclosure(claim, out, "caught")
        self.assertEqual(ann["annotation"], "contradicted")

    def test_e2_consistent(self):
        claim = _claim("existing_fix", "E2_boxed_warning_not_withdrawal",
                       {"status": "found"})
        out = {"status": "found",
               "candidate": {"status_badge": "BLACK BOX WARNING"}}
        ann = H.annotate_disclosure(claim, out, "caught")
        self.assertEqual(ann["annotation"], "consistent")

    def test_non_caught_is_not_applicable(self):
        claim = _claim("existing_fix", "E2_boxed_warning_not_withdrawal",
                       {"status": "found"})
        ann = H.annotate_disclosure(claim, {"status": "absent"}, "miss")
        self.assertEqual(ann["annotation"], "not_applicable")


# --------------------------------------------------------------------------- #
# Metrics: verdicts, abstention breach, exclusions
# --------------------------------------------------------------------------- #

def _group_claims(group, n, defect_class, expected):
    return [_claim(group, defect_class, expected, cid=f"{group}-{i:02d}",
                   drug=f"DRUG{i}")
            for i in range(n)]


class TestComputeMetrics(unittest.TestCase):
    def _run(self, outcome_by_group):
        """outcome_by_group: {group: (caught_or_flagged, abstain, excluded)}"""
        claims, outcomes = [], {}
        for group, (pos, abst, exc) in outcome_by_group.items():
            total = H.GROUP_TOTALS[group]
            dc = "none" if group == "control" else "E1_safety_withdrawal"
            exp = ({"no_finding_flagged": True} if group == "control"
                   else {"status": "found"})
            cs = _group_claims(group, total, dc, exp)
            claims += cs
            pos_out = "false_flag" if group == "control" else "caught"
            neg_out = "clean" if group == "control" else "miss"
            seq = ([{"outcome": pos_out, "citation": "valid"}] * pos
                   + [{"outcome": "abstain", "citation": "valid",
                       "reason": "synthetic"}] * abst
                   + [{"outcome": neg_out, "citation": "invalid"}] * exc)
            seq += [{"outcome": neg_out, "citation": "valid"}] * (
                total - len(seq))
            for c, o in zip(cs, seq):
                outcomes[c["claim_id"]] = o
        return H.compute_metrics(claims, outcomes)

    def test_pass_at_threshold(self):
        # 48/60 caught, 6/40 flagged — both at the registered operating point
        m = self._run({"existing_fix": (24, 0, 0), "novel": (24, 0, 0),
                       "control": (6, 0, 0)})
        self.assertEqual(m["verdict"], "PASS")
        self.assertAlmostEqual(m["defect_recall"], 0.80)
        self.assertAlmostEqual(m["control_false_flag_rate"], 0.15)

    def test_fail_below_recall(self):
        m = self._run({"existing_fix": (20, 0, 0), "novel": (20, 0, 0),
                       "control": (2, 0, 0)})
        self.assertEqual(m["verdict"], "FAIL")

    def test_fail_on_control_flags_even_with_perfect_recall(self):
        m = self._run({"existing_fix": (30, 0, 0), "novel": (30, 0, 0),
                       "control": (7, 0, 0)})
        self.assertEqual(m["verdict"], "FAIL")

    def test_invalid_data_on_abstention_breach(self):
        # 4/30 abstentions in existing_fix (>10%) -> INVALID-DATA
        m = self._run({"existing_fix": (26, 4, 0), "novel": (28, 2, 0),
                       "control": (3, 2, 0)})
        self.assertEqual(m["verdict"], "INVALID-DATA")

    def test_citation_invalid_excluded_from_denominators(self):
        m = self._run({"existing_fix": (26, 0, 4), "novel": (24, 0, 6),
                       "control": (6, 0, 4)})
        self.assertEqual(m["defect_counts"]["eligible"], 50)
        self.assertAlmostEqual(m["defect_recall"], 50 / 50)
        self.assertEqual(m["control_counts"]["eligible"], 36)

    def test_fixed_denominator_counts_abstentions_as_not_caught(self):
        m = self._run({"existing_fix": (27, 3, 0), "novel": (27, 3, 0),
                       "control": (5, 0, 0)})
        # eligible = 54, recall = 54/54 = 1.0; fixed = 54/60 = 0.9
        self.assertAlmostEqual(m["defect_recall"], 1.0)
        self.assertAlmostEqual(m["defect_recall_fixed_denominator"], 0.9)


# --------------------------------------------------------------------------- #
# Leakage guard: claim truth never reaches the audit path
# --------------------------------------------------------------------------- #

class TestLeakageGuard(unittest.TestCase):
    def test_only_input_fields_reach_run_audit(self):
        sentinel = "TRUTH_SENTINEL_NEVER_LEAK"
        claim = _claim("novel", "N1_combination_product_splitting",
                       {"finding": {"code": "N1", "status": "flagged"}},
                       citation={"source": "fda_label",
                                 "identifier": sentinel,
                                 "artifact_date": "2026-01-01"})
        claim["input"]["job_id_hint"] = "job123"
        captured = {}

        def fake_run_audit(**kwargs):
            captured.update(kwargs)
            return {"status": "no_case"}

        with mock.patch("api.audit.run_audit", side_effect=fake_run_audit):
            H.run_one_claim(claim)

        allowed = {"disease_name", "drug_name", "narrate", "job_id_hint",
                   "claimed_route", "claimed_dose", "claimed_modality",
                   "claimed_context", "source_deadline_monotonic"}
        self.assertLessEqual(set(captured), allowed)
        self.assertFalse(captured.get("narrate", True))
        self.assertNotIn(sentinel, json.dumps(captured))
        self.assertEqual(captured["job_id_hint"], "job123")
        self.assertEqual(captured["claimed_route"], "oral")

    def test_run_one_claim_exception_becomes_abstain_marker(self):
        with mock.patch("api.audit.run_audit",
                        side_effect=TimeoutError("boom")):
            out = H.run_one_claim(_claim("control", "none",
                                         {"no_finding_flagged": True}))
        self.assertEqual(out["status"], "__harness_exception__")
        self.assertIn("TimeoutError", out["error"])


# --------------------------------------------------------------------------- #
# Gates: label, freeze, health, idempotency
# --------------------------------------------------------------------------- #

class TestGates(unittest.TestCase):
    def test_label_guard_refuses_wrong_label(self):
        argv = ["prog", "--label", "benchmark_v2"]
        with mock.patch.object(sys := __import__("sys"), "argv", argv):
            with self.assertRaises(SystemExit) as cm:
                H.main()
        self.assertEqual(cm.exception.code, 2)

    def test_health_gate_refuses_on_probe_failure(self):
        def fake_get(url, params=None):
            if "fda.gov" in url:
                return None
            return {"ok": True}
        with mock.patch.object(H, "_get_json", side_effect=fake_get), \
                mock.patch.object(H.time, "sleep", lambda *_: None):
            with self.assertRaises(SystemExit) as cm:
                H.health_gate()
        self.assertEqual(cm.exception.code, 2)

    def test_health_gate_passes_when_all_probes_ok(self):
        with mock.patch.object(H, "_get_json", return_value={"ok": 1}), \
                mock.patch.object(H.time, "sleep", lambda *_: None):
            result = H.health_gate()
        self.assertTrue(all(result.values()))
        self.assertEqual(set(result), {"chembl", "openfda", "europepmc",
                                       "pubtator"})


class TestFreezeAndIdempotency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = self.tmp.name
        self.claim_set = os.path.join(d, "claim_set.json")
        self.manifest = os.path.join(d, "manifest.json")
        self.results = os.path.join(d, "results.json")
        json.dump({"claims": [], "pools_used_for_reachability": {}},
                  open(self.claim_set, "w"))

    def _patch_paths(self):
        return [mock.patch.object(H, "CLAIM_SET_JSON", self.claim_set),
                mock.patch.object(H, "FREEZE_MANIFEST_JSON", self.manifest),
                mock.patch.object(H, "RESULTS_JSON", self.results)]

    def test_missing_manifest_refuses(self):
        patches = self._patch_paths()
        with patches[0], patches[1]:
            with self.assertRaises(SystemExit):
                H.verify_freeze()

    def test_claim_set_hash_drift_refuses(self):
        json.dump({"claim_set_file_sha256": "0" * 64,
                   "code_commit": "0" * 40}, open(self.manifest, "w"))
        patches = self._patch_paths()
        with patches[0], patches[1]:
            with self.assertRaises(SystemExit):
                H.verify_freeze()

    def test_idempotency_refuses_second_run(self):
        json.dump({"verdict": "PASS"}, open(self.results, "w"))
        patches = self._patch_paths()
        argv = ["prog", "--label", "audit_claimset_v1"]
        with patches[0], patches[1], patches[2], \
                mock.patch.object(__import__("sys"), "argv", argv), \
                mock.patch.object(H, "verify_freeze",
                                  return_value={"claim_set_file_sha256": "x",
                                                "code_commit": "y"}):
            with self.assertRaises(SystemExit) as cm:
                H.main()
        self.assertEqual(cm.exception.code, 2)

    def test_recalc_only_is_exempt_from_idempotency(self):
        """The independent recalculation path exists to verify a COMPLETED
        run; it must not be blocked by the re-run guard (regression: first
        recalc attempt was refused by the idempotency check)."""
        import argparse
        args = argparse.Namespace(label=H.REQUIRED_LABEL, recalc_only=True,
                                  allow_rerun_after_harness_defect="")
        json.dump({"metrics": {"verdict": "PASS"}}, open(self.results, "w"))
        # recalc path must reach the archive check, not the idempotency refuse
        with mock.patch.object(H, "RESULTS_JSON", self.results), \
                mock.patch.object(H, "RAW_OUTPUTS_JSON",
                                  os.path.join(self.tmp.name, "nope.json")), \
                mock.patch.object(H, "CLAIM_SET_JSON", self.claim_set), \
                mock.patch.object(H, "verify_freeze",
                                  return_value={"claim_set_file_sha256": "x",
                                                "code_commit": "y"}):
            with self.assertRaises(SystemExit) as cm:
                with mock.patch.object(__import__("sys"), "argv",
                                       ["prog", "--label", H.REQUIRED_LABEL,
                                        "--recalc-only"]):
                    H.main()
        # refuses for the MISSING ARCHIVE reason, not the idempotency reason
        self.assertEqual(cm.exception.code, 2)


if __name__ == "__main__":
    unittest.main()


# --------------------------------------------------------------------------- #
# Citation revalidation (Amendment 1 §5)
# --------------------------------------------------------------------------- #

class TestCitationRevalidation(unittest.TestCase):
    def test_fda_label_valid(self):
        with mock.patch.object(H, "_get_json", return_value={
                "results": [{"effective_time": "20260101"}]}), \
                mock.patch.object(H.time, "sleep", lambda *_: None):
            self.assertEqual(H.revalidate_citation(
                {"source": "fda_label", "identifier": "x"}), "valid")

    def test_fda_label_revised_after_cutoff_is_invalid(self):
        with mock.patch.object(H, "_get_json", return_value={
                "results": [{"effective_time": "20260811"}]}), \
                mock.patch.object(H.time, "sleep", lambda *_: None):
            self.assertEqual(H.revalidate_citation(
                {"source": "fda_label", "identifier": "x"}), "invalid")

    def test_network_failure_is_unverifiable_not_invalid(self):
        with mock.patch.object(H, "_get_json", return_value=None), \
                mock.patch.object(H.time, "sleep", lambda *_: None):
            self.assertEqual(H.revalidate_citation(
                {"source": "fda_label", "identifier": "x"}), "unverifiable")

    def test_chembl_release_date_gate(self):
        with mock.patch.object(H, "_get_json", return_value={
                "chembl_release_date": "2026-05-01"}), \
                mock.patch.object(H.time, "sleep", lambda *_: None):
            self.assertEqual(H.revalidate_citation(
                {"source": "chembl_molecule", "identifier": "CHEMBL25"}),
                "valid")
        with mock.patch.object(H, "_get_json", return_value={
                "chembl_release_date": "2026-09-01"}), \
                mock.patch.object(H.time, "sleep", lambda *_: None):
            self.assertEqual(H.revalidate_citation(
                {"source": "chembl_mechanism", "identifier": "CHEMBL25"}),
                "invalid")


# --------------------------------------------------------------------------- #
# Raw-archive envelope round-trip (regression: first scored run crashed with
# KeyError when the in-memory bare dict met the on-disk envelope)
# --------------------------------------------------------------------------- #

class TestArchiveRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw_path = os.path.join(self.tmp.name, "raw.json")

    def test_run_all_claims_envelope_feeds_scorer(self):
        claims = [_claim("control", "none", {"no_finding_flagged": True},
                         cid="C-01"),
                  _claim("novel", "N1_combination_product_splitting",
                         {"finding": {"code": "N1", "status": "flagged"}},
                         cid="N1-01", drug="COMBO DRUG")]
        outputs = {"C-01": {"status": "no_case", **_ctx()},
                   "N1-01": {"status": "no_case",
                             **_ctx(findings=[{"code": "N1",
                                               "status": "flagged"}])}}
        with mock.patch.object(H, "RAW_OUTPUTS_JSON", self.raw_path), \
                mock.patch.object(H, "run_one_claim",
                                  side_effect=lambda c: outputs[c["claim_id"]]):
            raw = H.run_all_claims(claims)
        self.assertEqual(set(raw), {"claim_ids", "outputs"})
        scored = H.score_from_archive({"claims": claims}, raw,
                                      revalidate=False)
        self.assertEqual(scored["outcomes"]["C-01"]["outcome"], "clean")
        self.assertEqual(scored["outcomes"]["N1-01"]["outcome"], "caught")

    def test_load_or_run_recovers_complete_archive_without_rerunning(self):
        claims = [_claim("control", "none", {"no_finding_flagged": True},
                         cid="C-01")]
        envelope = {"claim_ids": ["C-01"],
                    "outputs": {"C-01": {"status": "no_case", **_ctx()}}}
        with open(self.raw_path, "w") as fh:
            json.dump(envelope, fh)
        with mock.patch.object(H, "RAW_OUTPUTS_JSON", self.raw_path), \
                mock.patch.object(H, "run_one_claim",
                                  side_effect=AssertionError(
                                      "audits must NOT re-run")):
            raw = H.load_or_run_archive(claims)
        self.assertEqual(raw, envelope)

    def test_load_or_run_refuses_incomplete_archive(self):
        claims = [_claim("control", "none", {"no_finding_flagged": True},
                         cid="C-01"),
                  _claim("control", "none", {"no_finding_flagged": True},
                         cid="C-02", drug="OTHER")]
        with open(self.raw_path, "w") as fh:
            json.dump({"claim_ids": ["C-01", "C-02"],
                       "outputs": {"C-01": {"status": "no_case"}}}, fh)
        with mock.patch.object(H, "RAW_OUTPUTS_JSON", self.raw_path):
            with self.assertRaises(SystemExit):
                H.load_or_run_archive(claims)


# --------------------------------------------------------------------------- #
# score_from_archive plumbing
# --------------------------------------------------------------------------- #

class TestScoreFromArchive(unittest.TestCase):
    def test_missing_raw_output_abstains(self):
        claim = _claim("control", "none", {"no_finding_flagged": True},
                       cid="C-01")
        claim_set = {"claims": [claim]}
        raw = {"outputs": {}}
        with mock.patch.object(H, "revalidate_citation",
                               return_value="valid"):
            scored = H.score_from_archive(claim_set, raw)
        self.assertEqual(scored["outcomes"]["C-01"]["outcome"], "abstain")

    def test_citation_unverifiable_becomes_abstention(self):
        claim = _claim("control", "none", {"no_finding_flagged": True},
                       cid="C-01")
        claim_set = {"claims": [claim]}
        raw = {"outputs": {"C-01": {"status": "no_case", **_ctx()}}}
        with mock.patch.object(H, "revalidate_citation",
                               return_value="unverifiable"):
            scored = H.score_from_archive(claim_set, raw)
        m = scored["metrics"]
        self.assertEqual(m["control_counts"]["abstained"], 1)
        self.assertEqual(m["control_counts"]["eligible"],
                         H.GROUP_TOTALS["control"] - 1)


if __name__ == "__main__":
    unittest.main()
