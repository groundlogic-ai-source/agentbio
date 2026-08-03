"""Unit tests for data_sources/europepmc_mechanisms.py (no network required).

The Europe PMC mechanism lane is a DISEASE-PROCESS discovery source: given a
disease it asks the literature which broad, disease-agnostic mechanism classes
are asserted, and returns the canonical human targets for each admitted class.
It never queries by a drug name.

Coverage:
  - positive non-fixture diseases admit the expected class/targets:
      * Dravet syndrome           -> voltage-gated sodium channel (SCN1A/SCN2A)
      * Wilms tumor               -> microtubule / mitotic spindle (TUBB/...)
      * Lesch-Nyhan syndrome      -> purine / nucleotide antimetabolite (HPRT1)
  - a negative non-fixture disease/class pairing is NOT admitted;
  - the dopaminergic class carries therapeutic_role == 'symptom_treatment';
  - a paper naming an active held-out drug is EXCLUDED before admission;
  - no drug name ever appears in the outgoing query (disease-only);
  - lineage/provenance (PMID/PMCID/title/excerpt/query/source) is preserved;
  - a transient failure -> 'unavailable' and is NOT cached;
  - a malformed payload -> 'parse_failed' and is NOT cached;
  - a healthy empty result IS cached (and served from cache on the next call).

All requests and the cache are mocked; nothing touches the network. No LLM and
no fixture/drug names appear anywhere below.

Run: python3 -m unittest validation.test_europepmc_mechanisms
"""

import sys
import unittest
from unittest import mock

sys.path.insert(0, ".")

import requests  # noqa: E402
from cache.cache import make_key, _get_conn  # noqa: E402
from data_sources import europepmc_mechanisms as epmc  # noqa: E402
from data_sources import holdout  # noqa: E402


# --------------------------------------------------------------------------- #
# Cache helpers
# --------------------------------------------------------------------------- #
def _has(key: str) -> bool:
    return _get_conn().execute(
        "SELECT 1 FROM cache WHERE key=?", (key,)).fetchone() is not None


def _purge(*keys: str) -> None:
    conn = _get_conn()
    for k in keys:
        conn.execute("DELETE FROM cache WHERE key=?", (k,))
    conn.commit()


def _cache_key(disease: str, min_support: int) -> str:
    return make_key(
        f"europepmc_discover_disease_process_{epmc._ONTOLOGY_VERSION}",
        disease.strip().lower(),
        min_support,
        sorted({epmc._norm(d) for d in holdout.drugs() if epmc._norm(d)}),
    )


# --------------------------------------------------------------------------- #
# Fake HTTP response + a class-term-routed fake requests.get
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status_code=200, json_data=None, raise_json=False):
        self.status_code = status_code
        self._json_data = json_data
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("no JSON object could be decoded")
        return self._json_data


def _result_payload(records):
    """Wrap raw record dicts in the Europe PMC search contract shape."""
    return {"resultList": {"result": list(records)}}


def _rec(title, abstract, pmid=None, pmcid=None, rec_id=None):
    r = {"title": title, "abstractText": abstract}
    if pmid is not None:
        r["pmid"] = pmid
    if pmcid is not None:
        r["pmcid"] = pmcid
    if rec_id is not None:
        r["id"] = rec_id
    return r


def _make_get(class_records, recorder=None, default=None):
    """Return a fake requests.get that answers per mechanism class.

    `class_records` maps a mechanism_class label to the list of record dicts
    that its query should return. The class is identified by inspecting which
    ontology class's terms appear (quoted) in the outgoing query string. Any
    class not present in the map returns `default` (an empty result list by
    default). `recorder` collects each outgoing query string.
    """
    term_to_class = {}
    for cls in epmc.MECHANISM_ONTOLOGY:
        for t in cls["terms"]:
            term_to_class[t] = cls["mechanism_class"]

    def _fake_get(url, params=None, headers=None, timeout=None):
        query = (params or {}).get("query", "")
        if recorder is not None:
            recorder.append(query)
        matched = None
        for term, label in term_to_class.items():
            if f'"{term}"' in query:
                matched = label
                break
        records = class_records.get(matched, default if default is not None
                                    else [])
        if isinstance(records, Exception):
            raise records
        if isinstance(records, _FakeResp):
            return records
        return _FakeResp(json_data=_result_payload(records))

    return _fake_get


