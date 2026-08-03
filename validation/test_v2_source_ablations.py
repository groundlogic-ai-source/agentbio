"""Unit tests for the V2 source-ablation control harness.

Run with stdlib only:  python3 -m unittest validation.test_v2_source_ablations -v

These tests are non-network: they exercise the harness's pure logic — protocol
label / refusal gate, source conditions, drug grouping, fingerprints, holdout
self-audit, and incremental-vs-baseline aggregation. The expensive live harness
is NOT run here.
"""

import os
import sys
import unittest
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from validation import run_v2_source_ablations as A  # noqa: E402


# ── Protocol label / refusal ─────────────────────────────────────────────────

class ProtocolLabelTest(unittest.TestCase):
    def test_label_is_source_ablation_control(self):
        self.assertEqual(A.LABEL, "source_ablation_control")
        self.assertNotEqual(A.LABEL, "benchmark_v2")

    def test_benchmark_labels_forbidden(self):
        self.assertIn("benchmark_v2", A.FORBIDDEN_LABELS)
        self.assertIn("benchmark-freeze-v2", A.FORBIDDEN_LABELS)

    def test_refuses_benchmark_v2_label(self):
        with patch.object(A, "_benchmark_v2_freeze_exists", return_value=False):
            with self.assertRaises(RuntimeError):
                A.assert_not_benchmark("benchmark_v2")

    def test_refuses_when_freeze_tag_exists(self):
        with patch.object(A, "_benchmark_v2_freeze_exists", return_value=True):
            with self.assertRaises(RuntimeError):
                A.assert_not_benchmark(A.LABEL)

    def test_allows_control_label_when_no_freeze(self):
        with patch.object(A, "_benchmark_v2_freeze_exists", return_value=False):
            A.assert_not_benchmark(A.LABEL)  # must not raise

    def test_main_returns_2_on_benchmark_label(self):
        with patch.object(A, "_benchmark_v2_freeze_exists", return_value=False):
            rc = A.main(["--label", "benchmark_v2"])
        self.assertEqual(rc, 2)

    def test_output_paths_are_not_benchmark(self):
        for p in (A.RESULTS_JSON, A.RESULTS_MD):
            self.assertNotIn("benchmark", os.path.basename(p).lower())


# ── Source conditions ─────────────────────────────────────────────────────────

class SourceConditionsTest(unittest.TestCase):
    def test_four_conditions_defined(self):
        self.assertEqual(
            set(A.CONDITIONS),
            {"chembl_only", "chembl_gtopdb", "chembl_drugcentral", "all_three"})

    def test_chembl_present_in_every_condition(self):
        for enabled in A.CONDITIONS.values():
            self.assertIn("chembl", enabled)

    def test_baseline_is_chembl_only(self):
        self.assertEqual(A.BASELINE_CONDITION, "chembl_only")
        self.assertEqual(A.CONDITIONS[A.BASELINE_CONDITION], ("chembl",))

    def test_condition_source_sets(self):
        self.assertEqual(A.CONDITIONS["chembl_gtopdb"], ("chembl", "gtopdb"))
        self.assertEqual(A.CONDITIONS["chembl_drugcentral"],
                         ("chembl", "drugcentral"))
        self.assertEqual(A.CONDITIONS["all_three"],
                         ("chembl", "gtopdb", "drugcentral"))

    def test_parse_conditions_default_all(self):
        self.assertEqual(A._parse_conditions(None), tuple(A.CONDITIONS))

    def test_parse_conditions_subset_keeps_canonical_order(self):
        self.assertEqual(
            A._parse_conditions("all_three,chembl_only"),
            ("chembl_only", "all_three"))

    def test_parse_conditions_unknown_errors(self):
        with self.assertRaises(ValueError):
            A._parse_conditions("chembl_only,bogus")

    def test_thirteen_cases(self):
        self.assertEqual(len(A.TARGET_CASES), 13)


# ── Holdout self-audit ────────────────────────────────────────────────────────

