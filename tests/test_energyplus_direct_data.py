import hashlib, json, unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "generated/energyplus_direct_process_pool_v2.json"
REQUIRED = {
    "thermal_occupancy_energy": {"zone_temperature", "zone_setpoint", "occupancy", "hvac", "total_energy", "weather"},
    "humidity_iaq_ventilation": {"humidity", "zone_temperature", "fan", "control_state"},
    "lighting_blinds": {"illuminance", "lighting_power", "shade_state", "solar"},
}

class TestEnergyPlusDirectV2(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.data = json.loads(OUT.read_text(encoding="utf-8"))
    def test_fail_closed_gate_contract(self):
        self.assertEqual(self.data["episode_count"], 0)
        for process in self.data["processes"]:
            self.assertTrue(process["observability_gate"])
            for gate in ("action_available_gate", "action_sensitivity_gate", "determinism_gate", "evaluator_gate"):
                self.assertFalse(process[gate])
            self.assertEqual(process["episode_count"], 0)
    def test_coherent_timestep_fields(self):
        for process in self.data["processes"]:
            self.assertTrue(REQUIRED[process["family"]] <= set(process["field_roles"]))
            self.assertTrue(all("(timestep)" in value.lower() for value in process["field_roles"].values()))
        thermal = next(row for row in self.data["processes"] if row["family"] == "thermal_occupancy_energy")
        for role in ("zone_temperature", "zone_setpoint", "occupancy", "hvac"):
            self.assertTrue(thermal["field_roles"][role].upper().startswith("ZONE 1:"))
    def test_contiguous_window_slices(self):
        for process in self.data["processes"]:
            for window in process["windows"]:
                self.assertEqual(window["duration_seconds"], 86400)
                self.assertEqual(window["end_row_exclusive"] - window["start_row"], window["row_count"])
                start = datetime.fromisoformat(window["coverage_start_timestamp"].removesuffix("Z"))
                end = datetime.fromisoformat(window["coverage_end_timestamp"].removesuffix("Z"))
                self.assertEqual(int((end - start).total_seconds()), 86400)
                self.assertEqual(len(window["slice_sha256"]), 64)
    def test_trace_hash(self):
        for process in self.data["processes"]:
            trace = ROOT / process["trace"]["path"]
            self.assertEqual(hashlib.sha256(trace.read_bytes()).hexdigest(), process["trace"]["sha256"])
            self.assertFalse(process["trace"]["immutable"])
            self.assertEqual(process["time_basis"],"EnergyPlus local simulation time; timezone unspecified")

if __name__ == "__main__": unittest.main()
