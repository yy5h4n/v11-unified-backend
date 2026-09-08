from __future__ import annotations

import json
from pathlib import Path
import unittest

from probe_energyplus_residential_runtime import validate_action

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "generated/energyplus_residential_runtime_v1"


class ResidentialRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads((RUNTIME / "gate_report.json").read_text())
        cls.replays = [json.loads(line) for line in (RUNTIME / "replays.jsonl").read_text().splitlines()]

    def test_real_residential_model_and_matrix(self):
        self.assertEqual(self.report["source_model"], "SingleFamilyHouse_TwoSpeed_MultiStageElectricSuppCoil.idf")
        self.assertEqual(self.report["people"], "LIVING ZONE People")
        self.assertEqual(self.report["occupancy_schedule"], "HOUSE OCCUPANCY")
        self.assertEqual(len(self.replays), 31 * 3)
        self.assertTrue(self.report["passed"])
        self.assertEqual(self.report["passed_window_count"], 30)
        self.assertEqual(self.report["certification_replay_count"], 31 * 3)
        self.assertTrue(self.report["dynamic_policy_gate"]["nonconstant_actions"])
        self.assertTrue(self.report["dynamic_policy_gate"]["exact_determinism"])

    def test_prefix_alignment_and_pre_action_observation(self):
        by_day = {}
        for row in self.replays:
            by_day.setdefault(row["day"], {})[row["strategy"]] = row
            self.assertEqual(len(row["steps"]), 96)
            self.assertIsNotNone(row["pre_action_initial_observation"])
            self.assertIn("identical across strategies", row["prefix_policy"])
        for group in by_day.values():
            self.assertEqual(set(group), {"witness", "contrast", "noop"})
            for i in range(96):
                self.assertEqual(group["witness"]["steps"][i]["time_key"], group["contrast"]["steps"][i]["time_key"])
                self.assertEqual(group["witness"]["steps"][i]["time_key"], group["noop"]["steps"][i]["time_key"])
            self.assertEqual(group["witness"]["pre_action_initial_observation"], group["contrast"]["pre_action_initial_observation"])
            self.assertEqual(group["witness"]["pre_action_initial_observation"], group["noop"]["pre_action_initial_observation"])

    def test_selected_determinism_and_fail_closed_window(self):
        self.assertTrue(all(row["selected_determinism"] and row["time_continuity"] and row["initial_observation_alignment"] for row in self.report["gate_report"]))
        failed = [row for row in self.report["gate_report"] if not row["passed"]]
        self.assertEqual([(row["day"], row["fixed_witness_feasible"]) for row in failed], [(4, False)])

    def test_policy_action_validation_and_pre_post_protocol(self):
        self.assertEqual(validate_action(21), 21.0)
        self.assertEqual(validate_action(22), 22.0)
        with self.assertRaises(ValueError):
            validate_action(20)
        first = self.replays[0]["steps"][0]
        self.assertIn("effective_heating_setpoint_c", first["observation"])
        self.assertIn("effective_heating_setpoint_c", first["effect"])
        self.assertNotEqual(first["observation"]["zone_temperature_c"], first["effect"]["zone_temperature_c"])


if __name__ == "__main__":
    unittest.main()
