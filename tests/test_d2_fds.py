import hashlib
import json
import unittest
from pathlib import Path

from d2_fds_adapter import (
    FDSRuntimeUnavailable,
    FDSSmokePropagationAdapter,
    _model_text,
    discover_fds_runtime,
    validate_action,
)

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "generated/d2_fds_v1/gate_report.json"


class TestD2FDSBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads(REPORT.read_text(encoding="utf-8"))

    def test_current_machine_has_real_runtime_evidence(self):
        self.assertTrue(self.data["passed"])
        self.assertEqual(self.data["status"], "REAL_RUNTIME_PROBED")
        self.assertFalse(self.data["surrogate_fallback"])
        self.assertEqual(self.data["exclusion_reasons"], [])
        self.assertEqual(self.data["replay_arm_count"], 4)
        self.assertEqual(set(self.data["causal_trace_refs"]), {"door_closed", "door_open"})

    def test_runtime_probe_discovers_project_local_runtime(self):
        probe = discover_fds_runtime()
        self.assertTrue(probe["available"])
        self.assertEqual(probe["blocker"], None)
        self.assertEqual(probe["checked_candidates"][0]["binary_architecture"], "x86_64")
        self.assertEqual(probe["checked_candidates"][0]["execution_mode"], "Rosetta 2 translation")

    def test_action_contract_is_binary_and_finite(self):
        self.assertEqual(validate_action(0), 0.0)
        self.assertEqual(validate_action(1), 1.0)
        for bad in (-1, 0.5, 2, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                validate_action(bad)

    def test_missing_runtime_refuses_to_run(self):
        adapter = FDSSmokePropagationAdapter(
            run_root=ROOT / "generated/d2_fds_v1/test_runs",
            runtime=ROOT / "generated/d2_fds_v1/does-not-exist/fds",
        )
        with self.assertRaises(FDSRuntimeUnavailable):
            adapter.reset(seed=0)

    def test_deck_is_real_fds_input_and_changes_only_door_geometry(self):
        closed = _model_text(0.0)
        opened = _model_text(1.0)
        self.assertIn("&HEAD", closed)
        self.assertIn("&REAC FUEL='PROPANE'", closed)
        self.assertIn("QUANTITY='VISIBILITY'", closed)
        self.assertIn("SURF_ID='INERT'", closed)
        self.assertIn("door panel absent", opened)
        self.assertNotEqual(hashlib.sha256(closed.encode()).hexdigest(), hashlib.sha256(opened.encode()).hexdigest())
        self.assertNotIn("diffusion model", (closed + opened).lower())
        template = self.data["model_template"]
        template_path = ROOT / template["path"]
        self.assertTrue(template_path.is_file())
        self.assertEqual(hashlib.sha256(template_path.read_bytes()).hexdigest(), template["sha256"])

    def test_report_is_backend_only(self):
        serialized = json.dumps(self.data, sort_keys=True).lower()
        for forbidden in ("episode", "evaluator", "responsibility", "contract", "threshold"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