class HoldoutSelfAuditTest(unittest.TestCase):
    def test_passes_when_redacted_and_no_credit(self):
        row = {"trials_holdout_redacted": True,
               "score_components": {"no_failed_trial": 0}}
        audit = A.holdout_self_audit(row)
        self.assertTrue(audit["ok"])

    def test_fails_when_not_redacted(self):
        row = {"trials_holdout_redacted": False,
               "score_components": {"no_failed_trial": 0}}
        audit = A.holdout_self_audit(row)
        self.assertFalse(audit["ok"])
        self.assertIn("trials_holdout_redacted", audit["reason"])

    def test_fails_when_no_failed_trial_credit_applied(self):
        row = {"trials_holdout_redacted": True,
               "score_components": {"no_failed_trial": 1}}
        audit = A.holdout_self_audit(row)
        self.assertFalse(audit["ok"])
        self.assertIn("no_failed_trial", audit["reason"])

    def test_fails_when_missing_components(self):
        row = {"trials_holdout_redacted": True}
        audit = A.holdout_self_audit(row)
        self.assertFalse(audit["ok"])

    def test_none_row_fails(self):
        audit = A.holdout_self_audit(None)
        self.assertFalse(audit["ok"])


# ── Fingerprints ──────────────────────────────────────────────────────────────

class FingerprintTest(unittest.TestCase):
    def test_fingerprint_changes_with_cap(self):
        all_c = tuple(A.CONDITIONS)
        self.assertNotEqual(
            A.config_source_fingerprint(10, all_c),
            A.config_source_fingerprint(5, all_c))

    def test_fingerprint_changes_with_conditions(self):
        self.assertNotEqual(
            A.config_source_fingerprint(10, ("chembl_only",)),
            A.config_source_fingerprint(10, ("chembl_only", "all_three")))

    def test_fingerprint_stable(self):
        all_c = tuple(A.CONDITIONS)
        self.assertEqual(
            A.config_source_fingerprint(10, all_c),
            A.config_source_fingerprint(10, all_c))

    def test_stale_resume_refused(self):
        import json
        import tempfile
        tmp = tempfile.mkdtemp()
        json_path = os.path.join(tmp, "results.json")
        with patch.object(A, "RESULTS_JSON", json_path):
            with open(json_path, "w") as f:
                json.dump({"label": A.LABEL, "fingerprint": "STALE",
                           "rows": [], "target_snapshots": []}, f)
            with self.assertRaises(RuntimeError):
                A._load_existing(
                    A.config_source_fingerprint(10, tuple(A.CONDITIONS)), 10)

    def test_matching_fingerprint_resumes(self):
        import json
        import tempfile
        tmp = tempfile.mkdtemp()
        json_path = os.path.join(tmp, "results.json")
        fp = A.config_source_fingerprint(10, tuple(A.CONDITIONS))
        rows_selected = [{"target_symbol": "TGT", "uniprot_id": "P1"}]
        snap = {
            "case_key": A._case_key("Foo", "Bar"),
            "drug_name": "Foo", "disease_name": "Bar", "cap": 10,
            "status": "ok", "in_universe": True,
            "selected_rows": rows_selected,
            "target_input_hash": A.target_input_hash("Bar", 10, rows_selected),
        }
        row = {"condition": "chembl_only", "drug_name": "Foo",
               "disease_name": "Bar",
               "target_input_hash": snap["target_input_hash"]}
        with patch.object(A, "RESULTS_JSON", json_path):
            with open(json_path, "w") as f:
                json.dump({"label": A.LABEL, "fingerprint": fp, "rows": [row],
                           "target_snapshots": [snap]}, f)
            done, snapshots = A._load_existing(fp, 10)
        self.assertEqual(len(done), 1)
        self.assertIn(("chembl_only", "foo", "bar"), done)
        self.assertIn(A._case_key("Foo", "Bar"), snapshots)


# ── Drug grouping / aggregation ──────────────────────────────────────────────

