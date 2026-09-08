import unittest

from conformance_v1 import hashing
from conformance_v1.config import CONFIG
from conformance_v1.pipeline import CorpusToEpisodePipeline
from conformance_v1.validator import CrossObjectValidator


def _unit(overrides=None):
    unit = {
        "object_type": "EvidenceUnit", "id": "EU-T", "schema_version": "1.0", "version": 1,
        "created_at": "2026-08-30T00:00:00Z",
        "statuses": {"semantic_status": "provisional_ai_pilot", "authorization_status": "unknown",
                     "physical_status": "unassigned", "release_status": "frozen"},
        "corpus_card_id": "CC-1",
        "source": {"source_type": "direct_interview_diary_quotation", "stable_locator": "loc",
                   "span_or_rule": "span", "collection_unit": "unit"},
        "household_id": "hh-1", "participant_id": "P1", "authorship": "authored",
        "verbatim_text": "text", "paper_described_example": False,
        "semantics": {"desired_state": "s", "beneficiary": "b", "context": "c", "temporal_scope": "t",
                      "ownership_evidence": "o", "failure_meaning": "f", "constraints": "c",
                      "tradeoffs": "t", "override_release": "r"},
        "device_action": {"means": "m", "device": "d", "action": "a"},
        "evidence_limitations": "lim",
        "propositions": [
            {"code": "reported_purpose", "value": "p", "inference_matrix_row": "Direct interview/diary quotation",
             "matrix_row_source_type": "direct_interview_diary_quotation"},
        ],
    }
    if overrides:
        unit.update(overrides)
    unit["hash"] = hashing.object_hash(unit)
    return unit


