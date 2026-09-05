"""Regression tests for the post-benchmark hardening pass.

Each test class pins ONE failure mode observed in the Cantu syndrome dossier,
where an unapproved BindingDB research compound (rendered as
``moiety:FAEKWTJYAYMJKF``) became the headline candidate against a target that
was never established as disease-causal, in a dossier that simultaneously
claimed the pool was restricted to approved drugs.

Run with:  python3 -m unittest discover -s validation
"""

import unittest
from unittest import mock

from agents import schemas
from agents.reviewer import (
    _apply_causal_tier_demotion, _postcap_direction_shortlist,
    _candidate_source_coverage, _rank_reviewed, _target_tier,
)
from agents.chemist import run_chemist
from agents.writer import (
    _approval_basis_cell, _citations, _discovery_method_cell, _target_tier_cell,
)
from data_sources.multisource_candidates import (
    APPROVAL_BASIS_LEDGER, APPROVAL_BASIS_MAX_PHASE, APPROVAL_BASIS_UNKNOWN,
    approval_basis, filter_repurposing_eligible, merge_chemist_candidates,
)


def _candidate(**overrides):
    """A merged-candidate dict shaped like the ledger's chemist output."""
    base = {
        "drug_name": "somedrug",
        "inchikey": "AAAAAAAAAAAAAA-BBBBBBBBBB-C",
        "max_phase": None,
        "target_symbol": "ABCC8",
        "uniprot_id": "Q09428",
        "target_discovery_method": "genetic_association",
        "_evidence_ledger": {"records": []},
    }
    base.update(overrides)
    return base


def _approval_record(provider="drugcentral", phase=4.0, qualified=True):
    return {
        "provider": provider,
        "source_type": "regulatory_approval",
        "source_id": f"{provider}-approval:123",
        "measurement_type": "phase",
        "measurement_value": phase,
        "qualification_status": "qualified" if qualified else "unqualified",
    }


class TestApprovalBasis(unittest.TestCase):
    """Approval must be POSITIVELY evidenced; unknown is not approved."""

    def test_unknown_approval_is_not_approved(self):
        approved, basis, providers = approval_basis(_candidate())
        self.assertFalse(approved)
        self.assertEqual(basis, APPROVAL_BASIS_UNKNOWN)
        self.assertEqual(providers, [])

    def test_max_phase_four_is_approved(self):
        approved, basis, _ = approval_basis(_candidate(max_phase=4.0))
        self.assertTrue(approved)
        self.assertEqual(basis, APPROVAL_BASIS_MAX_PHASE)

    def test_qualified_approval_record_is_approved(self):
        cand = _candidate(_evidence_ledger={"records": [_approval_record()]})
        approved, basis, providers = approval_basis(cand)
        self.assertTrue(approved)
        self.assertEqual(basis, APPROVAL_BASIS_LEDGER)
        self.assertEqual(providers, ["drugcentral"])

    def test_unqualified_or_low_phase_record_is_not_approval_evidence(self):
        for record in (_approval_record(qualified=False),
                       _approval_record(phase=2.0)):
            cand = _candidate(_evidence_ledger={"records": [record]})
            approved, basis, _ = approval_basis(cand)
            self.assertFalse(approved)
            self.assertEqual(basis, APPROVAL_BASIS_UNKNOWN)