def _hit_row(condition, drug, disease, *, valid=True, top10=True,
             strong=True, n_union=5):
    return {
        "condition": condition, "drug_name": drug, "disease_name": disease,
        "drug_key": A._norm_name(drug), "in_universe": True, "status": "hit",
        "generated": True, "mechanistically_valid": valid, "top10": top10,
        "strong_match": strong, "rank": 1, "n_union_candidates": n_union,
        "holdout_audit": {"ok": True},
        "generated_by_target": {"target_symbol": "TGT"},
    }


def _miss_row(condition, drug, disease):
    return {
        "condition": condition, "drug_name": drug, "disease_name": disease,
        "drug_key": A._norm_name(drug), "in_universe": True, "status": "miss",
        "generated": False, "mechanistically_valid": False, "top10": False,
        "strong_match": None, "rank": None, "n_union_candidates": 3,
        "holdout_audit": None,
    }


def _invalid_row(condition, drug, disease):
    return {
        "condition": condition, "drug_name": drug, "disease_name": disease,
        "drug_key": A._norm_name(drug), "in_universe": True, "status": "error",
        "generated": False, "mechanistically_valid": False, "top10": False,
        "strong_match": None, "rank": 1, "n_union_candidates": 4,
        "holdout_audit": {"ok": False, "reason": "trials_holdout_redacted "
                          "is not True"},
    }


class DrugGroupingTest(unittest.TestCase):
    def test_imatinib_two_diseases_counts_once_at_drug_level(self):
        rows = [
            _hit_row("all_three", "Imatinib", "Chronic eosinophilic leukemia"),
            _hit_row("all_three", "Imatinib",
                     "Idiopathic Hypereosinophilic Syndrome"),
            _hit_row("all_three", "Dapsone", "Leprosy"),
        ]
        s = A.summarize_condition(rows)
        # Pair-level: 3 generated.
        self.assertEqual(s["pair_generated_recall"], 3)
        self.assertEqual(s["n_pairs"], 3)
        # Unique-drug: Imatinib collapses to ONE, so 2 unique drugs.
        self.assertEqual(s["n_unique_drugs"], 2)
        self.assertEqual(s["unique_drug_generated_recall"], 2)

    def test_drug_generated_if_any_pair_generates(self):
        rows = [
            _hit_row("all_three", "Imatinib", "Disease A"),
            _miss_row("all_three", "Imatinib", "Disease B"),
        ]
        s = A.summarize_condition(rows)
        self.assertEqual(s["pair_generated_recall"], 1)
        self.assertEqual(s["unique_drug_generated_recall"], 1)
        self.assertEqual(s["n_unique_drugs"], 1)

    def test_invalid_holdout_row_not_counted_as_hit(self):
        rows = [
            _invalid_row("all_three", "Dapsone", "Leprosy"),
            _hit_row("all_three", "Anagrelide", "ET"),
        ]
        s = A.summarize_condition(rows)
        self.assertEqual(s["pair_generated_recall"], 1)  # only Anagrelide
        self.assertEqual(s["pair_invalid_holdout"], 1)

    def test_valid_top10_strong_counts(self):
        rows = [
            _hit_row("all_three", "A", "d", valid=True, top10=True, strong=True),
            _hit_row("all_three", "B", "d", valid=False, top10=False,
                     strong=False),
        ]
        s = A.summarize_condition(rows)
        self.assertEqual(s["pair_generated_recall"], 2)
        self.assertEqual(s["pair_mechanistically_valid"], 1)
        self.assertEqual(s["pair_top10"], 1)
        self.assertEqual(s["pair_strong"], 1)


