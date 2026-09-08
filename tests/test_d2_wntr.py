import hashlib
import json
import math
from pathlib import Path
import sys
import unittest

from unified_compiler.adapters.d2_wntr import WNTRActionError, WNTRResidentialWaterAdapter


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "generated/d2_wntr_v1/gate_report.json"


class TestD2WNTRBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(REPORT.read_text(encoding="utf-8"))

    def test_real_wntr_runtime_gate(self):
        self.assertTrue(self.data["passed"])
        self.assertEqual(self.data["status"], "REAL_RUNTIME_PROBED")
        self.assertEqual(self.data["backend_engine"], "WNTRSimulator")
        self.assertEqual(self.data["backend_version"], "1.3.0")
        self.assertTrue(self.data["runtime_stepping_gate"])
        self.assertTrue(self.data["provenance_gate"])
        self.assertFalse(self.data["provenance"]["epanet_library_present"])

    def test_action_changes_later_pressure_flow_tank_and_leak(self):
        self.assertTrue(self.data["action_available_gate"])
        self.assertTrue(self.data["action_sensitivity_gate"])
        for role, delta in self.data["maximum_observed_deltas"].items():
            self.assertGreater(delta, 0.0, role)

    def test_deterministic_double_replays(self):
        self.assertTrue(self.data["determinism_gate"])
        arms = self.data["replay_arms"]
        self.assertEqual(set(arms), {"valve_closed_1", "valve_closed_2", "valve_open_1", "valve_open_2"})
        self.assertEqual(arms["valve_closed_1"]["trace_digest"], arms["valve_closed_2"]["trace_digest"])
        self.assertEqual(arms["valve_open_1"]["trace_digest"], arms["valve_open_2"]["trace_digest"])

    def test_trace_files_are_hash_pinned_and_finite(self):
        for ref in self.data["causal_trace_refs"].values():
            path = ROOT / ref["path"]
            self.assertTrue(path.is_file())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), ref["sha256"])
            rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(len(rows), ref["row_count"])
            self.assertTrue(all(math.isfinite(value) for row in rows for value in row["observation"].values()))

    def test_adapter_fails_closed_on_illegal_action(self):
        adapter = WNTRResidentialWaterAdapter()
        adapter.reset(0)
        with self.assertRaises(WNTRActionError):
            adapter.run(0.5)

    def test_parent_process_is_not_polluted_by_wntr_runtime(self):
        self.assertNotIn("wntr", sys.modules)
        self.assertNotIn(str(ROOT / "shared_runtime/wntr-site-packages"), sys.path)

    def test_agent_closed_loop_is_live_and_action_changes_future(self):
        adapter = WNTRResidentialWaterAdapter()
        try:
            initial = adapter.reset(seed=17)
            self.assertEqual(initial, adapter.observe())
            self.assertEqual(adapter.legal_actions()["action_isolation_valve_open"]["values"], [0.0, 1.0])
            first = adapter.step(1.0)
            self.assertEqual(set(first), {"observation", "time_seconds", "action", "terminated", "truncated", "done", "delta_t_seconds", "steps", "info"})
            self.assertEqual(first["time_seconds"], 3600)
            self.assertFalse(first["done"])
            second = adapter.step(0.0)
            self.assertEqual(second["time_seconds"], 7200)
            self.assertNotEqual(first["observation"]["pressure_kitchen_m"], second["observation"]["pressure_kitchen_m"])
            self.assertEqual(adapter.observe(), second["observation"])
        finally:
            adapter.close()

    def test_agent_reset_replay_and_multi_step_delta_are_deterministic(self):
        adapter = WNTRResidentialWaterAdapter()
        try:
            trace_a = [adapter.reset(0), adapter.step(1.0), adapter.step(0.0, steps=2)]
            trace_b = [adapter.reset(0), adapter.step(1.0), adapter.step(0.0, steps=2)]
            self.assertEqual(trace_a, trace_b)
            self.assertEqual(trace_a[-1]["steps"], 2)
            self.assertEqual(trace_a[-1]["time_seconds"], 10800)
            with self.assertRaises(ValueError):
                adapter.step(0.0, delta_t=3601)
        finally:
            adapter.close()

    def test_agent_reports_termination_and_rejects_post_terminal_step(self):
        adapter = WNTRResidentialWaterAdapter()
        try:
            adapter.reset(0)
            result = None
            for _ in range(6):
                result = adapter.step(1.0)
            self.assertIsNotNone(result)
            self.assertTrue(result["terminated"])
            self.assertTrue(result["done"])
            with self.assertRaises(RuntimeError):
                adapter.step(0.0)
        finally:
            adapter.close()


if __name__ == "__main__":
    unittest.main()