# =========================================================================== #
class TestEuropePmcMechanisms(unittest.TestCase):

    def setUp(self):
        holdout.deactivate()
        self._keys = set()

    def tearDown(self):
        holdout.deactivate()
        _purge(*self._keys)

    def _run(self, disease, class_records, min_support=1, recorder=None,
             default=None):
        key = _cache_key(disease, min_support)
        self._keys.add(key)
        _purge(key)
        fake_get = _make_get(class_records, recorder, default)
        with mock.patch.object(epmc.requests, "get", fake_get):
            return epmc.discover_disease_process_targets(
                disease, min_support=min_support), key

    # ---- positive: Dravet -> voltage-gated sodium channel ---------------- #
    def test_positive_dravet_sodium_channel(self):
        disease = "Dravet syndrome"
        recs = [
            _rec("Voltage-gated sodium channel dysfunction in Dravet syndrome",
                 "Loss-of-function of the Nav1.1 voltage-gated sodium channel "
                 "is the core neurological mechanism of Dravet syndrome "
                 "pathophysiology and seizures.",
                 pmid="30000001"),
            _rec("Sodium channel Nav1.1 as a therapeutic target in Dravet "
                 "syndrome",
                 "A sodium channel receptor pathway underlies seizures; the "
                 "channel is a therapeutic neurological target in Dravet "
                 "syndrome epilepsy.",
                 pmid="30000002"),
        ]
        res, key = self._run(disease, {"voltage_gated_sodium_channel": recs},
                             min_support=2)
        self.assertEqual(res["source"], "europepmc")
        self.assertEqual(res["status"], "ok")
        self.assertIsNone(res["error"])
        classes = {t["mechanism_class"] for t in res["targets"]}
        self.assertIn("voltage_gated_sodium_channel", classes)
        symbols = {t["symbol"] for t in res["targets"]
                   if t["mechanism_class"] == "voltage_gated_sodium_channel"}
        self.assertIn("SCN1A", symbols)
        self.assertTrue(any(t["uniprot_id"] for t in res["targets"]))
        self.assertTrue(_has(key), "healthy ok must be cached")

    # ---- positive: Wilms tumor -> microtubule / mitotic spindle ---------- #
    def test_positive_wilms_microtubule(self):
        disease = "Wilms tumor"
        recs = [
            _rec("Microtubule and mitotic spindle targeting in Wilms tumor",
                 "Tubulin-directed agents disrupt the mitotic spindle; the "
                 "microtubule is a therapeutic target in Wilms tumor.",
                 pmcid="PMC7000001"),
        ]
        res, key = self._run(disease, {"microtubule_mitotic_spindle": recs},
                             min_support=1)
        self.assertEqual(res["status"], "ok")
        classes = {t["mechanism_class"] for t in res["targets"]}
        self.assertIn("microtubule_mitotic_spindle", classes)
        self.assertTrue(_has(key))

    # ---- positive: Lesch-Nyhan -> purine / nucleotide antimetabolite ----- #
    def test_positive_lesch_nyhan_purine(self):
        disease = "Lesch-Nyhan syndrome"
        recs = [
            _rec("Purine metabolism defect in Lesch Nyhan syndrome",
                 "Targeting purine metabolism is a therapeutic strategy in "
                 "Lesch Nyhan syndrome; nucleotide metabolism inhibition "
                 "modulates the disease pathway.",
                 pmid="30000010"),
        ]
        res, key = self._run(disease,
                             {"purine_nucleotide_antimetabolite": recs},
                             min_support=1)
        self.assertEqual(res["status"], "ok")
        symbols = {t["symbol"] for t in res["targets"]}
        self.assertIn("HPRT1", symbols)
        self.assertTrue(_has(key))

    # ---- negative non-fixture disease / class ---------------------------- #
    def test_negative_disease_class_not_admitted(self):
        # A disease whose records mention a class term but NOT the disease
        # tokens together with a relation keyword must not admit anything.
        disease = "Marfan syndrome"
        recs = [
            # class term present, but disease tokens absent -> not supporting.
            _rec("JAK-STAT signaling in an unrelated inflammatory model",
                 "Janus kinase and STAT signaling drive an unrelated "
                 "inflammatory mechanism pathway."),
        ]
        res, key = self._run(disease, {"jak_stat_signaling": recs},
                             min_support=1)
        self.assertEqual(res["status"], "empty")
        self.assertEqual(res["targets"], [])
        self.assertTrue(_has(key), "healthy empty is cacheable")

    # ---- symptom role for dopaminergic class ----------------------------- #
    def test_dopaminergic_symptom_role(self):
        disease = "Restless legs syndrome"
        recs = [
            _rec("Dopamine receptor modulation in Restless legs syndrome",
                 "Dopaminergic dopamine receptor modulation gives symptomatic "
                 "benefit; the receptor pathway mechanism is targeted in "
                 "Restless legs syndrome.",
                 pmid="30000020"),
        ]
        res, key = self._run(
            disease,
            {"dopamine_receptor_symptomatic_modulation": recs},
            min_support=1)
        self.assertEqual(res["status"], "ok")
        dop = [t for t in res["targets"]
               if t["mechanism_class"]
               == "dopamine_receptor_symptomatic_modulation"]
        self.assertTrue(dop)
        for t in dop:
            self.assertEqual(t["therapeutic_role"], "symptom_treatment")

    def test_symptom_complication_without_positive_outcome_not_admitted(self):
        disease = "Fabry disease"
        recs = [
            _rec(
                "Delirium in Fabry disease",
                "Delirium is a frequently inadequately treated "
                "neuropsychiatric complication in Fabry disease.",
                pmid="30000021",
            ),
        ]
        res, _ = self._run(
            disease,
            {"antipsychotic_symptom_management": recs},
            min_support=1,
        )
        self.assertEqual(res["status"], "empty")
        self.assertEqual(res["targets"], [])

    def test_therapy_followed_by_disease_is_not_efficacy_evidence(self):
        disease = "Erdheim-Chester disease"
        recs = [
            _rec(
                "Erdheim-Chester disease subsequent to Janus kinase therapy",
                "A selective Janus kinase inhibitor targets inflammatory "
                "signaling. Erdheim-Chester disease developed after therapy.",
                pmid="30000022",
            ),
        ]
        res, _ = self._run(
            disease,
            {"jak_stat_signaling": recs},
            min_support=1,
        )
        self.assertEqual(res["status"], "empty")
        self.assertEqual(res["targets"], [])

    # ---- held-out paper excluded before admission ------------------------ #
    def test_heldout_paper_excluded(self):
        disease = "Dravet syndrome"
        held = "Zylophenidine"  # invented, non-fixture placeholder name
        # Two supporting records, but the ONLY one is redacted because it names
        # the active held-out drug; with min_support=1 that leaves zero.
        recs = [
            _rec("Voltage-gated sodium channel in Dravet syndrome",
                 f"The {held} sodium channel study shows the voltage-gated "
                 "sodium channel is a therapeutic target in Dravet syndrome.",
                 pmid="30000030"),
        ]
        holdout.activate([held])
        try:
            key = _cache_key(disease, 1)
            self._keys.add(key)
            _purge(key)
            fake_get = _make_get({"voltage_gated_sodium_channel": recs})
            with mock.patch.object(epmc.requests, "get", fake_get):
                res = epmc.discover_disease_process_targets(disease,
                                                            min_support=1)
        finally:
            holdout.deactivate()
        self.assertEqual(res["status"], "empty")
        self.assertEqual(res["targets"], [])

    def test_heldout_fingerprint_partitions_cache(self):
        # Same disease, different holdout state -> different cache keys.
        disease = "Dravet syndrome"
        k_none = _cache_key(disease, 1)
        holdout.activate(["Zylophenidine"])
        try:
            k_held = _cache_key(disease, 1)
        finally:
            holdout.deactivate()
        self.assertNotEqual(k_none, k_held)

    # ---- no drug name in the outgoing query ------------------------------ #
    def test_query_is_disease_only(self):
        disease = "Dravet syndrome"
        recorder = []
        recs = [
            _rec("Voltage-gated sodium channel in Dravet syndrome",
                 "The voltage-gated sodium channel is a therapeutic target "
                 "mechanism in Dravet syndrome epilepsy and seizures.",
                 pmid="30000040"),
        ]
        res, _ = self._run(disease, {"voltage_gated_sodium_channel": recs},
                           min_support=1, recorder=recorder)
        self.assertEqual(res["status"], "ok")
        self.assertTrue(recorder)
        for q in recorder:
            # Every query is the exact disease phrase AND class terms only.
            self.assertIn(f'"{disease}"', q)
            self.assertIn("AND", q)
            # No drug-ish tokens: query is built purely from disease + ontology
            # class terms (asserted structurally, not by a drug denylist).
            self.assertNotIn("drug", q.lower())

    # ---- lineage / provenance preserved ---------------------------------- #
    def test_lineage_preserved(self):
        disease = "Dravet syndrome"
        recs = [
            _rec("Voltage-gated sodium channel in Dravet syndrome",
                 "The voltage-gated sodium channel is a therapeutic target "
                 "mechanism in Dravet syndrome neurological pathophysiology "
                 "and seizures.",
                 pmid="30000050", pmcid="PMC7000050"),
        ]
        res, _ = self._run(disease, {"voltage_gated_sodium_channel": recs},
                           min_support=1)
        self.assertEqual(res["status"], "ok")
        t = next(t for t in res["targets"]
                 if t["mechanism_class"] == "voltage_gated_sodium_channel")
        self.assertEqual(t["source"], "europepmc")
        self.assertEqual(t["ontology_version"], epmc._ONTOLOGY_VERSION)
        self.assertIn(f'"{disease}"', t["query"])
        self.assertTrue(t["supporting_records"])
        sr = t["supporting_records"][0]
        self.assertEqual(sr["pmid"], "30000050")
        self.assertEqual(sr["pmcid"], "PMC7000050")
        self.assertTrue(sr["title"])
        self.assertTrue(sr["excerpt"])

    # ---- distinct-record support threshold ------------------------------- #
    def test_min_support_needs_distinct_records(self):
        disease = "Dravet syndrome"
        # One supporting record but min_support=2 -> not admitted.
        recs = [
            _rec("Voltage-gated sodium channel in Dravet syndrome",
                 "The voltage-gated sodium channel is a therapeutic target "
                 "mechanism in Dravet syndrome.",
                 pmid="30000060"),
        ]
        res, key = self._run(disease, {"voltage_gated_sodium_channel": recs},
                             min_support=2)
        self.assertEqual(res["status"], "empty")
        self.assertTrue(_has(key))

    # ---- transient failure -> unavailable, NOT cached -------------------- #
    def test_transient_unavailable_not_cached(self):
        disease = "Dravet syndrome"
        res, key = self._run(
            disease,
            {"inhibitory_neurotransmission_gaba_a":
                _FakeResp(status_code=503)},
            min_support=1)
        self.assertEqual(res["status"], "unavailable")
        self.assertIsNotNone(res["error"])
        self.assertEqual(res["targets"], [])
        self.assertFalse(_has(key), "unavailable must not be cached")

    def test_timeout_unavailable_not_cached(self):
        disease = "Dravet syndrome"
        res, key = self._run(
            disease,
            {"inhibitory_neurotransmission_gaba_a":
                requests.exceptions.Timeout("timed out")},
            min_support=1)
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(_has(key))

    # ---- malformed payload -> parse_failed, NOT cached ------------------- #
    def test_malformed_payload_parse_failed_not_cached(self):
        disease = "Dravet syndrome"
        # resultList present but wrong type -> ParseFailed.
        bad = _FakeResp(json_data={"resultList": ["not", "a", "dict"]})
        res, key = self._run(
            disease,
            {"inhibitory_neurotransmission_gaba_a": bad},
            min_support=1)
        self.assertEqual(res["status"], "parse_failed")
        self.assertIsNotNone(res["error"])
        self.assertEqual(res["targets"], [])
        self.assertFalse(_has(key), "parse_failed must not poison the cache")

    def test_non_json_body_parse_failed_not_cached(self):
        disease = "Dravet syndrome"
        res, key = self._run(
            disease,
            {"inhibitory_neurotransmission_gaba_a":
                _FakeResp(raise_json=True)},
            min_support=1)
        self.assertEqual(res["status"], "parse_failed")
        self.assertFalse(_has(key))

    # ---- healthy empty served from cache --------------------------------- #
    def test_healthy_empty_cached_and_served(self):
        disease = "Marfan syndrome"
        key = _cache_key(disease, 1)
        self._keys.add(key)
        _purge(key)
        # First call: reachable, no class admitted -> empty, cached.
        with mock.patch.object(epmc.requests, "get", _make_get({})):
            res1 = epmc.discover_disease_process_targets(disease,
                                                        min_support=1)
        self.assertEqual(res1["status"], "empty")
        self.assertTrue(_has(key))
        # Second call must be served from cache: if the network were hit it
        # would raise (empty routes -> AssertionError-free, but we assert no
        # call happens by making requests.get blow up).
        def _boom(*a, **k):
            raise AssertionError("network hit despite cached empty")
        with mock.patch.object(epmc.requests, "get", _boom):
            res2 = epmc.discover_disease_process_targets(disease,
                                                        min_support=1)
        self.assertEqual(res2["status"], "empty")


if __name__ == "__main__":
    unittest.main()
