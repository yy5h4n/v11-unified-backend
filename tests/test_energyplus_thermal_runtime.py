import json, unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "generated/energyplus_thermal_runtime_v1/gate_report.json"
class TestThermalRuntimeGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.data = json.loads(REPORT.read_text())
    def test_runtime_action_is_causal_and_deterministic(self):
        self.assertTrue(self.data["passed"]); self.assertTrue(self.data["runtime_action_adapter_gate"])
        self.assertTrue(self.data["action_sensitivity_gate"]); self.assertTrue(self.data["determinism_gate"])
        self.assertGreater(self.data["maximum_zone_temperature_delta_c"], 0)
    def test_evaluator_still_fail_closed(self):
        self.assertFalse(self.data["evaluator_gate"]); self.assertFalse(self.data["episode_eligible"]); self.assertEqual(self.data["episode_count"], 0)
    def test_cross_run_digests(self):
        d = self.data["trajectory_digests"]; self.assertEqual(d["low_1"], d["low_2"]); self.assertEqual(d["high_1"], d["high_2"])
    def test_causal_traces_are_pinned(self):
        import hashlib
        for ref in self.data["causal_trace_refs"].values():
            path=ROOT/ref["path"]; self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),ref["sha256"])
            first=__import__("json").loads(path.read_text().splitlines()[0])
            self.assertTrue({"occupant_count","outdoor_temperature_c","facility_electric_demand_w"}<=set(first))
if __name__ == "__main__": unittest.main()
