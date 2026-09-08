import unittest

from conformance_v1 import hashing, schemas
from conformance_v1.config import CONFIG
from conformance_v1.pipeline import CorpusToEpisodePipeline
from conformance_v1.opportunity import assign_primary
from conformance_v1.validator import CrossObjectValidator

ALL_OBJECTS = ["CC-001", "EU-001", "EU-002", "EU-003", "EB-001", "CR-001", "Q-001", "CT-001", "OP-001",
               "PP-001", "PP-002", "PP-003", "E-001", "E-002", "E-003"]


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        cls.result = CorpusToEpisodePipeline().run(cls.fixture)
        cls.exp = cls.fixture["expected"]

    def test_release_passes(self):
        self.assertEqual(self.result.release_verdict, self.exp["release_verdict"])
        self.assertEqual(self.result.release_codes, [])

    def test_object_hashes_frozen(self):
        for oid in ALL_OBJECTS:
            with self.subTest(object=oid):
                self.assertEqual(self.result.objects[oid]["hash"], self.exp["object_hashes"][oid])

    def test_hashes_recomputable(self):
        for oid in ALL_OBJECTS:
            with self.subTest(object=oid):
                obj = self.result.objects[oid]
                self.assertEqual(hashing.object_hash(obj), obj["hash"])

    def test_all_objects_pass_schema(self):
        for oid in ALL_OBJECTS:
            with self.subTest(object=oid):
                obj = self.result.objects[oid]
                self.assertEqual(schemas.validate(obj, schemas.schema_for_object(obj)), [])

    def test_statuses_independent(self):
        self.assertEqual(self.result.objects["CR-001"]["statuses"]["semantic_status"], self.exp["semantic_status"])
        self.assertEqual(self.result.objects["CR-001"]["statuses"]["authorization_status"], self.exp["authorization_status"])
        for eid in ("E-001", "E-002", "E-003"):
            ep = self.result.objects[eid]
            self.assertEqual(ep["physical_label"], self.exp["physical_labels"][eid])
            self.assertEqual(ep["statuses"]["physical_status"], self.exp["physical_labels"][eid])

    def test_assignment_and_order(self):
        fx = self.fixture
        candidates = [
            {"physical_process_id": g["process_id"], "responsibility_id": "CR-001",
             "certificate_margin": g["certificate_margin"]}
            for g in fx["opportunity_groups"]
        ]
        primary = assign_primary(candidates, ["PP-001", "PP-002", "PP-003"], CONFIG)
        self.assertEqual(primary, self.exp["assignment"])
        self.assertEqual(list(primary.keys()), self.exp["assignment_order"])

    def test_splits(self):
        for eid in ("E-001", "E-002", "E-003"):
            self.assertEqual(self.result.splits[eid], self.exp["splits"][eid])
            self.assertEqual(self.result.objects[eid]["split"], self.exp["splits"][eid])

    def test_replay_digests_and_terminal_verdicts(self):
        for eid in ("E-001", "E-002", "E-003"):
            self.assertEqual(self.result.objects[eid]["replay_digest"], self.exp["replay_digests"][eid])
            self.assertEqual(self.result.objects[eid]["terminal_verdict"], self.exp["terminal_verdicts"][eid])
            self.assertEqual(self.result.verdicts[eid]["replay_digest"], self.exp["replay_digests"][eid])

    def test_sensitivity(self):
        self.assertEqual(self.result.sensitivity, self.exp["sensitivity"])
        self.assertTrue(self.result.sensitivity["material"])
        self.assertEqual(self.result.sensitivity["utility_delta"], 1.0)

    def test_validator_clean(self):
        v = CrossObjectValidator(CONFIG)
        self.assertEqual(v.check_all(self.result.objects), [])
        self.assertEqual(v.check_coverage_completeness(self.result.objects), [])

    def test_no_gold_leakage(self):
        for eid in ("E-001", "E-002", "E-003"):
            ep = self.result.objects[eid]
            self.assertEqual(ep["private_record"]["gold_actions"], [])
            for forbidden in ("evaluator_clauses", "gold_actions", "witness"):
                self.assertNotIn(forbidden, ep["public_record"])

    def test_lineage_parent_hashes(self):
        resp = self.result.objects["CR-001"]
        self.assertEqual(resp["evidence_bundle_hash"], self.result.objects["EB-001"]["hash"])
        for child_key, parent in (("query_id", "Q-001"),):
            self.assertEqual(self.result.objects["CT-001"][child_key], parent)
        self.assertEqual(self.result.objects["CT-001"]["query_id"], self.result.objects["Q-001"]["id"])

    def test_failure_codes_exercised_in_catalog(self):
        for code in self.exp["failure_codes_exercised"]:
            self.assertIn(code, CONFIG.codes)

    def test_pipeline_rejects_non_synthetic(self):
        fx = self.fixture
        bad = {
            "meta": {"is_synthetic_fixture": True, "schema_version": "1.0"},
            "corpus": fx["corpus"],
            "bundle_draft": fx["bundle_draft"],
            "responsibility_draft": fx["responsibility_draft"],
            "query_draft": fx["query_draft"],
            "contract_draft": fx["contract_draft"],
            "predicate_draft": fx["predicate_draft"],
            "process_drafts": [dict(fx["process_drafts"][0], source="real_backend")],
            "opportunity_groups": fx["opportunity_groups"],
            "expected": fx["expected"],
        }
        from conformance_v1.config import ConformanceError
        with self.assertRaises(ConformanceError):
            CorpusToEpisodePipeline().run(bad)


if __name__ == "__main__":
    unittest.main()
