"""Focused regressions for the audit findings (run from the project root)."""

import math

import pytest

from unified_compiler import (
    BindingRole,
    ClauseKind,
    ResponsibilityContract,
    StateTrajectory,
    TrajectoryClause,
    evaluate_trajectory,
)
from unified_compiler.agent_interface import AgentActionError, AgentInterfaceError, HarnessD1AgentBackend
from harness_v2.core import EpisodeSpec
from harness_v2.workflow_backend import WorkflowBackend


def _contract(*clauses):
    return ResponsibilityContract("c", "p", "r", BindingRole.PRIMARY, tuple(clauses))


def test_evaluator_rejects_nonfinite_ragged_and_empty_hard_trajectories():
    clause = TrajectoryClause("x", ClauseKind.HARD_INVARIANT, "x", "<=", {"value": 1.0})
    with pytest.raises((ValueError, TypeError)):
        evaluate_trajectory(_contract(clause), StateTrajectory({"x": [math.nan]}))
    with pytest.raises(ValueError, match="ragged"):
        evaluate_trajectory(_contract(clause), StateTrajectory({"x": [0.0], "y": [0.0, 1.0]}))
    result = evaluate_trajectory(_contract(clause), StateTrajectory({"x": []}))
    assert not result.hard_feasible and result.hard_violation_count > 0


def test_evaluator_rejects_invalid_numeric_clause_fields():
    with pytest.raises((ValueError, TypeError)):
        evaluate_trajectory(_contract(TrajectoryClause("x", ClauseKind.HARD_INVARIANT, "x", "<=", {"value": math.inf})), StateTrajectory({"x": [0.0]}))
    with pytest.raises((ValueError, TypeError)):
        evaluate_trajectory(_contract(TrajectoryClause("x", ClauseKind.HARD_INVARIANT, "x", "<=", {"value": 1.0}, weight=math.nan)), StateTrajectory({"x": [0.0]}))


def test_d1_terminal_step_and_invalid_dt_do_not_mutate_workflow():
    backend = WorkflowBackend(private_horizon_seconds=60)
    spec = EpisodeSpec("audit", {"scenario_type": "generic_household_workflow"}, 3)
    facade = HarnessD1AgentBackend(backend, spec)
    facade.reset(seed=3)
    before = backend.state_digest()
    with pytest.raises(AgentActionError):
        facade.step({"kind": "act", "commands": []}, dt_seconds=math.nan)
    assert backend.state_digest() == before
    receipt = facade.step({"kind": "act", "commands": []})
    assert receipt["done"] is True
    assert "private_feedback" not in receipt["info"]
    after = backend.state_digest()
    with pytest.raises(AgentInterfaceError):
        facade.step({"kind": "act", "commands": []}, dt_seconds=math.nan)
    assert backend.state_digest() == after
    assert after != before
