"""Unit tests for the V2 engineering acceptance runner.

Run with stdlib only:  python3 -m unittest validation.test_v2_acceptance -v

These tests MOCK the pipeline (no network) and assert the protocol invariants
declared in validation/run_v2_engineering_acceptance.py and the pre-registration
docs:

  * The confirmed drug name is NEVER passed to disease-side target selection or
    to any source-collection call — only to holdout sealing and post-run
    matching.
  * `mechanistically_valid` is decided ONLY on qualified efficacy/target/
    mechanism evidence + direction compatibility, never on a name co-mention.
    Positive AND negative NON-FIXTURE controls exercise the classifier.
  * The run refuses under any benchmark label and when benchmark-freeze-v2
    exists; it never creates a freeze/tag.
  * Active-moiety matching uses the InChIKey block first, then a conservative
    name fallback.
  * Every candidate target row runs up to the configurable cap.
  * Incremental resume is refused when the config/source fingerprint changes,
    unless --fresh is used.
"""

import os
import sys
import unittest
from unittest.mock import patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from validation import run_v2_engineering_acceptance as R  # noqa: E402
from data_sources import holdout as holdout_mod  # noqa: E402


# ── Fixtures / synthetic pipeline data (NON-fixture controls live here) ──────

# A confirmed drug used only for the "never-leaked" assertion.
CONTROL_DRUG = "Zaptozumab"          # deliberately not one of the five fixtures
CONTROL_DISEASE = "Synthetic Rare Disease"
CONTROL_BLOCK = "ABCDEFGHIJKLMN"     # 14-char InChIKey connectivity block


def _ledger(records, providers=None, efficacy=0.8, source_health=None):
    return {
        "providers": providers or ["chembl"],
        "efficacy_confidence": efficacy,
        "source_health": source_health or {"chembl": True},
        "records": records,
    }


def _qualified_efficacy_record(action="inhibitor", direction="inhibitor",
                               provider="chembl", lineage="assay:CH1|t=p|m=pchembl"):
    return {
        "provider": provider,
        "evidence_role": "efficacy",
        "qualification_status": "qualified",
        "action": action,
        "direction": direction,
        "contradiction_status": "none",
        "lineage_id": lineage,
    }


# ---- POSITIVE non-fixture control: a valid candidate row -----------------------
POSITIVE_CANDIDATE = {
    "drug_name": CONTROL_DRUG,
    "molecule_chembl_id": "CHEMBLPOS",
    "efficacy_confidence": 0.82,
    "strong_match": True,
    "mechanism_direction": {"verdict": "DIRECTIONALLY_COMPATIBLE",
                            "compatible": True, "incompatible": False},
    "mechanism_cap_applied": False,
    "_evidence_ledger": _ledger(
        [_qualified_efficacy_record()],
        providers=["chembl", "drugcentral"],
        efficacy=0.82,
    ),
}

# ---- NEGATIVE non-fixture control 1: name co-mention only (no qualified evidence)
NEGATIVE_NAME_ONLY = {
    "drug_name": CONTROL_DRUG,
    "molecule_chembl_id": "CHEMBLNEG1",
    "efficacy_confidence": None,
    "strong_match": False,
    "mechanism_direction": {},
    "_evidence_ledger": _ledger(
        [{
            "provider": "europepmc",
            "evidence_role": "other",           # bare co-mention, not efficacy
            "qualification_status": "unqualified",
            "action": "",
            "direction": "unknown",
            "contradiction_status": "none",
            "lineage_id": "pub:00000|t=|role=other",
        }],
        providers=["europepmc"],
        efficacy=None,
    ),
}

# ---- NEGATIVE non-fixture control 2: qualified evidence but direction incompatible
NEGATIVE_DIR_INCOMPATIBLE = {
    "drug_name": CONTROL_DRUG,
    "molecule_chembl_id": "CHEMBLNEG2",
    "efficacy_confidence": 0.75,
    "strong_match": False,
    "mechanism_direction": {"verdict": "DIRECTIONALLY_INCOMPATIBLE",
                            "incompatible": True, "compatible": False},
    "mechanism_cap_applied": True,
    "_evidence_ledger": _ledger(
        [_qualified_efficacy_record(action="inhibitor")],
        efficacy=0.75,
    ),
}


