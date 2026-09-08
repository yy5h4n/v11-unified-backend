from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence
import math
from numbers import Real

from .types import ClauseKind, ResponsibilityContract, TrajectoryClause


@dataclass(frozen=True)
class StateTrajectory:
    variables: Mapping[str, tuple[float, ...]]

    def __init__(self, variables: Mapping[str, Sequence[float]]):
        if not isinstance(variables, Mapping):
            raise TypeError("trajectory variables must be a mapping")
        normalized = {}
        for name, vals in variables.items():
            try:
                normalized[name] = tuple(_finite_number(v, f"trajectory {name!r}") for v in vals)
            except TypeError as exc:
                raise ValueError(f"trajectory variable {name!r} must be a numeric sequence") from exc
        object.__setattr__(
            self, "variables", normalized
        )

    @property
    def horizon(self) -> int:
        if not self.variables:
            return 0
        lengths = {len(v) for v in self.variables.values()}
        if len(lengths) != 1:
            raise ValueError(f"ragged trajectory: variable lengths {sorted(lengths)}")
        return lengths.pop()

    def series(self, variable: str) -> tuple[float, ...]:
        if variable not in self.variables:
            raise KeyError(f"trajectory has no variable {variable!r}")
        return self.variables[variable]


@dataclass(frozen=True)
class ClauseResult:
    clause_id: str
    kind: ClauseKind
    satisfied: bool
    violations: int
    severity: float
    cost: float


@dataclass(frozen=True)
class EvaluationResult:
    clause_results: tuple[ClauseResult, ...]
    hard_feasible: bool
    hard_violation_count: int
    hard_deficit: float
    total_soft_cost: float

    @property
    def lexicographic_key(self) -> tuple[int, float, float]:
        return (self.hard_violation_count, self.hard_deficit, self.total_soft_cost)


def _per_step_violation(op: str, params: Mapping[str, float], value: float) -> float:
    if op == "between":
        lo = float(params["lo"])
        hi = float(params["hi"])
        if value < lo:
            return lo - value
        if value > hi:
            return value - hi
        return 0.0
    if op == ">=":
        return max(0.0, float(params["value"]) - value)
    if op == "<=":
        return max(0.0, value - float(params["value"]))
    if op == "==":
        return abs(value - float(params["value"]))
    raise ValueError(f"unsupported op {op!r}")


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _validate_clause(clause: TrajectoryClause) -> None:
    _finite_number(clause.weight, f"clause {clause.clause_id!r} weight")
    if not isinstance(clause.params, Mapping):
        raise TypeError(f"clause {clause.clause_id!r} params must be a mapping")
    for key, value in clause.params.items():
        _finite_number(value, f"clause {clause.clause_id!r} operand {key!r}")


def evaluate_clause(clause: TrajectoryClause, trajectory: StateTrajectory) -> ClauseResult:
    _validate_clause(clause)
    trajectory.horizon  # validate all variables, including ones unused by clause
    series = trajectory.series(clause.variable)
    for value in series:
        _finite_number(value, f"trajectory {clause.variable!r}")
    if not series:
        if clause.kind in (ClauseKind.HARD_INVARIANT, ClauseKind.TERMINAL_GOAL):
            return ClauseResult(clause.clause_id, clause.kind, satisfied=False, violations=1, severity=1.0, cost=clause.weight)
        return ClauseResult(
            clause.clause_id, clause.kind, satisfied=False,
            violations=0, severity=0.0, cost=0.0,
        )

    if clause.kind is ClauseKind.HARD_INVARIANT:
        violations = [ _per_step_violation(clause.op, clause.params, v) for v in series ]
        n_viol = sum(1 for x in violations if x > 0.0)
        severity = sum(violations)
        return ClauseResult(
            clause.clause_id, clause.kind,
            satisfied=n_viol == 0, violations=n_viol,
            severity=severity,
            cost=severity * clause.weight,
        )

    if clause.kind is ClauseKind.TERMINAL_GOAL:
        viol = _per_step_violation(clause.op, clause.params, series[-1])
        return ClauseResult(
            clause.clause_id, clause.kind,
            satisfied=viol == 0.0, violations=1 if viol > 0.0 else 0,
            severity=viol,
            cost=viol * clause.weight,
        )

    if clause.kind is ClauseKind.CUMULATIVE_SOFT_COST:
        total = sum(_per_step_violation(clause.op, clause.params, v) for v in series)
        return ClauseResult(
            clause.clause_id, clause.kind,
            satisfied=total == 0.0, violations=0,
            severity=total,
            cost=total * clause.weight,
        )

    raise ValueError(f"unsupported clause kind {clause.kind!r}")


def evaluate_trajectory(
    contract: ResponsibilityContract,
    trajectory: StateTrajectory,
) -> EvaluationResult:
    # Validate at the scoring boundary too: the frozen dataclass does not make
    # the nested mapping immutable, so callers can mutate it after creation.
    for name, series in trajectory.variables.items():
        for value in series:
            _finite_number(value, f"trajectory {name!r}")
    trajectory.horizon  # force equal-length validation before any clause score
    results = tuple(evaluate_clause(c, trajectory) for c in contract.clauses)
    hard_results = [
        r for r in results
        if r.kind in (ClauseKind.HARD_INVARIANT, ClauseKind.TERMINAL_GOAL)
    ]
    hard_feasible = all(r.satisfied for r in hard_results)
    hard_violation_count = sum(r.violations for r in hard_results)
    hard_deficit = sum(r.severity for r in hard_results)
    total_soft = sum(r.cost for r in results if r.kind is ClauseKind.CUMULATIVE_SOFT_COST)
    return EvaluationResult(
        clause_results=results,
        hard_feasible=hard_feasible,
        hard_violation_count=hard_violation_count,
        hard_deficit=hard_deficit,
        total_soft_cost=total_soft,
    )


def lexicographic_better(a: EvaluationResult, b: EvaluationResult) -> bool:
    return a.lexicographic_key < b.lexicographic_key
