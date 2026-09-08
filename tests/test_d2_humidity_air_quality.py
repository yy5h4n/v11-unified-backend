import hashlib
import json
import math
from pathlib import Path
import unittest

from d2_humidity_air_quality_adapter import EnergyPlusHumidityAirQualityAdapter

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "generated/d2_humidity_air_quality_v1/gate_report.json"


class TestD2HumidityAirQualityBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(REPORT.read_text(encoding="utf-8"))

    def test_real_runtime_is_causal(self):
        self.assertTrue(self.data["passed"])
        self.assertEqual(self.data["status"], "REAL_RUNTIME_PROBED")
        self.assertTrue(self.data["runtime_stepping_gate"])
        self.assertTrue(self.data["action_available_gate"])
        self.assertTrue(self.data["action_sensitivity_gate"])
        self.assertTrue(self.data["provenance_gate"])
        self.assertGreater(self.data["maximum_observed_deltas"]["co2_ppm"], 0.0)
        self.assertGreater(self.data["maximum_observed_deltas"]["generic_contaminant_ppm"], 0.0)

    def test_four_replay_arms_are_present(self):
        arms = self.data["replay_arms"]
        self.assertEqual(self.data["replay_arm_count"], 4)
        self.assertEqual(set(arms), {"ventilation_off_1", "ventilation_off_2", "ventilation_on_1", "ventilation_on_2"})
        self.assertEqual({arm["action"] for arm in arms.values()}, {0.0, 1.0})

    def test_reset_replay_is_deterministic(self):
        digests = self.data["trajectory_digests"]
        self.assertEqual(digests["ventilation_off_1"], digests["ventilation_off_2"])
        self.assertEqual(digests["ventilation_on_1"], digests["ventilation_on_2"])

    def test_traces_are_hash_pinned_and_finite(self):
        for ref in self.data["causal_trace_refs"].values():
            path = ROOT / ref["path"]
            self.assertTrue(path.is_file())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), ref["sha256"])
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), ref["row_count"])
            self.assertTrue(all(math.isfinite(value) for row in rows for value in row["observation"].values()))

    def test_report_is_backend_only(self):
        serialized = json.dumps(self.data, sort_keys=True).lower()
        for forbidden in ("episode", "evaluator", "responsibility", "contract", "eligibility", "threshold"):
            self.assertNotIn(forbidden, serialized)

    def test_agent_closed_loop_is_live_and_action_changes_future(self):
        adapter = EnergyPlusHumidityAirQualityAdapter()
        try:
            initial = adapter.reset(seed=17)
            self.assertEqual(initial, adapter.observe())
            self.assertEqual(adapter.legal_actions()["action_ventilation_schedule"]["range"], [0.0, 1.0])
            first = adapter.step(1.0)
            self.assertEqual(set(first), {"observation", "time_seconds", "action", "terminated", "truncated", "done", "delta_t_seconds", "steps", "info"})
            self.assertEqual(first["action"], 1.0)
            self.assertEqual(first["time_seconds"], 1200)
            self.assertFalse(first["done"])
            second = adapter.step(0.0)
            self.assertEqual(second["time_seconds"], 1800)
            self.assertNotEqual(first["observation"]["co2_ppm"], second["observation"]["co2_ppm"])
            self.assertEqual(adapter.observe(), second["observation"])
        finally:
            adapter.close()

    def test_agent_reset_replay_and_multi_step_delta_are_deterministic(self):
        adapter = EnergyPlusHumidityAirQualityAdapter()
        try:
            trace_a = [adapter.reset(0), adapter.step(1.0), adapter.step(0.0, steps=2)]
            trace_b = [adapter.reset(0), adapter.step(1.0), adapter.step(0.0, steps=2)]
            self.assertEqual(trace_a, trace_b)
            self.assertEqual(trace_a[-1]["steps"], 2)
            self.assertEqual(trace_a[-1]["time_seconds"], 2400)
            with self.assertRaises(ValueError):
                adapter.step(0.0, delta_t=601)
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()