class MechanisticValidityClassifierTest(unittest.TestCase):
    """Positive + negative NON-fixture controls for the validity classifier."""

    def test_positive_control_is_valid(self):
        res = R.classify_mechanistic_validity(POSITIVE_CANDIDATE)
        self.assertTrue(res["mechanistically_valid"])
        self.assertGreaterEqual(res["qualified_evidence_count"], 1)
        self.assertTrue(res["direction_compatible"])

    def test_negative_name_only_is_invalid(self):
        res = R.classify_mechanistic_validity(NEGATIVE_NAME_ONLY)
        self.assertFalse(res["mechanistically_valid"])
        self.assertEqual(res["qualified_evidence_count"], 0)
        self.assertIn("name co-mention", res["reason"])

    def test_negative_direction_incompatible_is_invalid(self):
        res = R.classify_mechanistic_validity(NEGATIVE_DIR_INCOMPATIBLE)
        self.assertFalse(res["mechanistically_valid"])
        self.assertFalse(res["direction_compatible"])

    def test_none_row_is_invalid(self):
        res = R.classify_mechanistic_validity(None)
        self.assertFalse(res["mechanistically_valid"])

    def test_contradicted_qualified_evidence_is_invalid(self):
        row = {
            "drug_name": CONTROL_DRUG,
            "efficacy_confidence": 0.7,
            "_evidence_ledger": _ledger([{
                "provider": "chembl",
                "evidence_role": "efficacy",
                "qualification_status": "qualified",
                "action": "inhibitor",
                "direction": "inhibitor",
                "contradiction_status": "contradicted",
                "lineage_id": "assay:X|t=p|m=pchembl",
            }]),
        }
        res = R.classify_mechanistic_validity(row)
        self.assertFalse(res["mechanistically_valid"])


class ActiveMoietyMatchingTest(unittest.TestCase):
    """InChIKey connectivity block first, then conservative name fallback."""

    def test_inchikey_block_match_first(self):
        chem = [{"drug_name": "Some Brand Salt",
                 "molecule_chembl_id": "CHEMBL1",
                 "inchikey": f"{CONTROL_BLOCK}-XXXXXXXXXX-N"}]
        reviewed = [{"drug_name": "Some Brand Salt", "molecule_chembl_id": "CHEMBL1"}]
        rank, row, method = R.match_active_moiety(
            CONTROL_DRUG, chem, reviewed, CONTROL_BLOCK)
        self.assertEqual(rank, 1)
        self.assertEqual(method, "inchikey_block")

    def test_exact_normalized_name_fallback(self):
        chem = [{"drug_name": CONTROL_DRUG,
                 "molecule_chembl_id": "CONTROL-ID"}]
        reviewed = [{"drug_name": CONTROL_DRUG,
                     "molecule_chembl_id": "CONTROL-ID"}]
        rank, row, method = R.match_active_moiety(
            CONTROL_DRUG, chem, reviewed, None)
        self.assertEqual(rank, 1)
        self.assertIs(row, reviewed[0])
        self.assertEqual(method, "name")

    def test_name_substring_is_not_same_active_moiety(self):
        chem = [{"drug_name": "Triflupromazine",
                 "molecule_chembl_id": "DIFFERENT-ID"}]
        reviewed = [{"drug_name": "Triflupromazine",
                     "molecule_chembl_id": "DIFFERENT-ID"}]
        rank, row, method = R.match_active_moiety(
            "Promazine", chem, reviewed, None)
        self.assertIsNone(rank)
        self.assertIsNone(row)
        self.assertIsNone(method)