class TestRepurposingGate(unittest.TestCase):
    """The union boundary must re-check approval, not trust per-source filters."""

    def test_unapproved_candidate_is_dropped_in_repurposing_mode(self):
        pool = [_candidate(drug_name="moiety:FAEKWTJYAYMJKF"),
                _candidate(drug_name="glibenclamide", max_phase=4.0)]
        eligible, excluded = filter_repurposing_eligible(pool, enforce=True)
        self.assertEqual([c["drug_name"] for c in eligible], ["glibenclamide"])
        self.assertEqual([c["drug_name"] for c in excluded],
                         ["moiety:FAEKWTJYAYMJKF"])

    def test_mixed_pool_keeps_everything_but_still_stamps_basis(self):
        pool = [_candidate(drug_name="tool-compound")]
        eligible, excluded = filter_repurposing_eligible(pool, enforce=False)
        self.assertEqual(len(eligible), 1)
        self.assertEqual(excluded, [])
        self.assertEqual(eligible[0]["approval_basis"], APPROVAL_BASIS_UNKNOWN)

    def test_provider_approval_makes_is_approved_drug_true(self):
        # The ledger derives is_approved_drug from max_phase alone, so approval
        # evidenced only by a provider record must be reconciled — otherwise the
        # gate and the downstream unapproved cap disagree.
        cand = _candidate(_evidence_ledger={"records": [_approval_record()]})
        eligible, _ = filter_repurposing_eligible([cand], enforce=True)
        self.assertTrue(eligible[0]["is_approved_drug"])

    def test_remerge_preserves_provider_established_approval(self):
        record = dict(_approval_record(),
                      molecule_name="drugX",
                      inchikey="AAAAAAAAAAAAAA-BBBBBBBBBB-C")
        rows = [_candidate(drug_name="drugX",
                           _evidence_ledger={"records": [record]})]
        merged = merge_chemist_candidates(rows)
        self.assertEqual(len(merged), 1)
        self.assertTrue(merged[0]["is_approved_drug"])
        self.assertEqual(merged[0]["approval_basis"], APPROVAL_BASIS_LEDGER)


class TestValueLevelValidation(unittest.TestCase):
    """Presence-only validation passed a field that is present but blank."""

    def test_blank_discovery_method_is_reported(self):
        problems = schemas.validate_handoff(
            [{"drug_name": "d", "target_discovery_method": ""}],
            "test",
            [("target_discovery_method", "error")],
            [("target_discovery_method", "error")],
        )
        self.assertTrue(any("BLANK" in p for p in problems))

    def test_populated_value_is_silent(self):
        problems = schemas.validate_handoff(
            [{"drug_name": "d", "target_discovery_method": "pathway_neighbor"}],
            "test",
            [("target_discovery_method", "error")],
            [("target_discovery_method", "error")],
        )
        self.assertEqual(problems, [])

    def test_strict_mode_raises_on_blank_error_field(self):
        with mock.patch.object(schemas, "STRICT_VALIDATION", True):
            with self.assertRaises(RuntimeError):
                schemas.validate_handoff(
                    [{"drug_name": "d", "target_discovery_method": "  "}],
                    "test", [], [("target_discovery_method", "error")],
                )

    def test_chemist_value_spec_covers_discovery_method(self):
        self.assertIn(("target_discovery_method", "error"),
                      schemas._CHEMIST_VALUE_FIELDS)

    def test_approval_lineage_is_required_at_chemist_boundary(self):
        self.assertIn(("approval_basis", "error"),
                      schemas._CHEMIST_REQUIRED_FIELDS)

    def test_new_reviewer_lineage_fields_are_monitored(self):
        required = dict(schemas._REVIEWER_REQUIRED_FIELDS)
        for field in (
            "approval_basis", "approval_evidence_providers", "target_tier",
            "exploratory_rank_demoted", "causal_anchor",
        ):
            self.assertIn(field, required)


