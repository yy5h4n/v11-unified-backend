import json, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "generated/energyplus_thermal_action_v1/gate_report.json"

class TestThermalActionGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.data = json.loads(REPORT.read_text(encoding="utf-8"))
    def test_real_causal_gate(self):
        self.assertTrue(self.data["passed"])
        self.assertTrue(self.data["action_available_gate"])
        self.assertTrue(self.data["action_sensitivity_gate"])
        self.assertTrue(self.data["determinism_gate"])
        self.assertGreater(self.data["maximum_zone_temperature_delta_c"], 0)
        self.assertGreater(self.data["maximum_heating_rate_delta_w"], 0)
    def test_no_episode_leakage(self):
        self.assertFalse(self.data["evaluator_gate"])
        self.assertFalse(self.data["episode_eligible"])
        self.assertEqual(self.data["episode_count"], 0)
        self.assertFalse(self.data["gold_actions_released"])
    def test_replicate_digests_match(self):
        digests = self.data["trajectory_digests"]
        self.assertEqual(digests["baseline_replicate_1"], digests["baseline_replicate_2"])
        self.assertEqual(digests["intervention_replicate_1"], digests["intervention_replicate_2"])

if __name__ == "__main__": unittest.main()
