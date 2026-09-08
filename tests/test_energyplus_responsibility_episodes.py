from __future__ import annotations

import json
import subprocess
import sys
import unittest

from compile_energyplus_responsibility_episodes import RELEASE, build


def rows(name):
    return [json.loads(line) for line in build()[RELEASE / name].splitlines() if line.strip()]


class EnergyPlusReleaseTests(unittest.TestCase):
    def test_counts_unique_windows_and_primary_assignment(self):
        public, private = rows("episodes_public.jsonl"), rows("episodes_private.jsonl")
        self.assertEqual(len(public), 30)
        self.assertEqual(len(private), 30)
        self.assertEqual(sum(row["responsibility_id"] == "rd_37104b57370a" for row in private), 30)
        self.assertEqual({row["responsibility_id"] for row in private}, {"rd_37104b57370a"})
        self.assertEqual(len({row["physical_process_id"] for row in private}), 30)
        self.assertEqual(len({tuple(row["source_window"]["exact_key"].values()) for row in private}), 30)

    def test_public_private_separation_and_no_gold_actions(self):
        public, private = rows("episodes_public.jsonl"), rows("episodes_private.jsonl")
        self.assertTrue(all("source_window" not in row and "replay_certificates" not in row for row in public))
        self.assertTrue(all("witness" not in json.dumps(row).lower() for row in public))
        self.assertTrue(all(row["gold_actions"] == [] for row in private))
        self.assertTrue(all(row["split"] == "none" for row in public))

    def test_contract_status_and_replay_gates(self):
        public, private = rows("episodes_public.jsonl"), rows("episodes_private.jsonl")
        gate = json.loads(build()[RELEASE / "replay_gate.json"])
        self.assertEqual(gate["passed_episode_count"], 30)
        self.assertTrue(gate["all_released_episodes_passed"])
        self.assertEqual(gate["excluded_windows"][0]["failed_gates"], ["fixed_witness_feasible"])
        self.assertTrue(all(row["statuses"]["preview_only"] for row in public))
        self.assertTrue(all(row["statuses"]["authorization_status"] == "unknown" for row in public))
        self.assertTrue(all(row["statuses"]["release_status"] == "provisional" for row in public))
        self.assertTrue(all(row["termination"]["terminal_verdict"] == "continues_beyond_window" for row in public))
        self.assertTrue(all(row["qa_verdicts"]["passed"] for row in private))

    def test_compiler_check_is_idempotent(self):
        script = RELEASE.parent.parent / "compile_energyplus_responsibility_episodes.py"
        subprocess.run([sys.executable, str(script)], check=True)
        subprocess.run([sys.executable, str(script), "--check"], check=True)
        before = {path.name: path.read_bytes() for path in RELEASE.iterdir() if path.is_file()}
        subprocess.run([sys.executable, str(script), "--check"], check=True)
        after = {path.name: path.read_bytes() for path in RELEASE.iterdir() if path.is_file()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
