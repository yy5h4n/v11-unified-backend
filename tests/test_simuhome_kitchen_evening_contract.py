import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]
CONTRACT_PATH = ROOT / "contracts" / "simuhome_kitchen_evening_warmth_v1.json"


def load_contract():
    with CONTRACT_PATH.open() as f:
        return json.load(f)


class TestKitchenEveningContract(unittest.TestCase):
    def test_kitchen_contract_is_frozen_to_catalog_responsibility(self):
        c = load_contract()
        self.assertEqual(c["schema_version"], "responsibility-contract-v1")
        self.assertEqual(c["responsibility_id"], "rd_split_016151c7c038")
        self.assertEqual(c["natural_query"], "Keep the kitchen warm in the evening.")
        self.assertEqual(c["lifecycle"], "MAINTAIN")
        self.assertEqual(c["scope"], {
            "zone": "kitchen",
            "zone_must_be_explicit": True,
            "time_basis": "virtual_clock",
            "evening_window": {"start_local_hour": 17, "end_local_hour": 23, "end_inclusive": False},
        })


    def test_temperature_band_is_a_construction_parameter(self):
        c = load_contract()
        band = c["profile"]["temperature_band_c"]
        self.assertLess(band["lower"], c["profile"]["target_c"])
        self.assertLess(c["profile"]["target_c"], band["upper"])
        self.assertIn("construction_parameter", c["profile"]["provenance"])
        self.assertIn("not query semantics", c["profile"]["provenance"])
        self.assertIn("target_c", c["soft_metric"])


    def test_contract_limits_actions_and_excludes_unobserved_context(self):
        c = load_contract()
        actions = c["legal_action_schema"]
        self.assertEqual(actions["target_zone"], "kitchen")
        self.assertEqual(set(actions["allowed_families"]), {"thermostat_setpoint", "heat_pump_or_hvac_mode"})
        excluded = set(c["excluded_semantics"])
        self.assertTrue({"cooking_or_meal_activity", "shower_activity_or_completion", "wake_deadline_or_sleep_state"} <= excluded)
        self.assertTrue({"resident_presence_or_occupancy_lifecycle", "fireplace_state", "newborn_or_special_beneficiary_safety_band"} <= excluded)


    def test_replay_lineage_and_single_responsibility_requirements_are_present(self):
        c = load_contract()
        self.assertEqual(c["episode_identity"], "one_physical_window_has_one_primary_responsibility")
        self.assertTrue(c["opportunity_predicate"]["noop_must_be_replayed"])
        self.assertTrue(c["opportunity_predicate"]["fixed_witness_must_satisfy_all_hard_clauses"])
        self.assertIn("arbitrary_policy_replay_is_supported", c["replay_requirements"])
        self.assertIn("no_gold_actions_are_exposed_to_policy", c["replay_requirements"])
        self.assertEqual(c["release_status"], "provisional")


if __name__ == "__main__":
    unittest.main()
