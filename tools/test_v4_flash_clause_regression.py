"""Semantic regression for the pure offline Episode clause evaluator.

Covers the required operator/rejection matrix:
  * combined range + delta (all operators in one spec AND)
  * pre-action initial_observation / prefix_observation used, not trace[0]
  * signed deltas (max_delta is an upper bound, not an absolute value)
  * intermediate trajectory violation fails even when the endpoint passes
  * missing paths, unknown operators, NaN/bool values, bool/NaN operands
  * dotted-key ambiguity vs explicit path token arrays
  * terminal success vs legal target-failing baselines
  * exact string/list/bool equality semantics
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from v4_flash_pilot_evaluator import score_run, ALLOWED_OPERATORS


def make_run(observations, initial=None, prefix=None, times=None):
    """Build a builder-format run dict from trace observations.

    ``initial``/``prefix`` default to None (key omitted): the evaluator then
    falls back to trace[0]/the initial anchor, matching legacy run format.
    """
    frames = []
    for i, obs in enumerate(observations):
        frame = {"observation": obs}
        if times is not None and i < len(times):
            frame["time_seconds"] = times[i]
        frames.append(frame)
    run = {"ok": True, "trace": frames}
    if initial is not None:
        run["initial_observation"] = initial
    if prefix is not None:
        run["prefix_observation"] = prefix
    return run


def target_ops(report):
    return report["target"]["frames"][0]["leaves"][0]["operators"]


class ClauseSchemaTests(unittest.TestCase):
    def test_unknown_operator_rejected(self):
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "foo": 1}, "constraints": []})

    def test_unknown_operator_rejected_inside_all_of(self):
        with self.assertRaises(ValueError):
            score_run(
                make_run([{"x": 1}]),
                {"target": {"all_of": [{"path": "x", "min": 0, "mystery": 2}]}, "constraints": []},
            )

    def test_missing_path_key_rejected(self):
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"min": 0}, "constraints": []})

    def test_path_without_operator_rejected(self):
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x"}, "constraints": []})

    def test_bool_numeric_operand_rejected(self):
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "min": True}, "constraints": []})
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "max_delta_from_initial": False}, "constraints": []})

    def test_nonfinite_numeric_operand_rejected(self):
        bad = float("nan")
        for op, operand in (("min", bad), ("max", float("inf")), ("min_delta_from_initial", bad)):
            with self.assertRaises(ValueError):
                score_run(make_run([{"x": 1}]), {"target": {"path": "x", op: operand}, "constraints": []})
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "equals": bad}, "constraints": []})

    def test_malformed_range_rejected(self):
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "range": [1, 2, 3]}, "constraints": []})
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "range": [3, 1]}, "constraints": []})
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "range": "lo"}, "constraints": []})

    def test_empty_one_of_and_bad_type_rejected(self):
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "one_of": []}, "constraints": []})
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "type": "matrix"}, "constraints": []})

    def test_finite_false_operand_rejected(self):
        with self.assertRaises(ValueError):
            score_run(make_run([{"x": 1}]), {"target": {"path": "x", "finite": False}, "constraints": []})

    def test_metadata_keys_are_ignored(self):
        clause = {
            "steps": 3,
            "seed": 17,
            "schema_revision": "public_obs_v2",
            "legal_counterexample": "n/a",
            "target": {"path": "x", "min_delta_from_initial": 0.1},
            "constraints": [{"path": "co2", "finite": True}],
        }
        report = score_run(make_run([{"x": 0, "co2": 1}, {"x": 0.5, "co2": 2}], initial={"x": 0, "co2": 0}), clause)
        self.assertTrue(report["pass"])


class CombinedRangeAndDeltaTests(unittest.TestCase):
    def test_conjunctive_range_and_delta(self):
        clause = {
            "target": {"path": "soc", "min_delta_from_initial": 0.1, "range": [0, 1]},
            "constraints": [{"path": "co2", "max": 800}],
        }
        ok = make_run(
            [{"soc": 0.5, "co2": 700}, {"soc": 0.6, "co2": 710}],
            initial={"soc": 0.5, "co2": 700},
        )
        self.assertTrue(score_run(ok, clause)["pass"])
        # range holds, delta too short -> fail
        short = make_run(
            [{"soc": 0.5, "co2": 700}, {"soc": 0.55, "co2": 700}],
            initial={"soc": 0.5, "co2": 700},
        )
        report = score_run(short, clause)
        self.assertFalse(report["pass"])
        self.assertFalse(report["target"]["pass"])
        # delta holds but range exceeded -> all operators AND -> fail
        overflow = make_run(
            [{"soc": 0.5, "co2": 700}, {"soc": 1.1, "co2": 700}],
            initial={"soc": 0.5, "co2": 700},
        )
        report = score_run(overflow, clause)
        self.assertFalse(report["pass"])
        self.assertFalse(report["target"]["pass"])


class AnchorSemanticsTests(unittest.TestCase):
    def test_preaction_initial_used_not_first_trace_frame(self):
        clause = {
            "target": {"path": "soc", "min_delta_from_initial": 0.1},
            "constraints": [],
        }
        # pre-action state is 0.40; the first pilot action already moves SOC to
        # 0.50. Terminal 0.55 is +0.15 from the pre-action state (pass) but only
        # +0.05 from trace[0] (would wrongly fail).
        run = make_run(
            [{"soc": 0.50}, {"soc": 0.55}],
            initial={"soc": 0.40},
        )
        report = score_run(run, clause)
        self.assertTrue(report["pass"])
        ops = target_ops(report)
        delta_op = next(o for o in ops if o["operator"] == "min_delta_from_initial")
        self.assertAlmostEqual(delta_op["actual"], 0.15)
        self.assertEqual(delta_op["anchor"]["value"], 0.40)
        # delta_from_initial must be a true increase from the pre-action state
        run2 = make_run(
            [{"soc": 0.50}, {"soc": 0.46}],
            initial={"soc": 0.40},
        )
        self.assertFalse(score_run(run2, clause)["pass"])

    def test_prefix_observation_used_not_first_trace_frame(self):
        clause = {
            "target": {"path": "soc", "min_delta_from_prefix": 0.1},
            "constraints": [],
        }
        # pre-pilot prefix state is 0.44; trace[0] is already 0.50. Terminal
        # 0.56 is +0.12 from the prefix (pass) but only +0.06 from trace[0].
        run = make_run(
            [{"soc": 0.50}, {"soc": 0.56}],
            initial={"soc": 0.40},
            prefix={"soc": 0.44},
        )
        report = score_run(run, clause)
        self.assertTrue(report["pass"])
        ops = target_ops(report)
        delta_op = next(o for o in ops if o["operator"] == "min_delta_from_prefix")
        self.assertEqual(delta_op["anchor"]["role"], "prefix")
        self.assertEqual(delta_op["anchor"]["value"], 0.44)

    def test_missing_initial_anchor_fails_closed(self):
        # Delta clauses may not silently reinterpret trace[0] as pre-action.
        clause = {"target": {"path": "soc", "min_delta_from_initial": 0.1}, "constraints": []}
        run = make_run([{"soc": 0.5}, {"soc": 0.6}])
        report = score_run(run, clause)
        self.assertFalse(report["pass"])
        self.assertIn("missing_initial_anchor", report["reason"])

    def test_missing_prefix_anchor_fails_closed(self):
        clause = {"target": {"path": "soc", "min_delta_from_prefix": 0.1}, "constraints": []}
        report = score_run(make_run([{"soc": 0.5}, {"soc": 0.6}], initial={"soc": 0.4}), clause)
        self.assertFalse(report["pass"])
        self.assertIn("missing_prefix_anchor", report["reason"])

    def test_constraint_scope_is_explicit_and_reported(self):
        clause = {
            "constraint_scope": {"include_initial": True, "include_prefix": True, "include_trace": False},
            "target": {"path": "x", "min": 1},
            "constraints": [{"path": "x", "min": 0.25}],
        }
        report = score_run(
            make_run([{"x": 1}], initial={"x": 0}, prefix={"x": 0.5}), clause
        )
        self.assertFalse(report["pass"])
        self.assertEqual([f["scope_domain"] for f in report["constraints"][0]["frames"]], ["initial", "prefix"])

    def test_constraint_scope_rejects_non_bool_and_empty(self):
        clause = {"target": {"path": "x", "equals": 1}, "constraints": [], "constraint_scope": {"include_trace": 1}}
        with self.assertRaises(ValueError): score_run(make_run([{"x": 1}]), clause)
        clause["constraint_scope"] = {"include_trace": False}
        with self.assertRaises(ValueError): score_run(make_run([{"x": 1}]), clause)

    def test_reports_times_and_actual_values(self):
        clause = {"target": {"path": "soc", "min": 0.5}, "constraints": []}
        run = make_run(
            [{"soc": 0.4}, {"soc": 0.6}],
            times=[60.0, 120.0],
        )
        report = score_run(run, clause)
        frame = report["target"]["frames"][0]
        self.assertEqual(frame["time_seconds"], 120.0)
        leaf = frame["leaves"][0]
        self.assertEqual(leaf["value"], 0.6)
        op = leaf["operators"][0]
        self.assertEqual(op["operator"], "min")
        self.assertEqual(op["actual"], 0.6)
        self.assertEqual(op["threshold"], 0.5)


class SignedDeltaTests(unittest.TestCase):
    def test_max_delta_is_signed_upper_bound(self):
        clause = {"target": {"path": "x", "max_delta_from_initial": 0.1}, "constraints": []}
        initial = {"x": 0}
        self.assertTrue(score_run(make_run([{"x": 0}, {"x": 0.1}], initial=initial), clause)["pass"])
        # a large signed decrease is below the upper bound: allowed
        self.assertTrue(score_run(make_run([{"x": 0}, {"x": -0.5}], initial=initial), clause)["pass"])
        # any increase above the upper bound fails
        self.assertFalse(score_run(make_run([{"x": 0}, {"x": 0.2}], initial=initial), clause)["pass"])
        self.assertFalse(score_run(make_run([{"x": -1.0}, {"x": -0.7}], initial={"x": -1.0}), clause)["pass"])

    def test_min_delta_is_signed_lower_bound(self):
        clause = {"target": {"path": "x", "min_delta_from_initial": -0.5}, "constraints": []}
        initial = {"x": 1.0}
        self.assertTrue(score_run(make_run([{"x": 1.0}, {"x": 0.6}], initial=initial), clause)["pass"])
        self.assertFalse(score_run(make_run([{"x": 1.0}, {"x": 0.3}], initial=initial), clause)["pass"])

    def test_signed_corridor_combined(self):
        clause = {
            "target": {"path": "x", "min_delta_from_initial": -0.1, "max_delta_from_initial": 0.2},
            "constraints": [],
        }
        initial = {"x": 1.0}
        self.assertTrue(score_run(make_run([{"x": 1.0}, {"x": 1.05}], initial=initial), clause)["pass"])
        self.assertTrue(score_run(make_run([{"x": 1.0}, {"x": 0.95}], initial=initial), clause)["pass"])
        self.assertFalse(score_run(make_run([{"x": 1.0}, {"x": 1.25}], initial=initial), clause)["pass"])
        self.assertFalse(score_run(make_run([{"x": 1.0}, {"x": 0.85}], initial=initial), clause)["pass"])


class TrajectoryConstraintTests(unittest.TestCase):
    def test_intermediate_violation_fails(self):
        clause = {
            "target": {"path": "soc", "min_delta_from_initial": 0.1, "range": [0, 1]},
            "constraints": [{"path": "co2", "max": 800}],
        }
        run = make_run(
            [
                {"soc": 0.5, "co2": 700},
                {"soc": 0.6, "co2": 900},
                {"soc": 0.6, "co2": 710},
            ],
            initial={"soc": 0.5, "co2": 700},
        )
        report = score_run(run, clause)
        self.assertFalse(report["pass"])
        self.assertTrue(report["target"]["pass"])
        constraint = report["constraints"][0]
        self.assertFalse(constraint["pass"])
        self.assertEqual(constraint["violating_frames"], [1])

    def test_target_is_terminal_only(self):
        # delta target reached only at the final frame; early frames may be
        # below the delta because only the terminal state is scored for target.
        clause = {"target": {"path": "soc", "min_delta_from_initial": 0.1}, "constraints": []}
        run = make_run(
            [{"soc": 0.5}, {"soc": 0.5}, {"soc": 0.6}],
            initial={"soc": 0.5},
        )
        self.assertTrue(score_run(run, clause)["pass"])


class ExactSemanticsTests(unittest.TestCase):
    def test_bool_never_equals_int(self):
        clause = {"target": {"path": "x", "equals": 1}, "constraints": []}
        self.assertFalse(score_run(make_run([{"x": True}]), clause)["pass"])
        clause = {"target": {"path": "x", "equals": True}, "constraints": []}
        self.assertTrue(score_run(make_run([{"x": True}]), clause)["pass"])
        clause = {"target": {"path": "x", "one_of": [1, 2]}, "constraints": []}
        self.assertFalse(score_run(make_run([{"x": True}]), clause)["pass"])

    def test_exact_string_and_list_semantics(self):
        clause = {"target": {"path": "s", "equals": "off"}, "constraints": []}
        self.assertFalse(score_run(make_run([{"s": "of"}]), clause)["pass"])
        self.assertFalse(score_run(make_run([{"s": "off "}]), clause)["pass"])
        self.assertTrue(score_run(make_run([{"s": "off"}]), clause)["pass"])
        clause = {"target": {"path": "v", "one_of": ["washing", "running"]}, "constraints": []}
        self.assertTrue(score_run(make_run([{"v": "running"}]), clause)["pass"])
        self.assertFalse(score_run(make_run([{"v": "started"}]), clause)["pass"])
        clause = {"target": {"path": "v", "equals": [1, 2]}, "constraints": []}
        self.assertTrue(score_run(make_run([{"v": [1, 2]}]), clause)["pass"])
        self.assertFalse(score_run(make_run([{"v": [2, 1]}]), clause)["pass"])
        self.assertFalse(score_run(make_run([{"v": [1, 2, 3]}]), clause)["pass"])

    def test_type_and_all_of(self):
        clause = {
            "target": {
                "all_of": [
                    {"path": "active_rule_ids", "type": "list"},
                    {"path": "washer.state", "one_of": ["washing", "running"]},
                ]
            },
            "constraints": [],
        }
        self.assertTrue(
            score_run(make_run([{"active_rule_ids": [1, 2], "washer.state": "washing"}]), clause)["pass"]
        )
        self.assertFalse(
            score_run(make_run([{"active_rule_ids": "none", "washer.state": "washing"}]), clause)["pass"]
        )
        clause = {"target": {"path": "flag", "type": "bool"}, "constraints": []}
        self.assertTrue(score_run(make_run([{"flag": False}]), clause)["pass"])
        self.assertFalse(score_run(make_run([{"flag": 0}]), clause)["pass"])


class MissingAndAmbiguousPathTests(unittest.TestCase):
    def test_empty_and_bool_path_tokens_rejected(self):
        for path in ("", [], [True], ["devices", False]):
            with self.assertRaises(ValueError):
                score_run(make_run([{"x": 1}]), {"target": {"path": path, "equals": 1}, "constraints": []})

    def test_missing_path_fails_closed(self):
        clause = {"target": {"path": "devices.nonexistent", "equals": "off"}, "constraints": []}
        report = score_run(make_run([{"devices": {}}]), clause)
        self.assertFalse(report["pass"])
        self.assertIn("missing_path", report["reason"])
        leaf = report["target"]["frames"][0]["leaves"][0]
        self.assertFalse(leaf["resolved"])
        self.assertEqual(leaf["reason"], "missing_path")

    def test_ambiguous_dotted_path_fails_closed(self):
        clause = {"target": {"path": "outer.a.b.c", "equals": 10}, "constraints": []}
        obs = {"outer": {"a.b.c": 10, "a.b": 20}}
        report = score_run(make_run([obs]), clause)
        self.assertFalse(report["pass"])
        self.assertIn("ambiguous_path", report["reason"])

    def test_explicit_token_arrays_disambiguate_dotted_keys(self):
        obs = {"devices.interior_lights": "on", "devices": {"interior_lights": "off"}}
        dotted = {"target": {"path": "devices.interior_lights", "equals": "off"}, "constraints": []}
        self.assertTrue(score_run(make_run([obs]), dotted)["pass"])
        token_flat = {"target": {"path": ["devices.interior_lights"], "equals": "on"}, "constraints": []}
        self.assertTrue(score_run(make_run([obs]), token_flat)["pass"])
        token_nested = {"target": {"path": ["devices", "interior_lights"], "equals": "off"}, "constraints": []}
        self.assertTrue(score_run(make_run([obs]), token_nested)["pass"])
        # list indices inside explicit token arrays and dotted paths
        nested = {"ports": [{"soc": 0.9}, {"soc": 0.2}]}
        arr = {"target": {"path": ["ports", 1, "soc"], "equals": 0.2}, "constraints": []}
        self.assertTrue(score_run(make_run([nested]), arr)["pass"])
        dotted_idx = {"target": {"path": "ports.0.soc", "equals": 0.9}, "constraints": []}
        self.assertTrue(score_run(make_run([nested]), dotted_idx)["pass"])


class FailureModeTests(unittest.TestCase):
    def test_nan_observation_fails_closed(self):
        clause = {"target": {"path": "x", "max_delta_from_initial": 0.1}, "constraints": []}
        report = score_run(make_run([{"x": 0}, {"x": float("nan")}], initial={"x": 0}), clause)
        self.assertFalse(report["pass"])

    def test_inf_observation_fails_closed(self):
        clause = {"target": {"path": "x", "min": 0}, "constraints": []}
        report = score_run(make_run([{"x": float("inf")}]), clause)
        self.assertFalse(report["pass"])

    def test_bool_observation_fails_closed_for_numeric_operator(self):
        clause = {"target": {"path": "x", "min": 0}, "constraints": []}
        report = score_run(make_run([{"x": False}, {"x": True}], initial={"x": False}), clause)
        self.assertFalse(report["pass"])

    def test_equals_matches_bool_observation(self):
        clause = {"target": {"path": "x", "equals": False}, "constraints": []}
        self.assertTrue(score_run(make_run([{"x": True}, {"x": False}], initial={"x": True}), clause)["pass"])
        self.assertFalse(score_run(make_run([{"x": False}, {"x": True}], initial={"x": False}), clause)["pass"])

    def test_no_trace(self):
        clause = {"target": {"path": "x", "equals": 1}, "constraints": []}
        report = score_run({"ok": False, "error": "native_failure"}, clause)
        self.assertFalse(report["pass"])
        self.assertEqual(report["reason"], "no_trace")
        self.assertEqual(report["score"], 0.0)


class TaskSuccessVsValidityTests(unittest.TestCase):
    def test_terminal_success(self):
        clause = {
            "target": {"path": "battery_soc", "min_delta_from_initial": 0.05, "range": [0, 1]},
            "constraints": [{"path": "net_electricity_kwh", "finite": True}],
        }
        run = make_run(
            [{"battery_soc": 0.4, "net_electricity_kwh": 1.0}, {"battery_soc": 0.47, "net_electricity_kwh": 1.1}],
            initial={"battery_soc": 0.4, "net_electricity_kwh": 1.0},
        )
        report = score_run(run, clause)
        self.assertTrue(report["pass"])
        self.assertEqual(report["score"], 1.0)

    def test_legal_target_failure_is_not_success(self):
        clause = {
            "target": {"path": "battery_soc", "min_delta_from_initial": 0.05, "range": [0, 1]},
            "constraints": [{"path": "net_electricity_kwh", "finite": True}],
        }
        # A legal baseline: every observation finite, all validity constraints
        # hold, but SOC never rises +0.05. Validity must not count as success.
        run = make_run(
            [{"battery_soc": 0.4, "net_electricity_kwh": 0.0}, {"battery_soc": 0.41, "net_electricity_kwh": 0.0}],
            initial={"battery_soc": 0.4, "net_electricity_kwh": 0.0},
        )
        report = score_run(run, clause)
        self.assertFalse(report["pass"])
        self.assertFalse(report["target"]["pass"])
        self.assertTrue(report["constraints"][0]["pass"])
        self.assertIn("target", report["reason"])

    def test_target_pass_but_validity_violation_fails(self):
        clause = {
            "target": {"path": "battery_soc", "min_delta_from_initial": 0.05},
            "constraints": [{"path": "net_electricity_kwh", "finite": True}],
        }
        # Target is reached but an intermediate observation is non-finite.
        run = make_run(
            [{"battery_soc": 0.45, "net_electricity_kwh": float("nan")}, {"battery_soc": 0.6, "net_electricity_kwh": 1.0}],
            initial={"battery_soc": 0.4, "net_electricity_kwh": 1.0},
        )
        report = score_run(run, clause)
        self.assertFalse(report["pass"])
        self.assertTrue(report["target"]["pass"])
        self.assertFalse(report["constraints"][0]["pass"])


class OperatorCoverageTests(unittest.TestCase):
    def test_all_supported_operators_enumerated(self):
        self.assertEqual(
            ALLOWED_OPERATORS,
            {
                "equals",
                "one_of",
                "range",
                "min",
                "max",
                "finite",
                "type",
                "min_delta_from_initial",
                "max_delta_from_initial",
                "min_delta_from_prefix",
            },
        )

    def test_checks_shape_compatible(self):
        clause = {
            "target": {"path": "x", "min_delta_from_initial": 0.1},
            "constraints": [{"path": "y", "finite": True}],
        }
        run = make_run([{"x": 0, "y": 1}, {"x": 0.2, "y": 2}], initial={"x": 0, "y": 1})
        report = score_run(run, clause)
        self.assertEqual(report["checks"], [("target", True), ("constraint_0", True)])
        self.assertEqual(report["score"], 1.0)
        self.assertEqual(report["schema"], "v4.flash.pilot.evaluator.v1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
