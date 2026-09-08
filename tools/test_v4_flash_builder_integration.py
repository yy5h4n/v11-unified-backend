"""Integration checks that the contract builder is wired to the strict pilot evaluator."""
import importlib.util
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v4_flash_pilot_evaluator as evaluator


class BuilderIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("pilot_builder", ROOT / "tools/build_v4_flash_offline_contracts.py")
        cls.builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.builder)

    def test_builder_uses_strict_evaluator_and_has_no_duplicate(self):
        source = (ROOT / "tools/build_v4_flash_offline_contracts.py").read_text()
        self.assertNotIn("def score_run(", source)
        self.assertIs(self.builder.score_run, evaluator.score_run)

    def test_builder_score_runs_all_operators_and_initial_scope(self):
        clause = {
            "constraint_scope": {"include_initial": True, "include_prefix": True, "include_trace": True},
            "target": {"all_of": [
                {"path": "x", "min": 0.5, "range": [0, 1], "min_delta_from_initial": 0.1},
                {"path": ["payload", "flag"], "equals": True},
            ]},
            "constraints": [{"path": "y", "max": 10.0}],
        }
        run = {"ok": True, "initial_observation": {"x": 0.4, "y": 1, "payload": {"flag": True}},
               "prefix_observation": {"x": 0.45, "y": 2, "payload": {"flag": True}},
               "trace": [{"time_seconds": 1, "observation": {"x": 0.5, "y": 3, "payload": {"flag": True}}},
                         {"time_seconds": 2, "observation": {"x": 0.55, "y": 11, "payload": {"flag": True}}}]}
        report = self.builder.score_run(run, clause)
        self.assertFalse(report["pass"])
        self.assertEqual(report["constraints"][0]["violating_frames"], [1])
        self.assertEqual(report["constraint_scope"]["include_initial"], True)

    def test_missing_anchor_and_intermediate_missing_field_fail_closed(self):
        clause = {"target": {"path": "x", "min_delta_from_initial": 0.1}, "constraints": [{"path": "y", "finite": True}]}
        missing_anchor = {"ok": True, "trace": [{"time_seconds": 1, "observation": {"x": 1, "y": 1}}]}
        self.assertFalse(self.builder.score_run(missing_anchor, clause)["pass"])
        missing_field = {"ok": True, "initial_observation": {"x": 0},
                         "trace": [{"time_seconds": 1, "observation": {"x": 1, "y": 1}},
                                   {"time_seconds": 2, "observation": {"x": 1}}]}
        self.assertFalse(self.builder.score_run(missing_field, clause)["pass"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
