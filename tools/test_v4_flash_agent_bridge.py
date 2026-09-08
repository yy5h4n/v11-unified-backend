"""Offline transport checks for the native scalar/list/dict action bridge."""
import unittest

from run_v4_flash_all_backend_agent_pilot import parse_answer, run_model_episode


class AgentBridgeTests(unittest.TestCase):
    def test_scalar_list_dict_round_trip(self):
        for value in (1.0, [0.1, -0.2], {"battery_rate": 0.5, "hvac_rate": 0.0}):
            wire = '<answer>{"action": ' + __import__("json").dumps(value) + '}</answer>'
            self.assertEqual(parse_answer(wire), value)

    def test_rejects_unwrapped_native_value(self):
        with self.assertRaises(ValueError):
            parse_answer('<answer>1.0</answer>')

    def test_rejects_extra_or_missing_envelope_fields(self):
        with self.assertRaises(ValueError):
            parse_answer('<answer>{"value": 1}</answer>')
        with self.assertRaises(ValueError):
            parse_answer('<answer>{"action": 1, "debug": true}</answer>')

    def test_three_turn_feedback_and_task_scoring(self):
        class FakeRoute:
            def reset(self, seed): self.x = 0; return {"observation": {"x": 0}}
            def legal_actions(self): return 0.0
            def step(self, action):
                if not isinstance(action, (int, float)) or isinstance(action, bool): raise ValueError("scalar_required")
                self.x += action
                return {"observation": {"x": self.x}, "time_seconds": self.x + 1, "delta_t_seconds": 1, "done": False}

        contract = {"query": "Reach x at least 2.", "seed": 7, "pilot_horizon_steps": 3,
                    "task_clauses": {"target": {"path": "x", "min": 2}, "constraints": []}}

        class FakeClient:
            def __init__(self, actions): self.actions = actions; self.requests = []
            def complete(self, messages):
                self.requests.append(messages)
                i = len(self.requests) - 1
                return {"content": '<answer>{"action": '+__import__("json").dumps(self.actions[i])+'}</answer>', "usage": {}}

        passing = FakeClient([1, 1, 0])
        result = run_model_episode("fake", FakeRoute(), passing, contract, {})
        self.assertTrue(result["score"]["pass"])
        self.assertEqual(len(passing.requests), 3)
        self.assertIn("observation_delta", passing.requests[1][-1]["content"])
        self.assertNotIn("witness", passing.requests[0][0]["content"])
        self.assertNotIn("private", passing.requests[0][0]["content"])

        failing = FakeClient([0, 0, 0])
        self.assertFalse(run_model_episode("fake", FakeRoute(), failing, contract, {})["score"]["pass"])

        rejected = FakeClient([1, "bad", 1])
        rejected_result = run_model_episode("fake", FakeRoute(), rejected, contract, {})
        self.assertEqual(rejected_result["termination"], "agent_action_rejected")
        self.assertEqual(rejected_result["actions"][1]["status"], "rejected")


if __name__ == "__main__":
    unittest.main(verbosity=2)
