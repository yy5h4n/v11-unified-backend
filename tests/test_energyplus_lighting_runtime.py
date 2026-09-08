import hashlib, json, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class TestLightingRuntimeGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.data=json.loads((ROOT/"generated/energyplus_lighting_runtime_v1/gate_report.json").read_text())
    def test_gates(self):
        self.assertTrue(self.data["passed"]); self.assertTrue(self.data["action_sensitivity_gate"]); self.assertTrue(self.data["determinism_gate"])
        self.assertFalse(self.data["evaluator_gate"]); self.assertFalse(self.data["episode_eligible"]); self.assertEqual(self.data["episode_count"],0)
    def test_lighting_only(self):
        self.assertTrue(self.data["runtime_action_adapter"]["supports_lighting_action"]); self.assertFalse(self.data["runtime_action_adapter"]["supports_blind_action"])
        self.assertEqual(self.data["physical_process_id"],"energyplus:lighting_control_only")
        self.assertFalse(self.data["observability_gate_for_illuminance_contract"])
    def test_causal_traces_pinned(self):
        for ref in self.data["causal_trace_refs"].values():
            path=ROOT/ref["path"]; self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),ref["sha256"])
if __name__ == "__main__": unittest.main()