class IncrementalTest(unittest.TestCase):
    def test_incremental_recovered_pairs_and_drugs(self):
        base_rows = [
            _hit_row("chembl_only", "Dapsone", "Leprosy", n_union=2),
            _miss_row("chembl_only", "Sapropterin", "PKU"),
        ]
        cond_rows = [
            _hit_row("all_three", "Dapsone", "Leprosy", n_union=5),
            _hit_row("all_three", "Sapropterin", "PKU", n_union=6),
        ]
        base = A.summarize_condition(base_rows)
        cond = A.summarize_condition(cond_rows)
        inc = A.incremental_vs_baseline(cond, base)
        self.assertEqual(inc["n_incremental_recovered_pairs"], 1)
        self.assertEqual(inc["n_incremental_recovered_drugs"], 1)
        self.assertIn("sapropterin", inc["incremental_recovered_drugs"])
        # base union total = 2+3 = 5; cond union total = 5+6 = 11; delta = 6.
        self.assertEqual(inc["added_candidates_vs_baseline"], 6)

    def test_candidate_precision_not_estimable(self):
        base = A.summarize_condition([_hit_row("chembl_only", "A", "d")])
        cond = A.summarize_condition([_hit_row("all_three", "A", "d")])
        inc = A.incremental_vs_baseline(cond, base)
        self.assertIn("NOT ESTIMABLE",
                      inc["candidate_precision_added_false_positives"])
        self.assertIn("NOT ESTIMABLE", A.CANDIDATE_PRECISION_STATEMENT)


# ── Frozen snapshot / hashing ────────────────────────────────────────────────

class TargetInputHashTest(unittest.TestCase):
    def test_hash_deterministic(self):
        rows = [{"target_symbol": "A", "uniprot_id": "P1"},
                {"target_symbol": "B", "uniprot_id": "P2"}]
        self.assertEqual(
            A.target_input_hash("D", 10, rows),
            A.target_input_hash("D", 10, rows))

    def test_hash_changes_with_rows(self):
        rows1 = [{"target_symbol": "A"}]
        rows2 = [{"target_symbol": "B"}]
        self.assertNotEqual(
            A.target_input_hash("D", 10, rows1),
            A.target_input_hash("D", 10, rows2))

    def test_hash_changes_with_cap_and_disease(self):
        rows = [{"target_symbol": "A"}]
        self.assertNotEqual(A.target_input_hash("D", 10, rows),
                            A.target_input_hash("D", 5, rows))
        self.assertNotEqual(A.target_input_hash("D1", 10, rows),
                            A.target_input_hash("D2", 10, rows))

    def test_freeze_requires_active_holdout(self):
        with self.assertRaises(RuntimeError):
            A.freeze_case_targets("Drug", "Disease", 10)


class ValidateSnapshotTest(unittest.TestCase):
    def _good(self):
        rows = [{"target_symbol": "A", "uniprot_id": "P1"}]
        return {
            "case_key": A._case_key("Drug", "Disease"),
            "drug_name": "Drug", "disease_name": "Disease", "cap": 10,
            "status": "ok", "in_universe": True, "selected_rows": rows,
            "target_input_hash": A.target_input_hash("Disease", 10, rows),
        }

    def test_valid_snapshot_passes(self):
        A.validate_snapshot(self._good(), 10)  # must not raise

    def test_missing_field_refused(self):
        snap = self._good()
        del snap["target_input_hash"]
        with self.assertRaises(RuntimeError):
            A.validate_snapshot(snap, 10)

    def test_malformed_rows_refused(self):
        snap = self._good()
        snap["selected_rows"] = None
        with self.assertRaises(RuntimeError):
            A.validate_snapshot(snap, 10)

    def test_tampered_hash_refused(self):
        snap = self._good()
        # Mutate the frozen rows after the hash was computed -> mismatch.
        snap["selected_rows"].append({"target_symbol": "SNEAKY"})
        with self.assertRaises(RuntimeError):
            A.validate_snapshot(snap, 10)

    def test_cap_mismatch_refused(self):
        snap = self._good()  # hash computed for cap=10
        with self.assertRaises(RuntimeError):
            A.validate_snapshot(snap, 5)


# ── Case-major execution + frozen-selection reuse (mocked pipeline) ──────────