class SourceDiverseTargetSelectionTest(unittest.TestCase):
    """Coverage reserves canonical process targets without changing row scores."""

    def test_reserves_distinct_canonical_process_classes(self):
        rows = [
            {"target_symbol": f"G{i}", "tractability_score": 1 - i / 20}
            for i in range(8)
        ]
        rows.extend([
            {
                "target_symbol": "PROC_A",
                "mechanism_class": "class_a",
                "process_support": [{"pmid": "1"}],
                "process_target_priority": 0,
                "process_class_priority": 1,
                "tractability_score": 0.1,
            },
            {
                "target_symbol": "PROC_B",
                "mechanism_class": "class_b",
                "process_support": [{"pmid": "2"}],
                "process_target_priority": 0,
                "process_class_priority": 2,
                "tractability_score": 0.05,
            },
        ])
        selected = R.select_source_diverse_targets(rows, 5)
        self.assertEqual(len(selected), 5)
        self.assertEqual(
            [r["target_symbol"] for r in selected],
            ["G0", "G1", "G2", "PROC_A", "PROC_B"],
        )

    def test_secondary_mapping_does_not_consume_reserved_slot(self):
        rows = [
            {"target_symbol": "GENE", "tractability_score": 1.0},
            {
                "target_symbol": "SECONDARY",
                "mechanism_class": "class_a",
                "process_support": [{"pmid": "1"}],
                "process_target_priority": 1,
                "process_class_priority": 1,
                "tractability_score": 0.9,
            },
            {
                "target_symbol": "CANONICAL",
                "mechanism_class": "class_a",
                "process_support": [{"pmid": "1"}],
                "process_target_priority": 0,
                "process_class_priority": 1,
                "tractability_score": 0.1,
            },
        ]
        selected = R.select_source_diverse_targets(rows, 2)
        self.assertEqual(
            [r["target_symbol"] for r in selected], ["GENE", "CANONICAL"])

    def test_name_fallback_when_no_block(self):
        chem = [{"drug_name": CONTROL_DRUG, "molecule_chembl_id": "CHEMBL2",
                 "inchikey": None}]
        reviewed = [{"drug_name": CONTROL_DRUG, "molecule_chembl_id": "CHEMBL2"}]
        rank, row, method = R.match_active_moiety(
            CONTROL_DRUG, chem, reviewed, None)
        self.assertEqual(rank, 1)
        self.assertEqual(method, "name")

    def test_no_match_returns_none(self):
        chem = [{"drug_name": "Unrelated", "molecule_chembl_id": "CHEMBL9",
                 "inchikey": "ZZZZZZZZZZZZZZ-YYYYYYYYYY-N"}]
        reviewed = [{"drug_name": "Unrelated", "molecule_chembl_id": "CHEMBL9"}]
        rank, row, method = R.match_active_moiety(
            CONTROL_DRUG, chem, reviewed, CONTROL_BLOCK)
        self.assertIsNone(rank)

    def test_block_beats_name_even_with_salt_name(self):
        # Different display name but same connectivity block -> still a match.
        chem = [{"drug_name": "Totally Different Name",
                 "molecule_chembl_id": "CHEMBL3",
                 "inchikey": f"{CONTROL_BLOCK}-AAAAAAAAAA-M"}]
        reviewed = [{"drug_name": "Totally Different Name",
                     "molecule_chembl_id": "CHEMBL3"}]
        rank, row, method = R.match_active_moiety(
            CONTROL_DRUG, chem, reviewed, CONTROL_BLOCK)
        self.assertEqual(method, "inchikey_block")


class RefusalGateTest(unittest.TestCase):
    """Refuse benchmark labels and the v2 freeze; never create a freeze/tag."""

    def test_refuses_benchmark_v2_label(self):
        with patch.object(R, "_benchmark_v2_freeze_exists", return_value=False):
            with self.assertRaises(RuntimeError):
                R.assert_not_benchmark("benchmark_v2")

    def test_refuses_when_freeze_tag_exists(self):
        with patch.object(R, "_benchmark_v2_freeze_exists", return_value=True):
            with self.assertRaises(RuntimeError):
                R.assert_not_benchmark(R.LABEL)

    def test_allows_engineering_acceptance_label(self):
        with patch.object(R, "_benchmark_v2_freeze_exists", return_value=False):
            R.assert_not_benchmark(R.LABEL)  # must not raise

    def test_main_returns_2_on_benchmark_label(self):
        with patch.object(R, "_benchmark_v2_freeze_exists", return_value=False):
            rc = R.main(["--label", "benchmark_v2"])
        self.assertEqual(rc, 2)

    def test_label_constant_is_engineering_acceptance(self):
        self.assertEqual(R.LABEL, "engineering_acceptance")
        self.assertIn("benchmark_v2", R.FORBIDDEN_LABELS)