class TestCausalTierDemotion(unittest.TestCase):
    """An exploratory target may not silently outrank an anchored one."""

    def test_blank_discovery_method_is_unattributed(self):
        self.assertEqual(_target_tier(""), "unattributed")
        self.assertEqual(_target_tier(None), "unattributed")
        self.assertEqual(_target_tier("pathway_neighbor"), "exploratory_expansion")
        self.assertEqual(_target_tier("genetic_association"), "causal_anchor")

    def test_exploratory_leader_is_demoted_below_anchor(self):
        reviewed = [
            {"drug_name": "exploratory", "composite_score": 0.40,
             "target_discovery_method": "pathway_neighbor",
             "target_symbol": "ABCC8"},
            {"drug_name": "anchored", "composite_score": 0.38,
             "target_discovery_method": "genetic_association",
             "target_symbol": "ABCC9"},
        ]
        _apply_causal_tier_demotion(reviewed)
        self.assertEqual([r["drug_name"] for r in reviewed],
                         ["anchored", "exploratory"])
        self.assertTrue(reviewed[1]["exploratory_rank_demoted"])
        self.assertEqual(reviewed[1]["causal_anchor"]["target_symbol"], "ABCC9")

    def test_normal_rank_entrypoint_applies_causal_hierarchy(self):
        reviewed = [
            {"drug_name": "exploratory", "composite_score": 0.40,
             "pre_cap_score": 0.40, "target_discovery_method": "pathway_neighbor",
             "target_symbol": "ABCC8"},
            {"drug_name": "anchored", "composite_score": 0.38,
             "pre_cap_score": 0.38, "target_discovery_method": "genetic_association",
             "target_symbol": "ABCC9"},
        ]
        _rank_reviewed(reviewed)
        self.assertEqual(reviewed[0]["drug_name"], "anchored")

    def test_scores_are_never_changed(self):
        reviewed = [
            {"drug_name": "exploratory", "composite_score": 0.40,
             "target_discovery_method": "", "target_symbol": "ABCC8"},
            {"drug_name": "anchored", "composite_score": 0.38,
             "target_discovery_method": "genetic_association",
             "target_symbol": "ABCC9"},
        ]
        _apply_causal_tier_demotion(reviewed)
        by_name = {r["drug_name"]: r["composite_score"] for r in reviewed}
        self.assertEqual(by_name, {"exploratory": 0.40, "anchored": 0.38})

    def test_all_exploratory_pool_is_left_alone(self):
        reviewed = [
            {"drug_name": "a", "composite_score": 0.4,
             "target_discovery_method": "pathway_neighbor"},
            {"drug_name": "b", "composite_score": 0.3,
             "target_discovery_method": ""},
        ]
        _apply_causal_tier_demotion(reviewed)
        self.assertEqual([r["drug_name"] for r in reviewed], ["a", "b"])
        self.assertFalse(any(r["exploratory_rank_demoted"] for r in reviewed))

    def test_anchored_leader_is_untouched(self):
        reviewed = [
            {"drug_name": "anchored", "composite_score": 0.7,
             "target_discovery_method": "genetic_association"},
            {"drug_name": "exploratory", "composite_score": 0.5,
             "target_discovery_method": "pathway_neighbor"},
        ]
        _apply_causal_tier_demotion(reviewed)
        self.assertEqual([r["drug_name"] for r in reviewed],
                         ["anchored", "exploratory"])
        self.assertFalse(reviewed[1]["exploratory_rank_demoted"])

    def test_demotion_metadata_is_recomputed_after_score_change(self):
        reviewed = [
            {"drug_name": "exploratory", "composite_score": 0.7,
             "pre_cap_score": 0.7,
             "target_discovery_method": "pathway_neighbor"},
            {"drug_name": "anchor", "composite_score": 0.6,
             "pre_cap_score": 0.6,
             "target_discovery_method": "genetic_association"},
        ]
        _rank_reviewed(reviewed)
        self.assertTrue(reviewed[1]["exploratory_rank_demoted"])

        reviewed[1]["composite_score"] = 0.2
        _rank_reviewed(reviewed)
        exploratory = next(r for r in reviewed if r["drug_name"] == "exploratory")
        self.assertFalse(exploratory["exploratory_rank_demoted"])
        self.assertIsNone(exploratory["causal_anchor"])

    def test_postcap_shortlist_skips_weak_tier_prioritized_anchor(self):
        reviewed = [
            {"drug_name": "weak-anchor", "strong_match": False, "smiles": None},
            {"drug_name": "new-strong-exploratory", "strong_match": True,
             "smiles": None},
        ]
        selected = _postcap_direction_shortlist(reviewed, set(), [])
        self.assertEqual(
            [candidate["drug_name"] for candidate in selected],
            ["new-strong-exploratory"],
        )