class _FreezeSpy:
    """Wraps freeze_case_targets to count calls and force a fixed snapshot."""

    def __init__(self, rows):
        self.calls = []
        self.rows = rows

    def __call__(self, drug, disease, cap):
        self.calls.append((drug, disease, cap))
        return {
            "case_key": A._case_key(drug, disease),
            "drug_name": drug, "disease_name": disease, "cap": cap,
            "status": "ok", "in_universe": True, "selected_rows": self.rows,
            "target_input_hash": A.target_input_hash(disease, cap, self.rows),
            "holdout_drugs": [drug], "holdout_active": True,
        }


class _PairSpy:
    """Records the snapshot object each pair-condition run received."""

    def __init__(self):
        self.calls = []

    def __call__(self, drug, disease, condition, cap, snapshot):
        self.calls.append({
            "condition": condition, "drug": drug, "disease": disease,
            "snapshot_id": id(snapshot),
            "target_input_hash": snapshot.get("target_input_hash"),
        })
        return {
            "label": A.LABEL, "condition": condition, "drug_name": drug,
            "drug_key": A._norm_name(drug), "disease_name": disease,
            "target_input_hash": snapshot.get("target_input_hash"),
            "in_universe": True, "status": "miss", "generated": False,
            "mechanistically_valid": False, "top10": False,
            "n_union_candidates": 0,
        }


