import hashlib,json,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/"generated/energyplus_adapter_process_pool_v1.json"
class TestAdapterProcessPool(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.data=json.loads(OUT.read_text())
 def test_counts_and_gates(self):
  self.assertEqual(self.data["action_capability_probe_count"],3);self.assertEqual(self.data["tentative_family_match_count"],20);self.assertEqual(self.data["episode_count"],0)
  for process in self.data["processes"]:
   for gate in ("runtime_action_adapter","action_available","action_sensitivity","cross_run_determinism"):self.assertTrue(process["gates"][gate])
   self.assertFalse(process["gates"]["responsibility_evaluator"]);self.assertFalse(process["episode_eligible"])
   self.assertFalse(process["canonical_observation_action_process_joined"]);self.assertFalse(process["responsibility_contract_bound"])
 def test_trace_lineage(self):
  for process in self.data["processes"]:
   for ref in process["causal_trace_refs"].values():
    path=ROOT/ref["path"];self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),ref["sha256"])
    first=json.loads(path.read_text().splitlines()[0]);self.assertTrue({"environment_num","year","month","day","hour","zone_timestep_number","zone_timesteps_per_hour"}<=set(first))
 def test_blind_responsibilities_excluded(self):
  ids={binding["responsibility_id"] for process in self.data["processes"] for binding in process["tentative_family_matches"]}
  self.assertNotIn("rd_b62dd368cce0",ids);self.assertNotIn("rd_bc53f8b79068",ids)
if __name__=="__main__":unittest.main()
