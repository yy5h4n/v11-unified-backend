from __future__ import annotations

import json
from pathlib import Path
import unittest

from replay_energyplus_responsibility_episode import evaluate_episode

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "generated/energyplus_responsibility_release_v1/episodes_public.jsonl"


class EpisodePolicyBridgeTests(unittest.TestCase):
    def test_observation_conditioned_policy_runs_and_scores(self):
        episode_id = json.loads(PUBLIC.read_text(encoding="utf-8").splitlines()[0])["episode_id"]

        def policy(observation, step):
            self.assertIn("zone_temperature_c", observation)
            return 21.0 if observation["zone_temperature_c"] >= 22.0 else 22.0

        result = evaluate_episode(episode_id, policy, "test_dynamic_policy")
        self.assertTrue(result["trajectory_complete"])
        self.assertEqual(result["actions_acknowledged"], 96)
        self.assertEqual(result["terminal_verdict"], "continues_beyond_window")
        self.assertEqual(len(result["trajectory_digest"]), 64)


if __name__ == "__main__":
    unittest.main()