class FingerprintResumeTest(unittest.TestCase):
    """Fingerprint changes must refuse a stale resume unless --fresh."""

    def test_fingerprint_changes_with_cap(self):
        fp10 = R.config_source_fingerprint(10)
        fp5 = R.config_source_fingerprint(5)
        self.assertNotEqual(fp10, fp5)

    def test_stale_resume_refused(self):
        import json
        import tempfile
        tmp = tempfile.mkdtemp()
        json_path = os.path.join(tmp, "results.json")
        with patch.object(R, "RESULTS_JSON", json_path):
            with open(json_path, "w") as f:
                json.dump({"label": R.LABEL, "fingerprint": "STALE",
                           "cases": []}, f)
            with self.assertRaises(RuntimeError):
                R._load_existing(R.config_source_fingerprint(10))

    def test_matching_fingerprint_resumes(self):
        import json
        import tempfile
        tmp = tempfile.mkdtemp()
        json_path = os.path.join(tmp, "results.json")
        fp = R.config_source_fingerprint(10)
        with patch.object(R, "RESULTS_JSON", json_path):
            with open(json_path, "w") as f:
                json.dump({"label": R.LABEL, "fingerprint": fp,
                           "cases": [{"drug_name": "Foo", "disease_name": "Bar"}]}, f)
            done = R._load_existing(fp)
        self.assertEqual(len(done), 1)


class _CallSpy:
    """Records every positional + keyword argument passed to a callable."""

    def __init__(self, return_value):
        self.calls = []
        self.return_value = return_value

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.return_value

    def all_arg_strings(self):
        out = []
        for args, kwargs in self.calls:
            out.extend(repr(a) for a in args)
            out.extend(repr(v) for v in kwargs.values())
        return out