class TestEligibilityGate(unittest.TestCase):
    """No eligible candidate must terminate, not open a review checkpoint."""

    def setUp(self):
        import main_graph
        self.main_graph = main_graph
        patcher = mock.patch.object(main_graph, "_write_json")
        self.addCleanup(patcher.stop)
        patcher.start()

    def _verdict(self, candidates, repurposing_only=True):
        state = {"reviewed": {"candidates": candidates,
                              "repurposing_only": repurposing_only}}
        return self.main_graph.eligibility_gate_node(state)["eligibility"]

    def test_unapproved_only_pool_is_ineligible(self):
        verdict = self._verdict([{"drug_name": "tool", "is_approved_drug": False}])
        self.assertFalse(verdict["eligible"])
        self.assertIn("No eligible repurposing candidate", verdict["reason"])

    def test_empty_pool_is_ineligible(self):
        verdict = self._verdict([])
        self.assertFalse(verdict["eligible"])
        self.assertIn("No candidate compound", verdict["reason"])

    def test_approved_candidate_is_eligible(self):
        verdict = self._verdict([{
            "drug_name": "d",
            "is_approved_drug": True,
            "literature_limitation_blocked": False,
            "literature_limitation_gate_cleared": True,
        }])
        self.assertTrue(verdict["eligible"])
        self.assertEqual(verdict["reason"], "")

    def test_mixed_pool_mode_does_not_gate(self):
        verdict = self._verdict([{
            "drug_name": "tool",
            "is_approved_drug": False,
            "literature_limitation_blocked": False,
            "literature_limitation_gate_cleared": True,
        }], repurposing_only=False)
        self.assertTrue(verdict["eligible"])

    def test_pre_gate_legacy_candidate_is_ineligible_for_paid_validation(self):
        verdict = self._verdict([{
            "drug_name": "legacy",
            "is_approved_drug": True,
        }])
        self.assertFalse(verdict["eligible"])
        self.assertIn("did not clear", verdict["reason"])

    def test_router_sends_ineligible_runs_to_end(self):
        from langgraph.graph import END
        route = self.main_graph._route_after_eligibility
        self.assertEqual(route({"eligibility": {"eligible": False}}), END)
        self.assertEqual(route({"eligibility": {"eligible": True}}),
                         "structure_validation")


