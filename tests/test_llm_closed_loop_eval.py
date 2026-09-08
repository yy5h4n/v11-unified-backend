import os, json, unittest, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import evaluate_llm_closed_loop as ev

class FakeClient:
    def __init__(self, content): self.content=content
    def complete(self, messages): return {"content":self.content,"usage":{"total_tokens":7},"latency_ms":1}

def energy_ep():
    return {"episode_id":"e","responsibility_id":"r","natural_query":"set heat","profile":{},"initial_observation":{},"legal_action_schema":{"type":"discrete_thermostat_schedule_value_c","values":[21.0,22.0]}}

class TestClosedLoop(unittest.TestCase):
    def test_energyplus_rejects_index_and_fallback(self):
        ep=energy_ep(); self.assertEqual(ev.validate_action({"value":22.0},ep),22.0)
        with self.assertRaises(ValueError): ev.validate_action({"value":1},ep)
        self.assertEqual(ev._fallback(ep),22.0)

    def test_multiroom_requires_every_room_and_constraints(self):
        ep={"initial_observation":{"a":{"device_type":"air_conditioner"},"b":{"device_type":"heat_pump"}},"room_ids":["a","b"],"legal_action_schema":{"type":"multiroom_thermal_command","modes":["auto","heat","cool","off"],"target_c_range":[7,32],"device_constraints":{"air_conditioner":["heat","cool","off"],"heat_pump":["heat","off"]}}}
        with self.assertRaises(ValueError): ev.validate_action({"actions":{"a":{"mode":"off","target_c":22}}},ep)
        with self.assertRaises(ValueError): ev.validate_action({"actions":{"a":{"mode":"cool","target_c":22},"b":{"mode":"cool","target_c":22}}},ep)
        self.assertEqual(set(ev.validate_action({"actions":{"a":{"mode":"off","target_c":22},"b":{"mode":"off","target_c":22}}},ep)),{"a","b"})

    def test_single_room_action_is_not_treated_as_room_mapping(self):
        ep={"initial_observation":{"room_id":"kitchen","device_type":"air_conditioner","temperature_c":22.5},"legal_action_schema":{"type":"room_thermal_command","modes":["auto","heat","cool","off"],"target_c_range":[7,32],"device_constraints":{"air_conditioner":["heat","cool","off"]}}}
        self.assertEqual(ev.validate_action({"mode":"cool","target_c":22},ep),{"mode":"cool","target_c":22.0})
        self.assertEqual(ev._fallback(ep),{"mode":"off","target_c":22.0})

    def test_json_and_aggregation(self):
        self.assertEqual(ev._json_content('```json\n{"value": 22}\n```'),{"value":22})
        rows=[{"responsibility_id":"r","backend":"b","success":True,"valid_actions":2,"api_successes":2,"calls":2,"tokens":4,"latency_ms":3,"mean_soft_metric":1,"errors":{}}, {"responsibility_id":"r","backend":"b","success":False,"valid_actions":0,"api_successes":1,"calls":2,"tokens":5,"latency_ms":4,"mean_soft_metric":None,"errors":{"invalid_json":1}}]
        a=ev.aggregate(rows)["total"]; self.assertEqual(a["episodes"],2); self.assertEqual(a["tokens"],9); self.assertEqual(a["errors"],{"invalid_json":1})

    def test_key_never_in_report(self):
        old=os.environ.pop("AIGC_API_KEY",None)
        try:
            os.environ["AIGC_API_KEY"]="secret"
            c=ev.ChatClient(api_key="secret")
            report={"config":{"base_url":c.base_url,"model":c.model}}
            self.assertNotIn("secret",json.dumps(report))
        finally:
            os.environ.pop("AIGC_API_KEY",None)
            if old is not None: os.environ["AIGC_API_KEY"]=old

    def test_call_count_mismatch_invalidates_episode(self):
        ep=energy_ep();ep["horizon_steps"]=2
        private={"backend_binding":{"backend":"fake"}}
        def runner(_ep,_private,policy):
            policy({});policy({});policy({})
            return {"trajectory_complete":True,"hard_contract_satisfied":True,"mean_abs_error_c":0.0}
        row=ev.evaluate_episode(ep,private,FakeClient('{"value":22.0}'),backend_runner=runner)
        self.assertFalse(row["success"]);self.assertFalse(row["calls_match_horizon"])
        self.assertEqual(row["errors"],{"policy_call_count_mismatch":1})

if __name__ == '__main__': unittest.main()