class CaseMajorExecutionTest(unittest.TestCase):
    """select_for_disease runs once per case; all arms share identical rows/hash."""

    def _single_case(self):
        # Patch TARGET_CASES to ONE case so counting is unambiguous.
        return [(1, "Dapsone", "Leprosy", "Leprosy")]

    def _run(self, tmp_json, *, fresh=True, conditions=None):
        rows_selected = [{"target_symbol": "TGT", "uniprot_id": "P1"}]
        freeze_spy = _FreezeSpy(rows_selected)
        pair_spy = _PairSpy()
        conds = conditions or tuple(A.CONDITIONS)
        with patch.object(A, "TARGET_CASES", self._single_case()), \
                patch.object(A, "RESULTS_JSON", tmp_json), \
                patch.object(A, "RESULTS_MD", tmp_json + ".md"), \
                patch.object(A, "freeze_case_targets", freeze_spy), \
                patch.object(A, "run_pair_condition", pair_spy), \
                patch.object(A, "_benchmark_v2_freeze_exists",
                             return_value=False):
            rows = A.run_all(10, fresh, conds, "2026-01-01T00:00:00")
        return rows, freeze_spy, pair_spy

    def test_freeze_called_once_per_case(self):
        import tempfile
        tmp = os.path.join(tempfile.mkdtemp(), "r.json")
        rows, freeze_spy, pair_spy = self._run(tmp)
        # ONE case -> freeze exactly once, though four arms ran.
        self.assertEqual(len(freeze_spy.calls), 1)
        self.assertEqual(len(pair_spy.calls), 4)

    def test_all_arms_get_identical_snapshot_and_hash(self):
        import tempfile
        tmp = os.path.join(tempfile.mkdtemp(), "r.json")
        rows, freeze_spy, pair_spy = self._run(tmp)
        snapshot_ids = {c["snapshot_id"] for c in pair_spy.calls}
        hashes = {c["target_input_hash"] for c in pair_spy.calls}
        self.assertEqual(len(snapshot_ids), 1)   # byte-for-byte same object
        self.assertEqual(len(hashes), 1)         # identical hash across arms
        # Every persisted row echoes that one hash.
        self.assertEqual({r["target_input_hash"] for r in rows}, hashes)

    def test_execution_is_case_major(self):
        # With two cases, the freeze/run order must be case1(all arms),
        # then case2(all arms) — NOT arm-major.
        import tempfile
        tmp = os.path.join(tempfile.mkdtemp(), "r.json")
        two_cases = [(1, "Dapsone", "Leprosy", "Leprosy"),
                     (2, "Miglustat", "Gaucher Disease", "Gaucher Disease")]
        rows_selected = [{"target_symbol": "TGT", "uniprot_id": "P1"}]
        freeze_spy = _FreezeSpy(rows_selected)
        pair_spy = _PairSpy()
        with patch.object(A, "TARGET_CASES", two_cases), \
                patch.object(A, "RESULTS_JSON", tmp), \
                patch.object(A, "RESULTS_MD", tmp + ".md"), \
                patch.object(A, "freeze_case_targets", freeze_spy), \
                patch.object(A, "run_pair_condition", pair_spy), \
                patch.object(A, "_benchmark_v2_freeze_exists",
                             return_value=False):
            A.run_all(10, True, tuple(A.CONDITIONS), "2026-01-01T00:00:00")
        # First 4 pair calls belong to case 1, next 4 to case 2.
        drugs_in_order = [c["drug"] for c in pair_spy.calls]
        self.assertEqual(drugs_in_order[:4], ["Dapsone"] * 4)
        self.assertEqual(drugs_in_order[4:], ["Miglustat"] * 4)
        # Freeze order matches case order.
        self.assertEqual([c[0] for c in freeze_spy.calls],
                         ["Dapsone", "Miglustat"])

    def test_resume_reuses_frozen_snapshot_without_reselection(self):
        import tempfile
        tmp = os.path.join(tempfile.mkdtemp(), "r.json")
        # First pass: SAME four conditions (so the fingerprint is stable) but
        # interrupt after the first arm to leave 3 arms pending.
        rows_selected = [{"target_symbol": "TGT", "uniprot_id": "P1"}]
        freeze_spy1 = _FreezeSpy(rows_selected)

        class _InterruptAfterFirst(_PairSpy):
            def __call__(self, *a, **k):
                if len(self.calls) >= 1:
                    raise KeyboardInterrupt("simulated interruption")
                return super().__call__(*a, **k)

        pair_spy1 = _InterruptAfterFirst()
        with patch.object(A, "TARGET_CASES", self._single_case()), \
                patch.object(A, "RESULTS_JSON", tmp), \
                patch.object(A, "RESULTS_MD", tmp + ".md"), \
                patch.object(A, "freeze_case_targets", freeze_spy1), \
                patch.object(A, "run_pair_condition", pair_spy1), \
                patch.object(A, "_benchmark_v2_freeze_exists",
                             return_value=False):
            with self.assertRaises(KeyboardInterrupt):
                A.run_all(10, True, tuple(A.CONDITIONS), "2026-01-01T00:00:00")
        self.assertEqual(len(freeze_spy1.calls), 1)

        # Second pass (resume, all 4 conditions): freeze MUST NOT be called
        # again — the snapshot is reused from persisted state.
        freeze_spy2 = _FreezeSpy(rows_selected)
        pair_spy2 = _PairSpy()
        with patch.object(A, "TARGET_CASES", self._single_case()), \
                patch.object(A, "RESULTS_JSON", tmp), \
                patch.object(A, "RESULTS_MD", tmp + ".md"), \
                patch.object(A, "freeze_case_targets", freeze_spy2), \
                patch.object(A, "run_pair_condition", pair_spy2), \
                patch.object(A, "_benchmark_v2_freeze_exists",
                             return_value=False):
            rows = A.run_all(10, False, tuple(A.CONDITIONS),
                             "2026-01-01T00:00:00")
        # No re-selection for the incomplete arms.
        self.assertEqual(len(freeze_spy2.calls), 0)
        # The 3 remaining arms ran; chembl_only was skipped (already done).
        self.assertEqual(len(pair_spy2.calls), 3)
        self.assertEqual(len(rows), 4)
        # All rows still share the one frozen hash.
        self.assertEqual(len({r["target_input_hash"] for r in rows}), 1)

    def test_resume_refused_when_snapshot_tampered(self):
        import json
        import tempfile
        tmp = os.path.join(tempfile.mkdtemp(), "r.json")
        rows_selected = [{"target_symbol": "TGT", "uniprot_id": "P1"}]
        freeze_spy1 = _FreezeSpy(rows_selected)
        pair_spy1 = _PairSpy()
        with patch.object(A, "TARGET_CASES", self._single_case()), \
                patch.object(A, "RESULTS_JSON", tmp), \
                patch.object(A, "RESULTS_MD", tmp + ".md"), \
                patch.object(A, "freeze_case_targets", freeze_spy1), \
                patch.object(A, "run_pair_condition", pair_spy1), \
                patch.object(A, "_benchmark_v2_freeze_exists",
                             return_value=False):
            A.run_all(10, True, ("chembl_only",), "2026-01-01T00:00:00")

        # Tamper the persisted snapshot's rows WITHOUT updating its hash.
        with open(tmp) as f:
            data = json.load(f)
        data["target_snapshots"][0]["selected_rows"].append(
            {"target_symbol": "SNEAKY"})
        with open(tmp, "w") as f:
            json.dump(data, f)

        with patch.object(A, "RESULTS_JSON", tmp):
            with self.assertRaises(RuntimeError):
                A._load_existing(
                    A.config_source_fingerprint(10, tuple(A.CONDITIONS)), 10)

    def test_resume_refused_when_snapshot_missing_for_completed_row(self):
        import json
        import tempfile
        tmp = os.path.join(tempfile.mkdtemp(), "r.json")
        fp = A.config_source_fingerprint(10, tuple(A.CONDITIONS))
        row = {"condition": "chembl_only", "drug_name": "Foo",
               "disease_name": "Bar"}
        with open(tmp, "w") as f:
            json.dump({"label": A.LABEL, "fingerprint": fp, "rows": [row],
                       "target_snapshots": []}, f)
        with patch.object(A, "RESULTS_JSON", tmp):
            with self.assertRaises(RuntimeError):
                A._load_existing(fp, 10)