class DrugNameNeverLeakedTest(unittest.TestCase):
    """The confirmed drug name must never reach source collection.

    We mock the pipeline and record every argument passed to
    select_for_disease / run_biologist / run_chemist / run_reviewer.  The
    normalized drug name must not appear in ANY of those arguments — the drug is
    only allowed to reach holdout activation and the post-run PubChem/name match.
    """

    def _mock_pipeline(self, drug, disease):
        rows = [{
            "target_symbol": "TGT1", "uniprot_id": "P00001",
            "disease_name": disease, "ot_association_score": 0.5,
            "target_discovery_method": "genetic_association",
        }]
        select_spy = _CallSpy(rows)
        # biologist returns an object that only knows the target (never the drug)
        bio_spy = _CallSpy({"target": rows[0], "literature_hits": []})
        # chemist returns a pool that HAPPENS to contain the held-out drug
        chem_out = {"target": rows[0], "candidates": [{
            "drug_name": drug, "molecule_chembl_id": "CHEMBLX",
            "inchikey": f"{CONTROL_BLOCK}-XXXXXXXXXX-N",
        }]}
        chem_spy = _CallSpy(chem_out)
        reviewed = [dict(
            POSITIVE_CANDIDATE,
            molecule_chembl_id="CHEMBLX",
            drug_name=drug,
            trials_holdout_redacted=True,
            # Matches runtime policy: a redacted lookup is an unmade
            # observation, so the Reviewer emits None (term dropped from the
            # composite), never 0 (which would read as a real failed trial).
            score_components={"no_failed_trial": None},
            process_memberships=[{
                "target_symbol": "TGT1",
                "mechanism_class": "control_process",
                "therapeutic_role": "symptom_treatment",
            }],
        )]
        rev_spy = _CallSpy(reviewed)
        return select_spy, bio_spy, chem_spy, rev_spy

    def test_drug_name_never_passed_to_source_collection(self):
        drug, disease = CONTROL_DRUG, CONTROL_DISEASE
        select_spy, bio_spy, chem_spy, rev_spy = self._mock_pipeline(drug, disease)

        pubchem_spy = _CallSpy({"inchikey": f"{CONTROL_BLOCK}-XXXXXXXXXX-N"})

        with patch.object(R, "select_for_disease", select_spy), \
                patch.object(R, "run_biologist", bio_spy), \
                patch.object(R, "run_chemist", chem_spy), \
                patch.object(R, "run_reviewer", rev_spy), \
                patch.object(R, "get_compound_data", pubchem_spy):
            result = R.run_fixture(drug, disease, cap=10)

        norm_drug = R._norm_name(drug)

        # select_for_disease (target selection) must be driven by the DISEASE,
        # never the drug — the single most important leakage seal.
        self.assertEqual(len(select_spy.calls), 1)
        self.assertNotIn(norm_drug,
                         R._norm_name("".join(select_spy.all_arg_strings())))
        self.assertIn(R._norm_name(disease),
                      R._norm_name("".join(select_spy.all_arg_strings())))

        # The biologist is called with a target dict the HARNESS constructs from
        # disease-derived rows. The harness must never inject the drug name into
        # that input dict.
        for args, kwargs in bio_spy.calls:
            target_arg = args[0] if args else kwargs.get("target", {})
            self.assertNotIn(
                norm_drug,
                R._norm_name("".join(f"{k}{v}" for k, v in target_arg.items())),
                msg="drug name leaked into a harness-built biologist target input",
            )

        # Chemist / reviewer receive the PREVIOUS STAGE'S OUTPUT (which may
        # legitimately contain the drug as a pool member — that IS the
        # rediscovery). The harness must pass those through verbatim and never
        # add the drug name itself as an extra collection argument. So: the only
        # place the drug may appear in chem/rev call args is inside the piped
        # biologist/chemist output object, never as a separate string/kwarg the
        # harness introduced.
        for args, kwargs in chem_spy.calls:
            # first arg is the biologist output object (piped); any FURTHER
            # positional/keyword arg must not carry the drug name.
            extra = list(args[1:]) + list(kwargs.values())
            for e in extra:
                self.assertNotIn(norm_drug, R._norm_name(str(e)))
        for args, kwargs in rev_spy.calls:
            extra = list(args[2:]) + list(kwargs.values())
            for e in extra:
                self.assertNotIn(norm_drug, R._norm_name(str(e)))

        # get_compound_data (post-run matching) IS allowed to receive the drug.
        self.assertTrue(any(norm_drug in R._norm_name("".join(map(str, args)))
                            for args, _ in pubchem_spy.calls))

        # And the run actually matched the drug back via the InChIKey block.
        self.assertTrue(result["generated"])
        self.assertEqual(result["match_method"], "inchikey_block")
        self.assertTrue(result["trials_holdout_redacted"])
        # None, not 0: the redacted drug must be neither credited nor
        # penalised for the trial evidence the harness itself hid.
        self.assertIsNone(result["score_components"]["no_failed_trial"])
        self.assertEqual(
            result["process_memberships"],
            rev_spy.return_value[0]["process_memberships"],
        )

    def test_holdout_sealed_around_run(self):
        drug, disease = CONTROL_DRUG, CONTROL_DISEASE
        select_spy, bio_spy, chem_spy, rev_spy = self._mock_pipeline(drug, disease)
        observed = {}

        def _select(q):
            observed["holdout_active_during_run"] = holdout_mod.is_active()
            observed["holdout_drugs"] = holdout_mod.drugs()
            return select_spy(q)

        pubchem_spy = _CallSpy({"inchikey": f"{CONTROL_BLOCK}-XXXXXXXXXX-N"})
        with patch.object(R, "select_for_disease", _select), \
                patch.object(R, "run_biologist", bio_spy), \
                patch.object(R, "run_chemist", chem_spy), \
                patch.object(R, "run_reviewer", rev_spy), \
                patch.object(R, "get_compound_data", pubchem_spy):
            with holdout_mod.holdout_active([drug]):
                R.run_fixture(drug, disease, cap=10)

        self.assertTrue(observed["holdout_active_during_run"])
        self.assertIn(drug, observed["holdout_drugs"])
        # Holdout is cleared after the context exits.
        self.assertFalse(holdout_mod.is_active())


