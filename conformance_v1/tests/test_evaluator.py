import unittest

from conformance_v1.config import CONFIG
from conformance_v1.evaluator import TemporalEvaluator, _cmp, _tri_and, _tri_or, _tri_not, utility


def _contract(clauses, anti_gaming="terminal_guard_band"):
    return {
        "id": "CT-T", "anti_gaming_rule": anti_gaming,
        "priority_order": [c["clause_id"] for c in clauses],
        "clauses": clauses,
    }


def _within(cid, trigger, obligation, deadline):
    return {"clause_id": cid, "kind": "conditional_obligation",
            "formula": {"op": "within", "trigger": trigger, "obligation": obligation,
                        "deadline_intervals": deadline}, "coverage": []}


def _goal(cid, goal, deadline):
    return {"clause_id": cid, "kind": "terminal_goal",
            "formula": {"op": "terminal_goal", "goal": goal, "deadline_intervals": deadline},
            "coverage": []}


class TestEvaluator(unittest.TestCase):
    def setUp(self):
        self.ev = TemporalEvaluator(CONFIG)

    def test_e2e_episode_verdict(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        contract = dict(fx["contract_draft"], id="CT-001")
        for i, pid in enumerate(["PP-001", "PP-002", "PP-003"]):
            proc = {"id": pid, "trace": fx["process_drafts"][i]["trace"]}
            verdict = self.ev.evaluate(contract, proc, policy_id="pi_hat_g1", seed=i + 1)
            self.assertEqual(verdict["terminal_verdict"], "continues_beyond_window")
            self.assertEqual(verdict["soft_cost_total"], 2.0)
            self.assertEqual(verdict["obligations"]["CL-2"]["satisfied"], True)
            self.assertEqual(verdict["terminal_goals"]["CL-3"]["status"], "achieved")
            self.assertEqual(verdict["invariant_violations"], [])

    def test_noop_is_violated_at_truncation(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        contract = dict(fx["contract_draft"], id="CT-001")
        proc = {"id": "PP-NOOP", "trace": fx["noop_trace"]}
        verdict = self.ev.evaluate(contract, proc, policy_id="pi_0", seed=0)
        self.assertEqual(verdict["terminal_verdict"], "violated_at_truncation")

    def test_release_fires(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        contract = dict(fx["contract_draft"], id="CT-001")
        base = fx["process_drafts"][0]["trace"]
        # manual_override leaves temp unchanged; "none" adds +0.2
        actions = ["manual_override"] + ["none"] * 11
        lr = [16.0]
        for a in actions:
            d = 0.0 if a == "manual_override" else 0.2
            lr.append(round(lr[-1] + d, 6))
        schedule = [4, 4, 3, 3, 2, 2, 2, 1, 1, 0, 0, 0, 0]
        obs = [{"living_room_temp": lr[t], "outside_temp": schedule[t], "season": "winter"} for t in range(13)]
        trace = {"horizon": 12, "obs": obs, "actions_used": actions, "transition": base["transition"]}
        proc = {"id": "PP-REL", "trace": trace}
        verdict = self.ev.evaluate(contract, proc, policy_id="pi_release", seed=0)
        self.assertEqual(verdict["terminal_verdict"], "released")
        self.assertTrue(verdict["released"])

    def test_missing_observation_pauses_deadline(self):
        clauses = [_within("C1", {"op": "const", "value": True},
                            {"op": "obs", "variable": "living_room_temp", "cmp": "ge", "value": 18.0}, 1)]
        contract = _contract(clauses)
        trace = {
            "horizon": 1,
            "obs": [{"living_room_temp": 16.0, "outside_temp": 4, "season": "winter"},
                    {"living_room_temp": None, "outside_temp": 4, "season": "winter"}],
            "actions_used": ["none"],
            "transition": {"none": {"living_room_temp": {"set": None}},
                           "exogenous": {"outside_temp": {"schedule": [4, 4]}}},
        }
        verdict = self.ev.evaluate(contract, {"id": "PP-M", "trace": trace}, policy_id="p", seed=0)
        self.assertEqual(verdict["terminal_verdict"], "censored_pending")
        self.assertEqual(verdict["obligations"]["C1"]["violated"], False)

    def test_deadline_violation(self):
        clauses = [_within("C1", {"op": "const", "value": True},
                            {"op": "obs", "variable": "living_room_temp", "cmp": "ge", "value": 18.0}, 1)]
        contract = _contract(clauses)
        trace = {
            "horizon": 2,
            "obs": [{"living_room_temp": 16.0, "outside_temp": 4, "season": "winter"},
                    {"living_room_temp": 16.2, "outside_temp": 4, "season": "winter"},
                    {"living_room_temp": 16.4, "outside_temp": 4, "season": "winter"}],
            "actions_used": ["none", "none"],
            "transition": {"none": {"living_room_temp": {"delta": 0.2}},
                           "exogenous": {"outside_temp": {"schedule": [4, 4, 4]}}},
        }
        verdict = self.ev.evaluate(contract, {"id": "PP-D", "trace": trace}, policy_id="p", seed=0)
        self.assertEqual(verdict["terminal_verdict"], "violated_at_truncation")
        self.assertTrue(verdict["obligations"]["C1"]["violated"])

    def test_terminal_viability_rule(self):
        clauses = [_within("C1", {"op": "const", "value": True},
                            {"op": "obs", "variable": "living_room_temp", "cmp": "ge", "value": 18.0}, 5)]
        contract = _contract(clauses, anti_gaming="terminal_viability")
        trace = {
            "horizon": 2,
            "obs": [{"living_room_temp": 16.0, "outside_temp": 4, "season": "winter"},
                    {"living_room_temp": 16.2, "outside_temp": 4, "season": "winter"},
                    {"living_room_temp": 16.4, "outside_temp": 4, "season": "winter"}],
            "actions_used": ["none", "none"],
            "transition": {"none": {"living_room_temp": {"delta": 0.2}},
                           "exogenous": {"outside_temp": {"schedule": [4, 4, 4]}}},
        }
        verdict = self.ev.evaluate(contract, {"id": "PP-V", "trace": trace}, policy_id="p", seed=0)
        self.assertEqual(verdict["terminal_verdict"], "censored_pending")

    def test_policy_replay_mismatch(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        contract = dict(fx["contract_draft"], id="CT-001")
        proc = {"id": "PP-001", "trace": fx["process_drafts"][0]["trace"]}
        with self.assertRaises(Exception) as ctx:
            self.ev.evaluate(contract, proc, policy=lambda obs, t: "none", policy_id="bad", seed=0)
        self.assertIn("REPLAY_MISMATCH", str(ctx.exception))

    def test_utility(self):
        self.assertEqual(utility({"terminal_verdict": "continues_beyond_window", "invariant_violations": [],
                                  "obligations": {}}), 1.0)
        self.assertEqual(utility({"terminal_verdict": "violated_at_truncation", "invariant_violations": [],
                                  "obligations": {}}), 0.0)

    def test_tristate_helpers(self):
        self.assertEqual(_cmp(None, "lt", 10), None)
        self.assertEqual(_cmp(5, "lt", 10), True)
        self.assertEqual(_tri_and([True, None]), None)
        self.assertEqual(_tri_and([True, False]), False)
        self.assertEqual(_tri_or([False, None]), None)
        self.assertEqual(_tri_or([True, None]), True)
        self.assertEqual(_tri_not(None), None)

    def test_validate_dsl_rejects_invalid(self):
        from conformance_v1.config import ConformanceError
        with self.assertRaises(ConformanceError) as ctx:
            self.ev.validate_dsl({"op": "wibble"})
        self.assertEqual(ctx.exception.code, "SCHEMA_VIOLATION")


if __name__ == "__main__":
    unittest.main()