class RunPairConditionNoSelectionTest(unittest.TestCase):
    """run_pair_condition must never call select_for_disease itself."""

    def test_uses_snapshot_and_never_selects(self):
        snapshot = {
            "case_key": A._case_key("Dapsone", "Leprosy"),
            "drug_name": "Dapsone", "disease_name": "Leprosy", "cap": 10,
            "status": "ok", "in_universe": True,
            "selected_rows": [],   # empty -> no target loop, but no selection
            "target_input_hash": A.target_input_hash("Leprosy", 10, []),
        }

        def _boom(*a, **k):
            raise AssertionError("select_for_disease must NOT be called")

        with patch.object(A, "select_for_disease", _boom), \
                patch.object(A, "run_reviewer", lambda *a, **k: []), \
                patch.object(A, "merge_chemist_candidates",
                             lambda c: []), \
                patch.object(A, "_resolve_confirmed_inchikey_block",
                             lambda d: None):
            res = A.run_pair_condition("Dapsone", "Leprosy", "chembl_only",
                                       10, snapshot)
        self.assertEqual(res["status"], "miss")
        self.assertEqual(res["target_input_hash"],
                         snapshot["target_input_hash"])
        self.assertTrue(res["in_universe"])

    def test_out_of_scope_snapshot_short_circuits(self):
        snapshot = {
            "case_key": A._case_key("X", "Nope"),
            "drug_name": "X", "disease_name": "Nope", "cap": 10,
            "status": "out_of_scope", "in_universe": False,
            "error": "not in universe", "selected_rows": [],
            "target_input_hash": A.target_input_hash("Nope", 10, []),
        }

        def _boom(*a, **k):
            raise AssertionError("select_for_disease must NOT be called")

        with patch.object(A, "select_for_disease", _boom):
            res = A.run_pair_condition("X", "Nope", "chembl_only", 10, snapshot)
        self.assertEqual(res["status"], "out_of_scope")
        self.assertFalse(res["in_universe"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
