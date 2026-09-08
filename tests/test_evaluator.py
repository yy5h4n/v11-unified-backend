import inspect

import pytest

from unified_compiler import (
    BindingRole,
    ClauseKind,
    ResponsibilityContract,
    StateTrajectory,
    TrajectoryClause,
    evaluate_trajectory,
    lexicographic_better,
)


def make_contract(clauses) -> ResponsibilityContract:
    return ResponsibilityContract(
        contract_id="c1",
        process_id="p1",
        responsibility="resp",
        role=BindingRole.PRIMARY,
        clauses=tuple(clauses),
    )


SOC_BOUNDS = TrajectoryClause(
    clause_id="soc_bounds",
    kind=ClauseKind.HARD_INVARIANT,
    variable="soc",
    op="between",
    params={"lo": 0.0, "hi": 1.0},
)

TERMINAL_SOC = TrajectoryClause(
    clause_id="terminal_soc",
    kind=ClauseKind.TERMINAL_GOAL,
    variable="soc",
    op=">=",
    params={"value": 0.8},
)

COMFORT = TrajectoryClause(
    clause_id="comfort",
    kind=ClauseKind.CUMULATIVE_SOFT_COST,
    variable="temp",
    op="between",
    params={"lo": 20.0, "hi": 26.0},
    weight=2.0,
)


def test_hard_invariant_violation_detected():
    contract = make_contract([SOC_BOUNDS])
    traj = StateTrajectory({"soc": [0.5, 1.2, 0.7]})
    result = evaluate_trajectory(contract, traj)
    assert not result.hard_feasible
    assert result.clause_results[0].violations == 1


def test_terminal_goal_pass_and_fail():
    contract = make_contract([TERMINAL_SOC])
    ok = evaluate_trajectory(contract, StateTrajectory({"soc": [0.1, 0.85]}))
    bad = evaluate_trajectory(contract, StateTrajectory({"soc": [0.1, 0.5]}))
    assert ok.hard_feasible
    assert not bad.hard_feasible


def test_cumulative_soft_cost_weighted():
    contract = make_contract([COMFORT])
    traj = StateTrajectory({"temp": [22.0, 28.0, 18.0]})
    result = evaluate_trajectory(contract, traj)
    assert result.hard_feasible
    assert result.total_soft_cost == pytest.approx((2.0 + 2.0) * 2.0)


def test_lexicographic_scoring_hard_beats_soft():
    contract = make_contract([SOC_BOUNDS, COMFORT])
    feasible_costly = evaluate_trajectory(
        contract, StateTrajectory({"soc": [0.5, 0.5], "temp": [30.0, 30.0]})
    )
    infeasible_cheap = evaluate_trajectory(
        contract, StateTrajectory({"soc": [0.5, 1.5], "temp": [22.0, 22.0]})
    )
    assert feasible_costly.total_soft_cost > infeasible_cheap.total_soft_cost
    assert lexicographic_better(feasible_costly, infeasible_cheap)
    assert feasible_costly.lexicographic_key == (0, 0.0, feasible_costly.total_soft_cost)
    assert infeasible_cheap.lexicographic_key == (
        1,
        infeasible_cheap.hard_deficit,
        infeasible_cheap.total_soft_cost,
    )


def test_hard_severity_ordered_before_soft_cost():
    soc_wide = TrajectoryClause(
        clause_id="soc_wide",
        kind=ClauseKind.HARD_INVARIANT,
        variable="soc",
        op="between",
        params={"lo": 0.0, "hi": 1.0},
    )
    contract = make_contract([soc_wide, COMFORT])
    one_small_violation = evaluate_trajectory(
        contract, StateTrajectory({"soc": [0.5, 1.1, 0.5], "temp": [40.0, 40.0, 40.0]})
    )
    two_small_violations = evaluate_trajectory(
        contract, StateTrajectory({"soc": [1.1, 1.1, 0.5], "temp": [22.0, 22.0, 22.0]})
    )
    one_big_violation = evaluate_trajectory(
        contract, StateTrajectory({"soc": [0.5, 2.0, 0.5], "temp": [22.0, 22.0, 22.0]})
    )

    assert one_small_violation.hard_violation_count == 1
    assert two_small_violations.hard_violation_count == 2
    assert one_big_violation.hard_violation_count == 1
    assert one_big_violation.hard_deficit > one_small_violation.hard_deficit
    assert one_small_violation.total_soft_cost > two_small_violations.total_soft_cost

    assert lexicographic_better(one_small_violation, two_small_violations)
    assert lexicographic_better(one_small_violation, one_big_violation)
    sorted_results = sorted(
        (two_small_violations, one_big_violation, one_small_violation),
        key=lambda r: r.lexicographic_key,
    )
    assert sorted_results[0] is one_small_violation
    assert sorted_results[1] is one_big_violation
    assert sorted_results[2] is two_small_violations


def test_missing_variable_raises():
    contract = make_contract([SOC_BOUNDS])
    with pytest.raises(KeyError):
        evaluate_trajectory(contract, StateTrajectory({"temp": [1.0]}))


def test_evaluator_is_gold_action_free():
    sig = inspect.signature(evaluate_trajectory)
    param_names = set(sig.parameters)
    assert param_names == {"contract", "trajectory"}
    forbidden = {"gold_actions", "actions", "action_trajectory", "policy", "agent"}
    assert param_names.isdisjoint(forbidden)
    state_fields = set(StateTrajectory.__dataclass_fields__)
    assert state_fields.isdisjoint(forbidden)
    clause_fields = set(TrajectoryClause.__dataclass_fields__)
    assert clause_fields.isdisjoint(forbidden)