class TestDossierPreflightSelection(unittest.TestCase):
    def test_pool_larger_than_shortlist_validates_only_selected_paid_lead(self):
        import main_graph

        selected = {
            "drug_name": "selected",
            "strong_match": True,
            "literature_limitation_blocked": False,
            "literature_limitation_gate_cleared": True,
            "paid_validation_eligible": True,
            "headline_eligible": True,
            "is_approved_drug": True,
        }
        audit_only = [
            {
                "drug_name": f"audit-{index}",
                "strong_match": False,
                "literature_limitation_blocked": False,
                "literature_limitation_gate_cleared": False,
                "paid_validation_eligible": False,
                "headline_eligible": False,
                "is_approved_drug": True,
            }
            for index in range(
                main_graph.MAX_LITERATURE_LIMITATION_CANDIDATES + 2)
        ]
        state = {
            "reviewed": {
                "candidates": [selected, *audit_only],
                "repurposing_only": True,
            },
            "targets": [],
            "biologist_outputs": [],
        }
        with mock.patch.object(main_graph, "_write_json"), mock.patch.object(
            main_graph.writer, "validate_dossier_inputs"
        ) as validate:
            verdict = main_graph.dossier_preflight_node(state)[
                "dossier_preflight"]
        self.assertEqual(verdict["n_candidates_checked"], 1)
        validate.assert_called_once()
        self.assertIs(validate.call_args.args[0], selected)

    def test_holdout_reviewer_reuses_persisted_frozen_output_even_for_job(self):
        import main_graph

        frozen = {
            "formula": {"formula_version": "reviewer-composite-v2"},
            "candidates": [{"drug_name": "frozen-ranked-lead"}],
        }
        with mock.patch.object(
            main_graph._holdout, "is_active", return_value=True
        ), mock.patch.object(
            main_graph, "_load_json", return_value=frozen
        ), mock.patch.object(
            main_graph, "run_reviewer"
        ) as run_reviewer:
            result = main_graph.reviewer_node({
                "job_id": "fresh-production-style-job",
                "chemist_output": {"candidates": [{"drug_name": "changed"}]},
            })
        self.assertIs(result["reviewed"], frozen)
        run_reviewer.assert_not_called()

    def test_cli_reviewer_cache_invalidates_on_input_source_and_formula(self):
        import main_graph
        import api.policy_contracts as policy

        base_chemist = {
            "candidates": [],
            "source_status": {
                "chembl": {"status": "complete"},
                "gtopdb": {"status": "complete"},
                "drugcentral": {"status": "complete"},
                "bindingdb": {"status": "complete"},
            },
        }
        cached = {
            "reviewer_input_fingerprint":
                policy.reviewer_input_fingerprint(base_chemist),
            "formula": {
                "formula_version": policy.REVIEWER_FORMULA_VERSION,
            },
            "safety_schema_version": policy.SAFETY_SCHEMA_VERSION,
            "literature_schema_version": policy.LITERATURE_SCHEMA_VERSION,
            "report_contract_version": policy.REPORT_CONTRACT_VERSION,
            "candidates": [],
        }
        with mock.patch.object(
            main_graph._holdout, "is_active", return_value=False
        ), mock.patch.object(
            main_graph, "_load_json", return_value=cached
        ), mock.patch.object(main_graph, "run_reviewer") as reviewer:
            reused = main_graph.reviewer_node({"chemist_output": base_chemist})
        self.assertIs(reused["reviewed"], cached)
        reviewer.assert_not_called()

        mutations = [
            {**base_chemist, "candidates": [{"drug_name": "changed input"}]},
            {
                **base_chemist,
                "source_status": {
                    **base_chemist["source_status"],
                    "chembl": {"status": "failed", "error": "down"},
                },
            },
        ]
        for changed in mutations:
            with self.subTest(changed=changed), mock.patch.object(
                main_graph._holdout, "is_active", return_value=False
            ), mock.patch.object(
                main_graph, "_load_json", return_value=cached
            ), mock.patch.object(
                main_graph, "run_reviewer", return_value=[]
            ) as reviewer, mock.patch.object(
                main_graph, "validate_reviewer_handoff"
            ), mock.patch.object(main_graph, "_write_json"):
                main_graph.reviewer_node({"chemist_output": changed})
            reviewer.assert_called_once()

        with mock.patch.object(
            policy, "REVIEWER_FORMULA_VERSION", "future-reviewer-formula"
        ), mock.patch.object(
            main_graph._holdout, "is_active", return_value=False
        ), mock.patch.object(
            main_graph, "_load_json", return_value=cached
        ), mock.patch.object(
            main_graph, "run_reviewer", return_value=[]
        ) as reviewer, mock.patch.object(
            main_graph, "validate_reviewer_handoff"
        ), mock.patch.object(main_graph, "_write_json"):
            main_graph.reviewer_node({"chemist_output": base_chemist})
        reviewer.assert_called_once()

    def test_missing_frozen_artifact_fails_closed_without_reviewer(self):
        import main_graph

        with mock.patch.object(
            main_graph._holdout, "is_active", return_value=True
        ), mock.patch.object(
            main_graph, "_load_json", return_value=None
        ), mock.patch.object(
            main_graph, "run_reviewer"
        ) as run_reviewer:
            with self.assertRaisesRegex(
                RuntimeError, "provenance/integrity failure"
            ):
                main_graph.reviewer_node({
                    "job_id": "frozen-without-artifact",
                    "chemist_output": {"candidates": []},
                })
        run_reviewer.assert_not_called()