class TestCrossObjectValidator(unittest.TestCase):
    def setUp(self):
        self.v = CrossObjectValidator(CONFIG)

    def test_e2e_objects_clean(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        result = CorpusToEpisodePipeline().run(fx)
        self.assertEqual(result.release_verdict, "PASS")
        self.assertEqual(self.v.check_all(result.objects), [])

    def test_unsupported_inference_recurrence_from_quote(self):
        unit = _unit({"propositions": [
            {"code": "recurrence", "value": "every night", "inference_matrix_row": "Direct interview/diary quotation",
             "matrix_row_source_type": "direct_interview_diary_quotation"}]})
        self.assertIn("UNSUPPORTED_INFERENCE", self.v.check_object(unit))

    def test_unsupported_inference_wrong_row(self):
        unit = _unit({"propositions": [
            {"code": "reported_purpose", "value": "p", "inference_matrix_row": "Naturally authored Routine",
             "matrix_row_source_type": "direct_interview_diary_quotation"}]})
        self.assertIn("UNSUPPORTED_INFERENCE", self.v.check_object(unit))

    def test_hash_mismatch(self):
        unit = _unit()
        unit["verbatim_text"] = "tampered"
        self.assertIn("HASH_MISMATCH", self.v.check_object(unit))

    def test_status_separation_violation(self):
        unit = _unit()
        unit["status"] = unit.pop("statuses")
        self.assertIn("STATUS_SEPARATION_VIOLATION", self.v.check_object(unit))

    def test_query_adds_obligation(self):
        q = {"object_type": "Query", "id": "Q-T", "schema_version": "1.0", "version": 1,
             "created_at": "2026-08-30T00:00:00Z",
             "statuses": {"semantic_status": "human_validated", "authorization_status": "authorized_agent_control",
                          "physical_status": "unassigned", "release_status": "frozen"},
             "responsibility_id": "R", "text": "q", "paraphrase_set": ["q"],
             "equivalence_verdict": "adds_obligation"}
        q["hash"] = hashing.object_hash(q)
        self.assertIn("ENTAILMENT_FAILURE", self.v.check_object(q))

    def test_contract_gold_action(self):
        contract = {
            "object_type": "Contract", "id": "CT-G", "schema_version": "1.0", "version": 1,
            "created_at": "2026-08-30T00:00:00Z",
            "statuses": {"semantic_status": "human_validated", "authorization_status": "authorized_agent_control",
                         "physical_status": "unassigned", "release_status": "frozen"},
            "query_id": "Q", "anti_gaming_rule": "terminal_guard_band", "priority_order": ["C1"],
            "clauses": [{"clause_id": "C1", "kind": "conditional_obligation",
                         "formula": {"op": "within", "trigger": {"op": "const", "value": True},
                                     "obligation": {"op": "action", "variable": "heat_setpoint_up", "cmp": "eq", "value": True},
                                     "deadline_intervals": 2},
                         "coverage": [{"responsibility_field": "desired_state", "clause_ref": "C1",
                                       "transformation_rule": "t", "evidence_span_or_design_choice": "e",
                                       "reviewer_A": "pass", "reviewer_B": "pass", "adjudication": "pass",
                                       "adjudication_reason": "r"}]}],
            "rejected_predicates": [],
        }
        contract["hash"] = hashing.object_hash(contract)
        self.assertIn("GOLD_ACTION_IN_CONTRACT", self.v.check_object(contract))

    def test_contract_missing_coverage(self):
        contract = {
            "object_type": "Contract", "id": "CT-M", "schema_version": "1.0", "version": 1,
            "created_at": "2026-08-30T00:00:00Z",
            "statuses": {"semantic_status": "human_validated", "authorization_status": "authorized_agent_control",
                         "physical_status": "unassigned", "release_status": "frozen"},
            "query_id": "Q", "anti_gaming_rule": "terminal_guard_band", "priority_order": ["C1"],
            "clauses": [{"clause_id": "C1", "kind": "hard_invariant",
                         "formula": {"op": "const", "value": True}, "coverage": []}],
            "rejected_predicates": [],
        }
        contract["hash"] = hashing.object_hash(contract)
        codes = self.v.check_object(contract)
        self.assertIn("MISSING_REQUIRED_COVERAGE", codes)

    def test_semantic_unknown_not_promotable(self):
        objects = {}
        resp = {
            "object_type": "CanonicalResponsibility", "id": "CR-S", "schema_version": "1.0", "version": 1,
            "created_at": "2026-08-30T00:00:00Z",
            "statuses": {"semantic_status": "out_of_scope", "authorization_status": "unknown",
                         "physical_status": "unassigned", "release_status": "frozen"},
            "evidence_bundle_hash": "0" * 64, "independence_unit_id": "IU",
            "desired_state": "s", "beneficiary": "b", "accountable_party": "a", "context": "c",
            "persistence": {"scope": "recurring", "supported": True}, "failure_meaning": "f",
            "override_release": "o",
            "executable_control": {"requires_agent_delegation": False, "requires_authorized_action_set": False,
                                   "authorized_action_set": None, "authorization_evidence_span": "s"},
            "admission": {"agreement_alpha": 0.0, "precision": 0.0, "recall": 0.0, "confirmatory_units": 0,
                          "adjudicated_positive_units": 0, "eligible_denominator": 0, "missingness": 1.0,
                          "threshold_sensitivity": []},
            "critical_case_validated": False, "backend_influence": "none",
        }
        resp["hash"] = hashing.object_hash(resp)
        objects["CR-S"] = resp
        objects["CC-S"] = {"object_type": "CorpusCard", "id": "CC-S", "hash": "a" * 64}
        objects["EB-S"] = {"object_type": "EvidenceBundle", "id": "EB-S", "corpus_card_id": "CC-S", "hash": "0" * 64}
        # no downstream: clean
        self.assertEqual(self.v.check_lineage(objects), [])
        q = {"object_type": "Query", "id": "Q-S", "schema_version": "1.0", "version": 1,
             "created_at": "2026-08-30T00:00:00Z",
             "statuses": {"semantic_status": "out_of_scope", "authorization_status": "unknown",
                          "physical_status": "unassigned", "release_status": "frozen"},
             "responsibility_id": "CR-S", "text": "q", "paraphrase_set": ["q"],
             "equivalence_verdict": "bidirectional_entailed"}
        q["hash"] = hashing.object_hash(q)
        objects["Q-S"] = q
        self.assertIn("INVALID_TRANSITION", self.v.check_lineage(objects))

    def test_coverage_completeness(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        result = CorpusToEpisodePipeline().run(fx)
        self.assertEqual(self.v.check_coverage_completeness(result.objects), [])


if __name__ == "__main__":
    unittest.main()
