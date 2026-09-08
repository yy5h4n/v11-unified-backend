import json, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; REPORT = ROOT / "generated/energyplus_humidity_runtime_v1/gate_report.json"
class TestHumidityRuntimeGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.data = json.loads(REPORT.read_text())
    def test_runtime_opening_is_causal(self):
        self.assertTrue(self.data["passed"]); self.assertTrue(self.data["runtime_action_adapter_gate"])
        self.assertTrue(self.data["action_sensitivity_gate"]); self.assertTrue(self.data["determinism_gate"])
        self.assertGreater(self.data["maximum_observed_opening_factor_delta"], .99)
        self.assertTrue(self.data["maximum_relative_humidity_delta_pct"] > 0 or self.data["maximum_zone_temperature_delta_c"] > 0)
    def test_evaluator_fail_closed(self):
        self.assertFalse(self.data["evaluator_gate"]); self.assertFalse(self.data["episode_eligible"]); self.assertEqual(self.data["episode_count"], 0)
    def test_cross_run_digests(self):
        d=self.data["trajectory_digests"]; self.assertEqual(d["closed_1"],d["closed_2"]); self.assertEqual(d["open_1"],d["open_2"])
    def test_causal_traces_are_pinned(self):
        import hashlib
        for ref in self.data["causal_trace_refs"].values():
            path=ROOT/ref["path"]; self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),ref["sha256"])
if __name__ == "__main__": unittest.main()