class TestKTargetCompleteness(unittest.TestCase):
    @staticmethod
    def _healthy_sources():
        return {
            source: {"status": "complete", "error": None}
            for source in ("bindingdb", "chembl", "drugcentral", "gtopdb")
        }

    def test_missing_uniprot_returns_explicit_unevaluated_sources(self):
        result = run_chemist({
            "target": {
                "target_symbol": "MISSING",
                "disease_name": "Synthetic disease",
            },
        })
        self.assertIn("error", result)
        self.assertEqual(
            set(result["source_status"]),
            {"bindingdb", "chembl", "drugcentral", "gtopdb"},
        )
        self.assertTrue(all(
            row["status"] == "unavailable" and row["evaluated"] is False
            for row in result["source_status"].values()
        ))

    def test_disjoint_biologist_and_chemist_failures_form_union(self):
        import main_graph

        targets = [
            {"target_symbol": symbol, "uniprot_id": f"P{index}"}
            for index, symbol in enumerate(("BIOFAIL", "CHEMFAIL", "HEALTHY"))
        ]
        bios = [
            {"target": targets[0], "error": "biologist failed"},
            {"target": targets[1]},
            {"target": targets[2]},
        ]
        surviving = {
            "drug_name": "survivor",
            "target_symbol": "HEALTHY",
            "uniprot_id": "P2",
        }

        def chemist_result(bio, **_kwargs):
            symbol = bio["target"]["target_symbol"]
            if symbol == "CHEMFAIL":
                raise RuntimeError("chemist failed")
            return {
                "target": bio["target"],
                "candidates": [surviving] if symbol == "HEALTHY" else [],
                "source_status": self._healthy_sources(),
                "approved_reference_set_size": 0,
                "approval_gate": {"excluded": []},
            }

        state = {
            "targets": targets,
            "target": targets[0],
            "biologist_outputs": bios,
            "biologist_output": bios[0],
            "repurposing_only": True,
            "k_bio_failed": 1,
        }
        with mock.patch.object(
            main_graph, "run_chemist", side_effect=chemist_result
        ), mock.patch.object(
            main_graph, "merge_chemist_candidates",
            side_effect=lambda rows: rows,
        ), mock.patch.object(
            main_graph, "validate_chemist_handoff"
        ), mock.patch.object(main_graph, "_write_json"):
            output = main_graph.chemist_node(state)["chemist_output"]

        summary = output["k_target_summary"]
        self.assertEqual(summary["k_failed"], 2)
        self.assertEqual(
            set(summary["failed_targets"]), {"BIOFAIL", "CHEMFAIL"})
        coverage = _candidate_source_coverage(output["source_status"])
        self.assertFalse(coverage["complete"])
        self.assertTrue(any(
            row["source"] == "target_evaluation"
            for row in coverage["failures"]
        ))

        reviewed_survivor = {
            **surviving,
            "is_approved_drug": True,
            "strong_match": True,
            "literature_limitation_blocked": False,
            "literature_limitation_gate_cleared": True,
            "paid_validation_eligible": True,
            "headline_eligible": True,
        }
        with mock.patch.object(main_graph, "_write_json"):
            verdict = main_graph.eligibility_gate_node({
                "repurposing_only": True,
                "reviewed": {
                    "repurposing_only": True,
                    "candidates": [reviewed_survivor],
                },
                "chemist_output": output,
            })["eligibility"]
        self.assertFalse(verdict["eligible"])
        self.assertEqual(verdict["terminal_status"], "source_unavailable")


class TestScopeAwareTargetCoverage(unittest.TestCase):
    @staticmethod
    def _sources(status="complete"):
        return {
            source: {"status": status, "error": "no verifiable ChEMBL target"
                     if status != "complete" else None}
            for source in ("bindingdb", "chembl", "drugcentral", "gtopdb")
        }

    def _status(self):
        targets = [
            ("ABCC9", "O60706", "genetic_association", None, "complete"),
            ("KCNJ8", "Q15842", "genetic_association", None, "complete"),
            ("KCNJ11", "P48048", "genetic_association", None, "complete"),
            ("ABCA10", "Q8WWZ7", "pathway_neighbor", "direct", "failed"),
            ("ABCA12", "Q86UK0", "pathway_neighbor", "moderate", "failed"),
        ]
        return {
            f"target_{i}_{symbol}": {
                "_target": {
                    "status": target_status, "target_index": i,
                    "target_symbol": symbol, "uniprot_id": accession,
                    "target_discovery_method": method,
                    "pathway_neighbor_tier": tier,
                    "coverage_required": method != "pathway_neighbor",
                },
                **self._sources(
                    "unavailable" if target_status == "failed" else "complete"),
            }
            for i, (symbol, accession, method, tier, target_status)
            in enumerate(targets, 1)
        }

    def test_unrelated_exploratory_failures_warn_not_veto_causal_candidate(self):
        coverage = _candidate_source_coverage(
            self._status(), {
                "drug_name": "glibenclamide",
                "target_symbol": "ABCC9", "uniprot_id": "O60706",
            })
        self.assertTrue(coverage["complete"])
        self.assertTrue(coverage["warnings"])
        self.assertIn("ABCA10", {row["target_symbol"] for row in coverage["warnings"]})

        # Eligibility's global source projection has the same scope: failed
        # ABCA branches must not turn a complete ABCC9 lead into
        # source_unavailable.
        import main_graph
        self.assertEqual(main_graph._source_failure_details(self._status()), [])

    def test_failed_exploratory_target_vetoes_its_own_candidate(self):
        coverage = _candidate_source_coverage(
            self._status(), {"target_symbol": "ABCA10", "uniprot_id": "Q8WWZ7"})
        self.assertFalse(coverage["complete"])
        self.assertIn("ABCA10", {row["target_symbol"] for row in coverage["failures"]})

    def test_required_target_failure_vetoes_every_candidate(self):
        status = self._status()
        status["target_1_ABCC9"]["chembl"]["status"] = "unavailable"
        coverage = _candidate_source_coverage(
            status, {"target_symbol": "KCNJ11", "uniprot_id": "P48048"})
        self.assertFalse(coverage["complete"])
        self.assertIn("ABCC9", {row["target_symbol"] for row in coverage["failures"]})


