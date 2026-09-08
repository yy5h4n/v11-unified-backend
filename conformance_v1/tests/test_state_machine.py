import unittest

from conformance_v1 import hashing
from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1.state_machine import ImmutableStore


def _query(oid="Q-1", text="q"):
    return {
        "object_type": "Query", "id": oid, "schema_version": "1.0", "version": 1,
        "created_at": "2026-08-30T00:00:00Z",
        "statuses": {"semantic_status": "provisional_ai_pilot", "authorization_status": "unknown",
                     "physical_status": "unassigned", "release_status": "provisional"},
        "responsibility_id": "R-1", "text": text, "paraphrase_set": [text],
        "equivalence_verdict": "bidirectional_entailed",
    }


class TestStateMachine(unittest.TestCase):
    def setUp(self):
        self.store = ImmutableStore(CONFIG)

    def test_provisional_frozen_released_flow(self):
        oid = self.store.put(_query())
        self.assertEqual(self.store.release_status(oid), "provisional")
        self.store.freeze(oid, "FREEZE_EVENT")
        self.assertEqual(self.store.release_status(oid), "frozen")
        self.store.release(oid, "RELEASE_EVENT")
        self.assertEqual(self.store.release_status(oid), "released")

    def test_invalid_transition_rejected(self):
        oid = self.store.put(_query())
        with self.assertRaises(ConformanceError) as ctx:
            self.store.release(oid, "RELEASE_EVENT")
        self.assertEqual(ctx.exception.code, "INVALID_TRANSITION")

    def test_unknown_reason_code_rejected(self):
        oid = self.store.put(_query())
        with self.assertRaises(ConformanceError) as ctx:
            self.store.freeze(oid, "NOT_A_CODE")
        self.assertEqual(ctx.exception.code, "UNKNOWN_REASON_CODE")

    def test_post_freeze_mutation_negative_fixture(self):
        fx = CONFIG.fixture("fixtures/negative/negative_post_freeze_mutation.json")
        obj = fx["object"]
        obj["hash"] = hashing.object_hash(obj)
        oid = self.store.put(obj)
        self.store.freeze(oid, "FREEZE_EVENT")
        mutated = dict(obj)
        mutated["desired_state"] = fx["mutation"]["desired_state"]
        mutated["hash"] = hashing.object_hash(mutated)
        with self.assertRaises(ConformanceError) as ctx:
            self.store.put(mutated)
        self.assertEqual(ctx.exception.code, fx["expected_failure_code"])

    def test_frozen_object_drift_detected(self):
        obj = _query()
        obj["hash"] = hashing.object_hash(obj)
        oid = self.store.put(obj)
        self.store.freeze(oid, "FREEZE_EVENT")
        self.store._objects[oid]["text"] = "tampered"
        with self.assertRaises(ConformanceError) as ctx:
            self.store.assert_unmodified(oid)
        self.assertEqual(ctx.exception.code, "POST_FREEZE_MUTATION")

    def test_downstream_invalidation(self):
        store = ImmutableStore(CONFIG)
        resp = {
            "object_type": "CanonicalResponsibility", "id": "R-1", "schema_version": "1.0",
            "version": 1, "created_at": "2026-08-30T00:00:00Z",
            "statuses": {"semantic_status": "human_validated", "authorization_status": "authorized_agent_control",
                         "physical_status": "unassigned", "release_status": "provisional"},
            "evidence_bundle_hash": "0" * 64, "independence_unit_id": "IU",
            "desired_state": "s", "beneficiary": "b", "accountable_party": "a", "context": "c",
            "persistence": {"scope": "recurring", "supported": True}, "failure_meaning": "f",
            "override_release": "o",
            "executable_control": {"requires_agent_delegation": True, "requires_authorized_action_set": True,
                                   "authorized_action_set": ["a"], "authorization_evidence_span": "s"},
            "admission": {"agreement_alpha": 0.82, "precision": 0.9, "recall": 0.8, "confirmatory_units": 30,
                          "adjudicated_positive_units": 12, "eligible_denominator": 60, "missingness": 0.0,
                          "threshold_sensitivity": []},
            "critical_case_validated": False, "backend_influence": "none",
        }
        resp["hash"] = hashing.object_hash(resp)
        q = _query()
        q["responsibility_id"] = "R-1"
        q["hash"] = hashing.object_hash(q)
        store.put(resp)
        store.put(q)
        store.freeze("R-1", "FREEZE_EVENT")
        store.freeze("Q-1", "FREEZE_EVENT")
        affected = store.invalidate_downstream("R-1", "VERSIONING_INVALIDATION")
        self.assertIn("R-1", affected)
        self.assertIn("Q-1", affected)
        self.assertEqual(store.release_status("R-1"), "invalidated")
        self.assertEqual(store.release_status("Q-1"), "invalidated")


if __name__ == "__main__":
    unittest.main()
