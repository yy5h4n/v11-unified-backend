import unittest

from conformance_v1 import hashing
from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1.opportunity import OpportunityClassifier
from conformance_v1.sandbox import PolicySandbox
from conformance_v1.graphs import split_leakage_audit
from conformance_v1.validator import CrossObjectValidator

CLASSIFIER_GOLDEN = [
    ("golden_positive.json", "positive_opportunity"),
    ("golden_boundary.json", "boundary_opportunity"),
    ("golden_certified_noop.json", "certified_no_opportunity"),
    ("golden_unsupported.json", "unsupported"),
]


class TestClassifierGolden(unittest.TestCase):
    def test_physical_labels(self):
        clf = OpportunityClassifier(CONFIG)
        for name, expected in CLASSIFIER_GOLDEN:
            with self.subTest(fixture=name):
                fx = CONFIG.fixture(f"fixtures/golden/{name}")
                label = clf.classify(fx["opportunity_inputs"])
                self.assertEqual(label, fx["expected"]["physical_label"])
                self.assertEqual(label, expected)

    def test_boundary_overclaim_raises(self):
        fx = CONFIG.fixture("fixtures/golden/golden_boundary.json")
        sb = PolicySandbox(CONFIG)
        with self.assertRaises(ConformanceError) as ctx:
            sb.check_bound_covers_pi_auth(fx["opportunity_inputs"]["certified_ub"],
                                          fx["opportunity_inputs"]["certified_covers_pi_auth"])
        self.assertEqual(ctx.exception.code, "BOUND_NOT_COVERING_PI_AUTH")


def _out_of_scope_responsibility(oid):
    obj = {
        "object_type": "CanonicalResponsibility", "id": oid, "schema_version": "1.0", "version": 1,
        "created_at": "2026-08-30T00:00:00Z",
        "statuses": {"semantic_status": "out_of_scope", "authorization_status": "unknown",
                     "physical_status": "unassigned", "release_status": "frozen"},
        "evidence_bundle_hash": "0" * 64, "independence_unit_id": "IU",
        "desired_state": "s", "beneficiary": "", "accountable_party": "", "context": "c",
        "persistence": {"scope": "recurring", "supported": False}, "failure_meaning": "",
        "override_release": "",
        "executable_control": {"requires_agent_delegation": False, "requires_authorized_action_set": False,
                               "authorized_action_set": None, "authorization_evidence_span": ""},
        "admission": {"agreement_alpha": 0.0, "precision": 0.0, "recall": 0.0, "confirmatory_units": 0,
                      "adjudicated_positive_units": 0, "eligible_denominator": 0, "missingness": 1.0,
                      "threshold_sensitivity": []},
        "critical_case_validated": False, "backend_influence": "none",
    }
    obj["hash"] = hashing.object_hash(obj)
    return obj


class TestSemanticUnknownGolden(unittest.TestCase):
    def test_not_promotable(self):
        fx = CONFIG.fixture("fixtures/golden/golden_semantic_unknown.json")
        self.assertFalse(fx["evidence_insufficiency"]["purpose_supported"])
        v = CrossObjectValidator(CONFIG)
        objects = {
            "CC-S": {"object_type": "CorpusCard", "id": "CC-S", "hash": "a" * 64},
            "EB-S": {"object_type": "EvidenceBundle", "id": "EB-S", "corpus_card_id": "CC-S", "hash": "0" * 64},
            "CR-S": _out_of_scope_responsibility("CR-S"),
        }
        self.assertEqual(v.check_lineage(objects), [])
        q = {"object_type": "Query", "id": "Q-S", "schema_version": "1.0", "version": 1,
             "created_at": "2026-08-30T00:00:00Z",
             "statuses": {"semantic_status": "out_of_scope", "authorization_status": "unknown",
                          "physical_status": "unassigned", "release_status": "frozen"},
             "responsibility_id": "CR-S", "text": "q", "paraphrase_set": ["q"],
             "equivalence_verdict": "bidirectional_entailed"}
        q["hash"] = hashing.object_hash(q)
        objects["Q-S"] = q
        codes = v.check_lineage(objects)
        self.assertEqual(fx["expected"]["promotion_failure_code"], "INVALID_TRANSITION")
        self.assertIn("INVALID_TRANSITION", codes)
        self.assertFalse(fx["expected"]["promotable"])


class TestFailureClosedGolden(unittest.TestCase):
    def test_split_leak_fails_closed(self):
        fx = CONFIG.fixture("fixtures/golden/golden_failure_closed.json")
        scenario = fx["scenario"]
        codes = split_leakage_audit(scenario["episodes"],
                                    {e["id"]: e["split"] for e in scenario["episodes"]},
                                    scenario["physical_component_of"])
        self.assertEqual(codes, ["SPLIT_LEAKAGE"])
        self.assertEqual(fx["expected"]["release_verdict"], "FAILED_CLOSED")
        # release QA treats blocking codes as FAILED_CLOSED
        blocking = [c for c in codes if c not in ("QUOTA_INFEASIBLE", "BOUND_NOT_COVERING_PI_AUTH")]
        self.assertEqual(blocking, ["SPLIT_LEAKAGE"])


class TestPositiveGoldenReferencesE2E(unittest.TestCase):
    def test_positive_label_matches_e2e(self):
        fx = CONFIG.fixture("fixtures/golden/golden_positive.json")
        ref = fx["e2e_reference"]
        # fixture path is relative to conformance_v1; resolve and check the label
        self.assertEqual(ref, "fixtures/e2e/corpus_to_episode.json#expected.physical_labels.E-001")
        e2e = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        self.assertEqual(e2e["expected"]["physical_labels"]["E-001"], fx["expected"]["physical_label"])


if __name__ == "__main__":
    unittest.main()