class TestDossierDisclosure(unittest.TestCase):
    """The dossier must not assert traceability or provenance it lacks."""

    def test_blank_discovery_method_is_not_rendered_as_genetic(self):
        cell = _discovery_method_cell({"target_discovery_method": ""})
        self.assertNotIn("genetic_association", cell)
        self.assertIn("unattributed", cell)

    def test_missing_discovery_method_is_not_rendered_as_genetic(self):
        self.assertIn("unattributed", _discovery_method_cell({}))

    def test_unknown_approval_is_disclosed(self):
        cell = _approval_basis_cell({"approval_basis": "unknown"})
        self.assertIn("NOT established", cell)

    def test_demotion_is_disclosed_in_the_tier_cell(self):
        cell = _target_tier_cell({
            "target_tier": "exploratory_expansion",
            "exploratory_rank_demoted": True,
            "causal_anchor": {"drug_name": "anchored", "target_symbol": "ABCC9",
                              "target_discovery_method": "genetic_association"},
        })
        self.assertIn("exploratory", cell)
        self.assertIn("anchored", cell)

    def test_multisource_record_ids_are_cited(self):
        candidate = {
            "drug_name": "d",
            "_evidence_ledger": {"records": [
                {"provider": "bindingdb", "source_id": "bindingdb:50123:IC50:12345678",
                 "publication_id": "12345678"},
                {"provider": "gtopdb", "source_id": "gtopdb-approval:4321"},
                {"provider": "chembl", "source_id": "CHEMBL25"},
            ]},
        }
        with mock.patch("agents.writer.check_prior_trials",
                        return_value={"trials": []}):
            cites = _citations(candidate, None)
        self.assertIn("bindingdb", cites["source_records"])
        self.assertIn("gtopdb", cites["source_records"])
        # ChEMBL keeps its own citation class; it is not duplicated here.
        self.assertNotIn("chembl", cites["source_records"])
        self.assertIn("12345678", cites["pmids"])

    def test_non_numeric_publication_reference_is_not_called_a_pmid(self):
        candidate = {
            "drug_name": "d",
            "_evidence_ledger": {"records": [{
                "provider": "bindingdb",
                "publication_id": "10.1016/j.example.2024.01.001",
            }]},
        }
        with mock.patch("agents.writer.check_prior_trials",
                        return_value={"trials": []}):
            cites = _citations(candidate, None)
        self.assertEqual(cites["pmids"], [])
        self.assertIn(
            "doi/ref:10.1016/j.example.2024.01.001",
            cites["source_records"]["bindingdb"],
        )

    def test_provider_without_identifier_is_disclosed_not_dropped(self):
        candidate = {
            "drug_name": "d",
            "_evidence_ledger": {"records": [{"provider": "drugcentral"}]},
        }
        with mock.patch("agents.writer.check_prior_trials",
                        return_value={"trials": []}):
            cites = _citations(candidate, None)
        self.assertIn("drugcentral", cites["source_records"])
        self.assertIn(
            "⚠ record with no citable identifier",
            cites["source_records"]["drugcentral"],
        )


if __name__ == "__main__":
    unittest.main()
