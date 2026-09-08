import copy
import json
import tempfile
import unittest
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from compile_simuhome_kitchen_evening_episodes import DATA, RID, main
from unified_compiler.simuhome_room_thermal_adapter import (
    SimuHomeRoomThermalAdapter,
    digest_json,
)


class TestSimuHomeKitchenEveningEpisodes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tempdir.name)
        cls.report = main(cls.out)
        cls.public = [json.loads(line) for line in (cls.out / "episodes_public.jsonl").read_text().splitlines() if line]
        cls.private = [json.loads(line) for line in (cls.out / "episodes_private.jsonl").read_text().splitlines() if line]
        cls.gate = json.loads((cls.out / "replay_gate.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_nonempty_release_and_identity_invariants(self):
        self.assertTrue(self.report["passed"])
        self.assertGreater(len(self.public), 0)
        self.assertEqual(self.report["episode_count"], len(self.public))
        self.assertEqual(len(self.public), len(self.private))
        self.assertEqual({row["episode_id"] for row in self.public}, {row["episode_id"] for row in self.private})
        self.assertEqual(len({row["physical_process_id"] for row in self.private}), len(self.private))
        self.assertTrue(all(row["responsibility_id"] == RID for row in self.public))
        self.assertTrue(all(row["binding_role"] == "PRIMARY" for row in self.private))
        self.assertEqual(self.report["source_file_count"], 600)
        self.assertEqual(self.report["candidate_source_count"], 8)
        self.assertEqual(self.report["episode_count"], 3)
        self.assertEqual(self.report["failure_counts"]["CERTIFICATION_FAILED"], 1)
        self.assertEqual(self.report["failure_counts"]["DUPLICATE_RESPONSIBILITY_PROCESS"], 4)
        accounted = self.report["episode_count"] + sum(self.report["failure_counts"].values())
        self.assertEqual(accounted, 600)
        signatures = [row["source_window"]["responsibility_process_signature"] for row in self.private]
        self.assertEqual(len(signatures), len(set(signatures)))

    def test_public_release_has_no_gold_and_content_hashes_match(self):
        for row in self.public:
            self.assertNotIn("gold_actions", row)
            expected = row["content_hash"]
            body = copy.deepcopy(row)
            body.pop("content_hash")
            self.assertEqual(expected, digest_json(body))
            self.assertEqual(row["split"], "none")
            self.assertTrue(row["statuses"]["preview_only"])

    def test_every_released_episode_passes_every_gate(self):
        self.assertTrue(self.gate["all_released_episodes_passed"])
        self.assertEqual(self.gate["released_episode_count"], len(self.public))
        for row in self.private:
            self.assertTrue(row["qa_verdicts"]["passed"])
            self.assertTrue(all(row["qa_verdicts"].values()))
            cert = row["replay_certificates"]
            self.assertGreaterEqual(cert["soft_metric_improvement_vs_noop_c"], 0.1)
            self.assertEqual(cert["witness_metrics"]["hard_violation_count"], 0)
            self.assertFalse(row["gold_actions_released_publicly"])
            self.assertEqual(cert["witness_digest"], cert["witness_replicate_digest"])
            self.assertEqual(cert["noop_digest"], cert["noop_replicate_digest"])
            self.assertEqual(cert["contrast_digest"], cert["contrast_replicate_digest"])
            self.assertEqual(row["source_hashes"]["corpus_manifest_file_count"], 600)

    def test_first_episode_independently_replays_to_pinned_digest(self):
        row = self.private[0]
        source = json.loads((DATA / row["source_window"]["source_file"]).read_text())
        adapter = SimuHomeRoomThermalAdapter(
            source["initial_home_config"],
            device_id=row["backend_binding"]["device_id"],
        )
        target = row["contract"]["profile"]["target_c"]
        replay = adapter.replay(lambda _step, _obs: {"mode": "auto", "target_c": target})
        self.assertEqual(digest_json(replay), row["replay_certificates"]["witness_digest"])
        self.assertTrue(all(17 <= int(step["observation"]["virtual_time"][11:13]) < 23 for step in replay["trace"]))


if __name__ == "__main__":
    unittest.main()
