import unittest

from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1.opportunity import OpportunityClassifier, normalized_certificate_margin, assign_primary


class TestOpportunityClassifier(unittest.TestCase):
    def setUp(self):
        self.clf = OpportunityClassifier(CONFIG)

    def test_thresholds_valid(self):
        self.clf.validate_thresholds()

    def test_positive(self):
        label = self.clf.classify({
            "delta_lcb": 0.35, "certified_ub": None, "certified_covers_pi_auth": False,
            "pi_hat_acceptable": True, "pi_0_acceptable": True,
            "capability_supported": True, "information_supported": True,
        })
        self.assertEqual(label, "positive_opportunity")

    def test_boundary_when_pi_cert_only(self):
        label = self.clf.classify({
            "delta_lcb": 0.05, "certified_ub": 0.01, "certified_covers_pi_auth": False,
            "pi_hat_acceptable": True, "pi_0_acceptable": True,
            "capability_supported": True, "information_supported": True,
        })
        self.assertEqual(label, "boundary_opportunity")

    def test_certified_noop(self):
        label = self.clf.classify({
            "delta_lcb": None, "certified_ub": 0.01, "certified_covers_pi_auth": True,
            "pi_hat_acceptable": True, "pi_0_acceptable": True,
            "capability_supported": True, "information_supported": True,
        })
        self.assertEqual(label, "certified_no_opportunity")

    def test_unsupported(self):
        label = self.clf.classify({
            "delta_lcb": 0.35, "certified_ub": None, "certified_covers_pi_auth": False,
            "pi_hat_acceptable": True, "pi_0_acceptable": True,
            "capability_supported": False, "information_supported": True,
        })
        self.assertEqual(label, "unsupported")

    def test_invalid_threshold_config(self):
        import copy
        bad = copy.deepcopy(CONFIG)
        bad.release_config["opportunity_thresholds"]["delta_noop"] = 0.5
        bad.release_config["opportunity_thresholds"]["delta_positive"] = 0.4
        clf = OpportunityClassifier(bad)
        with self.assertRaises(ConformanceError) as ctx:
            clf.validate_thresholds()
        self.assertEqual(ctx.exception.code, "OPPORTUNITY_THRESHOLD_INVALID")


class TestMarginsAndAssignment(unittest.TestCase):
    def test_normalized_margin(self):
        # delta_noop=0.02, delta_positive=0.10
        self.assertAlmostEqual(normalized_certificate_margin(0.06, CONFIG), 0.5)
        self.assertEqual(normalized_certificate_margin(0.02, CONFIG), 0.0)
        self.assertAlmostEqual(normalized_certificate_margin(0.10, CONFIG), 1.0)
        self.assertEqual(normalized_certificate_margin(0.5, CONFIG), 1.0)

    def test_assign_primary_picks_higher_margin(self):
        candidates = [
            {"physical_process_id": "PP-001", "responsibility_id": "CR-001", "certificate_margin": 0.06},
            {"physical_process_id": "PP-001", "responsibility_id": "CR-002", "certificate_margin": 0.04},
            {"physical_process_id": "PP-002", "responsibility_id": "CR-003", "certificate_margin": 0.05},
        ]
        primary = assign_primary(candidates, ["PP-001", "PP-002"], CONFIG)
        self.assertEqual(primary, {"PP-001": "CR-001", "PP-002": "CR-003"})

    def test_assign_primary_lexical_tie_break(self):
        candidates = [
            {"physical_process_id": "PP-001", "responsibility_id": "CR-002", "certificate_margin": 0.06},
            {"physical_process_id": "PP-001", "responsibility_id": "CR-001", "certificate_margin": 0.06},
        ]
        primary = assign_primary(candidates, ["PP-001"], CONFIG)
        self.assertEqual(primary, {"PP-001": "CR-001"})

    def test_e2e_assignment(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        candidates = [
            {"physical_process_id": g["process_id"], "responsibility_id": "CR-001",
             "certificate_margin": g["certificate_margin"]}
            for g in fx["opportunity_groups"]
        ]
        primary = assign_primary(candidates, ["PP-001", "PP-002", "PP-003"], CONFIG)
        self.assertEqual(primary, fx["expected"]["assignment"])


if __name__ == "__main__":
    unittest.main()
