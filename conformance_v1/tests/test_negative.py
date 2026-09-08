import unittest

from conformance_v1 import hashing
from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1.state_machine import ImmutableStore
from conformance_v1.sandbox import PolicySandbox


class TestNegativePostFreezeMutation(unittest.TestCase):
    def test_fixture(self):
        fx = CONFIG.fixture("fixtures/negative/negative_post_freeze_mutation.json")
        obj = dict(fx["object"])
        obj["hash"] = hashing.object_hash(obj)
        store = ImmutableStore(CONFIG)
        oid = store.put(obj)
        store.freeze(oid, "FREEZE_EVENT")
        mutated = dict(obj)
        mutated["desired_state"] = fx["mutation"]["desired_state"]
        mutated["hash"] = hashing.object_hash(mutated)
        with self.assertRaises(ConformanceError) as ctx:
            store.put(mutated)
        self.assertEqual(ctx.exception.code, fx["expected_failure_code"])


class TestNegativeSeedLeakage(unittest.TestCase):
    def test_all_cases(self):
        fx = CONFIG.fixture("fixtures/negative/negative_seed_leakage.json")
        sb = PolicySandbox(CONFIG)
        for case in fx["cases"]:
            with self.subTest(case=case["case_id"]):
                if "selection_log" in case:
                    sb.check_seed_isolation(case["search_seeds"], case["certification_seeds"], case["search_log"])
                    with self.assertRaises(ConformanceError) as ctx2:
                        sb.check_no_certification_optimization(case["selection_log"])
                    self.assertEqual(ctx2.exception.code, case["expected_failure_code"])
                elif "certified_ub" in case:
                    with self.assertRaises(ConformanceError) as ctx:
                        sb.check_bound_covers_pi_auth(case["certified_ub"], case["certified_covers_pi_auth_pub"])
                    self.assertEqual(ctx.exception.code, case["expected_failure_code"])
                elif "pi_cert_entries" in case:
                    with self.assertRaises(ConformanceError) as ctx:
                        sb.check_pi_cert_contains_pi0(case["pi_cert_entries"], case["pi_0"])
                    self.assertEqual(ctx.exception.code, case["expected_failure_code"])
                else:
                    with self.assertRaises(ConformanceError) as ctx:
                        sb.check_seed_isolation(case["search_seeds"], case["certification_seeds"], case["search_log"])
                    self.assertEqual(ctx.exception.code, case["expected_failure_code"])


if __name__ == "__main__":
    unittest.main()