class TargetCapTest(unittest.TestCase):
    """Every candidate target row runs, up to the configurable cap."""

    def _rows(self, n):
        return [{
            "target_symbol": f"T{i}", "uniprot_id": f"P{i:05d}",
            "disease_name": CONTROL_DISEASE, "ot_association_score": 0.5,
            "target_discovery_method": "genetic_association",
        } for i in range(n)]

    def _run_with_rows(self, n_rows, cap):
        rows = self._rows(n_rows)
        select_spy = _CallSpy(rows)
        bio_spy = _CallSpy({"target": rows[0], "literature_hits": []})
        chem_spy = _CallSpy({"target": rows[0], "candidates": []})
        rev_spy = _CallSpy([])  # nothing generated -> exercises full cap loop
        pubchem_spy = _CallSpy({"inchikey": None})
        with patch.object(R, "select_for_disease", select_spy), \
                patch.object(R, "run_biologist", bio_spy), \
                patch.object(R, "run_chemist", chem_spy), \
                patch.object(R, "run_reviewer", rev_spy), \
                patch.object(R, "get_compound_data", pubchem_spy):
            result = R.run_fixture(CONTROL_DRUG, CONTROL_DISEASE, cap=cap)
        return result, bio_spy

    def test_runs_up_to_cap(self):
        result, bio_spy = self._run_with_rows(n_rows=20, cap=10)
        self.assertEqual(result["n_targets_run"], 10)
        self.assertEqual(len(bio_spy.calls), 10)

    def test_runs_all_when_fewer_than_cap(self):
        result, bio_spy = self._run_with_rows(n_rows=3, cap=10)
        self.assertEqual(result["n_targets_run"], 3)
        self.assertEqual(len(bio_spy.calls), 3)

    def test_default_cap_is_10(self):
        self.assertEqual(R.DEFAULT_TARGET_CAP, 10)


class FixtureIdentityTest(unittest.TestCase):
    """The five archived fixtures, exactly."""

    def test_exactly_five_fixtures(self):
        self.assertEqual(len(R.FIXTURE_CASES), 5)

    def test_fixtures_match_archive(self):
        expected = {
            ("phenobarbital", "lennoxgastautsyndrome"),
            ("lamotrigine", "lennoxgastautsyndrome"),
            ("mercaptopurine", "acutepromyelocyticleukemia"),
            ("vincristine", "rhabdomyosarcoma"),
            ("promazine", "acuteintermittentporphyria"),
        }
        got = {(R._norm_name(d), R._norm_name(dis)) for d, dis in R.FIXTURE_CASES}
        self.assertEqual(got, expected)


class ScopeClassificationTest(unittest.TestCase):
    """Scope reporting separates non-comparable cases without excluding them."""

    def test_cytotoxic_fixture_is_reported_outside_expected_scope(self):
        result = R.classify_scope_limitation("Vincristine", "Rhabdomyosarcoma")
        self.assertFalse(result["in_expected_scope"])
        self.assertEqual(result["classification"], "cytotoxic_chemo")

    def test_symptomatic_fixture_is_reported_outside_expected_scope(self):
        result = R.classify_scope_limitation("Promazine", "Acute intermittent porphyria")
        self.assertFalse(result["in_expected_scope"])
        self.assertEqual(result["classification"], "symptomatic")

    def test_mechanism_driven_controls_are_not_misclassified(self):
        for drug, disease in [
            ("Ibrutinib", "Waldenstrom macroglobulinemia"),
            ("Tretinoin", "Acute promyelocytic leukemia"),
            ("Anagrelide", "Essential thrombocythemia"),
        ]:
            result = R.classify_scope_limitation(drug, disease)
            self.assertTrue(result["in_expected_scope"])
            self.assertEqual(result["classification"], "mechanism_driven")


class OnlySelectionTest(unittest.TestCase):
    """--only runs a subset without redefining or overwriting the suite."""

    def test_only_selects_a_single_archived_fixture(self):
        self.assertEqual(R.select_fixture_cases("phenobarbital"),
                         [("Phenobarbital", "Lennox-Gastaut syndrome")])

    def test_only_matches_disease_names_too(self):
        picked = R.select_fixture_cases("Lennox-Gastaut")
        self.assertEqual(len(picked), 2)
        self.assertTrue(all(d == "Lennox-Gastaut syndrome" for _, d in picked))

    def test_unmatched_only_is_a_hard_error(self):
        with self.assertRaises(RuntimeError):
            R.select_fixture_cases("nosuchdrug")

    def test_no_filter_runs_every_archived_fixture(self):
        self.assertEqual(R.select_fixture_cases(None), list(R.FIXTURE_CASES))

    def test_subset_run_never_overwrites_canonical_artifacts(self):
        full_json, full_md = R.diagnostic_result_paths(None)
        only_json, only_md = R.diagnostic_result_paths("phenobarbital")
        self.assertEqual(full_json, R.RESULTS_JSON)
        self.assertEqual(full_md, R.RESULTS_MD)
        self.assertNotEqual(only_json, R.RESULTS_JSON)
        self.assertNotEqual(only_md, R.RESULTS_MD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
