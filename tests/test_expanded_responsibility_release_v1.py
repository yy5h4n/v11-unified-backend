import json
import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from build_expanded_responsibility_release_v1 import main


class TestExpandedResponsibilityRelease(unittest.TestCase):
    def test_combined_release_preserves_component_identity_and_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            report = main(out)
            public = [json.loads(line) for line in (out / "episodes_public.jsonl").read_text().splitlines()]
            private = [json.loads(line) for line in (out / "episodes_private.jsonl").read_text().splitlines()]
            self.assertTrue(report["passed"])
            self.assertEqual(report["responsibility_count"], 3)
            self.assertEqual(report["episode_count"], 40)
            self.assertEqual(report["responsibility_episode_counts"], {
                "rd_37104b57370a": 30,
                "rd_split_016151c7c038": 3,
                "rd_split_b8457e559b4d": 7,
            })
            self.assertEqual(len(public), len(private))
            self.assertEqual(len({row["episode_id"] for row in public}), 40)
            self.assertEqual(len({row["physical_process_id"] for row in private}), 40)
            self.assertFalse(any("gold_actions" in row for row in public))
            self.assertTrue(all(row["qa_verdicts"]["passed"] for row in private))
            self.assertFalse(report["pooled_cross_stratum_metric_allowed"])
            self.assertEqual(
                report["backend_fidelity_strata"]["simuhome_synthetic_room_state_aggregator"]["episode_count"],
                10,
            )
            self.assertIn("internal_preview_only", report["redistribution_status"])


if __name__ == "__main__":
    unittest.main()
