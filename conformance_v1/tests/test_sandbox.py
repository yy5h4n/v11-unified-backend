import unittest

from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1.sandbox import PolicySandbox


class TestPolicySandbox(unittest.TestCase):
    def setUp(self):
        self.sb = PolicySandbox(CONFIG)
        self.public = ["living_room_temp", "outside_temp", "season"]
        self.actions = ["heat_setpoint_up", "none"]

    def test_admissible_policy(self):
        self.sb.check_policy_admissible(
            {"id": "pi_rule_1", "inputs": ["living_room_temp", "outside_temp"], "actions": ["heat_setpoint_up", "none"]},
            self.public, self.actions,
        )

    def test_forbidden_input_rejected(self):
        with self.assertRaises(ConformanceError) as ctx:
            self.sb.check_policy_admissible(
                {"id": "p", "inputs": ["episode_id_xxx"], "actions": ["none"]},
                self.public, self.actions,
            )
        self.assertEqual(ctx.exception.code, "POLICY_UNADMISSIBLE")

    def test_unauthorized_action_rejected(self):
        with self.assertRaises(ConformanceError) as ctx:
            self.sb.check_policy_admissible(
                {"id": "p", "inputs": [], "actions": ["reset_all"]},
                self.public, self.actions,
            )
        self.assertEqual(ctx.exception.code, "POLICY_UNADMISSIBLE")

    def test_seed_leakage_negative_fixture(self):
        fx = CONFIG.fixture("fixtures/negative/negative_seed_leakage.json")
        for case in fx["cases"]:
            with self.subTest(case=case["case_id"]):
                if "selection_log" in case:
                    self.sb.check_seed_isolation(case["search_seeds"], case["certification_seeds"], case["search_log"])
                    with self.assertRaises(ConformanceError) as ctx2:
                        self.sb.check_no_certification_optimization(case["selection_log"])
                    self.assertEqual(ctx2.exception.code, "CERTIFICATION_OPTIMIZATION")
                elif "certified_ub" in case:
                    with self.assertRaises(ConformanceError) as ctx:
                        self.sb.check_bound_covers_pi_auth(case["certified_ub"], case["certified_covers_pi_auth_pub"])
                    self.assertEqual(ctx.exception.code, "BOUND_NOT_COVERING_PI_AUTH")
                elif "pi_cert_entries" in case:
                    with self.assertRaises(ConformanceError) as ctx:
                        self.sb.check_pi_cert_contains_pi0(case["pi_cert_entries"], case["pi_0"])
                    self.assertEqual(ctx.exception.code, "SEED_LEAKAGE")
                else:
                    with self.assertRaises(ConformanceError) as ctx:
                        self.sb.check_seed_isolation(case["search_seeds"], case["certification_seeds"], case["search_log"])
                    self.assertEqual(ctx.exception.code, case["expected_failure_code"])

    def test_clean_seed_isolation(self):
        self.sb.check_seed_isolation([101, 202, 303], [404, 505, 606], [{"phase": "search", "seed_id": 101}])

    def test_pi0_in_pi_cert(self):
        entries = CONFIG.release_config["pi_cert"]["entries"]
        self.sb.check_pi_cert_contains_pi0(entries, CONFIG.release_config["pi_0"])

    def test_noop_acceptability(self):
        accept = CONFIG.release_config["authorization_acceptable_policies"]
        self.sb.check_noop_acceptable("authorized_agent_control", accept)
        with self.assertRaises(ConformanceError) as ctx:
            self.sb.check_noop_acceptable("refused", accept)
        self.assertEqual(ctx.exception.code, "POLICY_UNADMISSIBLE")

    def test_pi0_is_noop_acceptable_per_config(self):
        self.assertTrue(CONFIG.release_config["policy"]["noop_is_acceptable"])


if __name__ == "__main__":
    unittest.main()
