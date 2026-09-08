import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "generated/energyplus_adapter_readiness_v1.json"


class TestEnergyPlusAdapterReadiness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(OUT.read_text(encoding="utf-8"))

    def test_no_gate_bypass(self):
        self.assertEqual(self.data["responsibility_candidate_count"], 25)
        self.assertEqual(self.data["episode_count"], 0)
        for row in self.data["responsibilities"]:
            self.assertFalse(row["episode_eligible"])
            self.assertEqual(row["episode_count"], 0)
            if "responsibility_gates" in row:
                self.assertFalse(any(row["responsibility_gates"].values()))

    def test_known_fail_closed_cases(self):
        by_id = {row["responsibility_id"]: row for row in self.data["responsibilities"]}
        self.assertEqual(by_id["rd_f0bc2c668699"]["readiness_status"], "BLOCKED_MODEL_VARIANT_REQUIRED")
        self.assertEqual(by_id["rd_split_39d32cc7fd22"]["readiness_status"], "BLOCKED_CONTRACT_UNDERSPECIFIED")

    def test_source_lineage_is_pinned(self):
        sources = self.data["source_artifacts"]
        self.assertEqual(len(sources["direct_adapter_packet_sha256"]), 64)
        self.assertEqual(len(sources["physical_process_pool_sha256"]), 64)
        self.assertEqual(len(sources["thermal_action_gate_sha256"]), 64)
        self.assertEqual(len(sources["humidity_action_gate_sha256"]), 64)
        self.assertEqual(len(sources["lighting_action_gate_sha256"]), 64)

    def test_thermal_gate_is_not_reused_for_occupancy_energy(self):
        temperature = [row for row in self.data["responsibilities"] if row["responsibility_family"] == "temperature"]
        occupancy = [row for row in self.data["responsibilities"] if row["responsibility_family"] == "occupancy_energy"]
        self.assertEqual(len(temperature), 9)
        self.assertTrue(all(row["readiness_status"] == "CAPABILITY_PROBED_CONTRACT_BINDING_PENDING" for row in temperature))
        self.assertEqual(len(occupancy), 1)
        self.assertFalse(occupancy[0]["evidence_gates"]["family_action_capability_probe"])

    def test_blind_actions_are_not_inferred_from_lighting_control(self):
        by_id={row["responsibility_id"]:row for row in self.data["responsibilities"]}
        for responsibility_id in ("rd_b62dd368cce0","rd_bc53f8b79068"):
            self.assertIsNone(by_id[responsibility_id]["action_capability_process_id"])
            self.assertFalse(by_id[responsibility_id]["evidence_gates"]["family_action_capability_probe"])

    def test_family_evidence_never_disposes_responsibility_gates(self):
        for row in self.data["responsibilities"]:
            if row["readiness_status"] == "CAPABILITY_PROBED_CONTRACT_BINDING_PENDING":
                self.assertTrue(row["evidence_gates"]["family_action_capability_probe"])
                self.assertIn("SAME_PROCESS_LINEAGE_NOT_ESTABLISHED",row["blockers"])

    def test_capability_evidence_is_from_passing_gate(self):
        for row in self.data["responsibilities"]:
            if row.get("action_capability_gate_ref"):
                gate=json.loads((ROOT/row["action_capability_gate_ref"]).read_text())
                self.assertTrue(gate["passed"])


if __name__ == "__main__":
    unittest.main()
