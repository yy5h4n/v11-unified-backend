"""Recompute the claims made by V2 native diagnostic artifacts.

The verifier dispatches only on versioned schemas. Unknown or legacy evidence can
still be retained for development history, but it cannot certify a frozen
validation admission.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .native_evidence import canonical, recheck


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _unique_runs(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    runs = document.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("native evidence needs nonempty runs")
    result = {}
    for run in runs:
        _object(run, "run")
        name = run.get("policy")
        if not isinstance(name, str) or not name or name in result:
            raise ValueError("native evidence policy names must be unique nonempty text")
        result[name] = run
    return result


def _expected_clock(samples: list[dict[str, Any]], cadence: float, horizon: float) -> None:
    if horizon % cadence:
        raise ValueError("diagnostic horizon must align with cadence")
    expected = [index * cadence for index in range(int(horizon / cadence) + 1)]
    actual = [sample.get("time_seconds") for sample in samples]
    if actual != expected:
        raise ValueError("native diagnostic has missing, duplicate, or mis-timed samples")


def _actions_match_receipts(actions: list[Any], samples: list[dict[str, Any]]) -> None:
    if len(actions) != len(samples) - 1:
        raise ValueError("native diagnostic action/sample count mismatch")
    for action, receipt in zip(actions, samples[1:]):
        if canonical(action) != canonical(receipt.get("action")):
            raise ValueError("declared diagnostic action differs from native receipt")


def _thermal(document: dict[str, Any]) -> dict[str, Any]:
    public = _object(document.get("public_contract"), "public_contract")
    route_id = public.get("route_id")
    if document.get("route_id") != route_id:
        raise ValueError("thermal diagnostic route does not match its contract")
    cadence = public.get("cadence_seconds")
    horizon = public.get("horizon_seconds")
    if isinstance(cadence, bool) or not isinstance(cadence, (int, float)) or cadence <= 0:
        raise ValueError("invalid thermal cadence")
    if isinstance(horizon, bool) or not isinstance(horizon, (int, float)) or horizon <= 0:
        raise ValueError("invalid thermal horizon")

    runs = _unique_runs(document)
    required = {"idle", "one_shot", "fixed_0_3", "feedback"}
    if set(runs) != required:
        raise ValueError("thermal core contrast requires exactly four declared policies")
    outcomes = {}
    initial = None
    for name, run in runs.items():
        samples = run.get("samples")
        if not isinstance(samples, list) or not samples:
            raise ValueError("thermal diagnostic samples required")
        _expected_clock(samples, float(cadence), float(horizon))
        _actions_match_receipts(run.get("actions", []), samples)
        observation = samples[0].get("observation")
        if initial is None:
            initial = observation
        elif canonical(observation) != canonical(initial):
            raise ValueError("thermal policies do not share one initial observation")
        score = recheck(public, samples)
        if canonical(score) != canonical(run.get("evaluation")):
            raise ValueError("thermal saved score differs from current-code evaluation")
        outcomes[name] = score.get("task_success")
    contrast = (
        outcomes["idle"] is False
        and outcomes["one_shot"] is False
        and outcomes["fixed_0_3"] is False
        and outcomes["feedback"] is True
    )
    if document.get("core_contrast_passed") is not contrast:
        raise ValueError("thermal summary flag differs from recomputed policy contrast")
    return {
        "item_id": document.get("item_id"),
        "route_id": route_id,
        "core_contrast_passed": contrast,
        "policy_outcomes": outcomes,
    }


def _garage(document: dict[str, Any]) -> dict[str, Any]:
    conditions = _object(document.get("public_conditions"), "public_conditions")
    cadence = conditions.get("native_cadence_seconds")
    horizon = conditions.get("horizon_seconds")
    check_time = conditions.get("daily_check_time_seconds")
    deadline = conditions.get("close_response_deadline_seconds")
    values = (cadence, horizon, check_time, deadline)
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in values):
        raise ValueError("garage diagnostic times must be numeric")
    if cadence <= 0 or horizon <= 0 or not 0 <= check_time <= deadline <= horizon:
        raise ValueError("invalid garage diagnostic time ordering")
    if check_time % cadence or deadline % cadence:
        raise ValueError("garage diagnostic times must align with native cadence")
    runs = _unique_runs(document)
    if set(runs) != {"idle", "scheduled_close"}:
        raise ValueError("garage calibration requires idle and scheduled_close")

    outcomes = {}
    initial = None
    for name, run in runs.items():
        samples = run.get("samples")
        if not isinstance(samples, list) or not samples:
            raise ValueError("garage diagnostic samples required")
        _expected_clock(samples, float(cadence), float(horizon))
        _actions_match_receipts(run.get("actions", []), samples)
        observation = samples[0].get("observation")
        if initial is None:
            initial = observation
        elif canonical(observation) != canonical(initial):
            raise ValueError("garage policies do not share one initial observation")
        check_state = samples[int(check_time / cadence)]["observation"]["devices"]["garage_door.main"]["state"]
        deadline_state = samples[int(deadline / cadence)]["observation"]["devices"]["garage_door.main"]["state"]
        score = {
            "evaluated": True,
            "task_success": deadline_state == "closed",
            "check_time_seconds": check_time,
            "response_deadline_seconds": deadline,
            "state_at_check": check_state,
            "state_at_deadline": deadline_state,
            "scope": "one disclosed daily-check opportunity; later reopening is outside this calibration query",
        }
        if canonical(score) != canonical(run.get("evaluation")):
            raise ValueError("garage saved score differs from trajectory")
        outcomes[name] = score["task_success"]
    contrast = outcomes == {"idle": False, "scheduled_close": True}
    if document.get("diagnostic_passed") is not contrast:
        raise ValueError("garage summary flag differs from recomputed policy contrast")
    return {
        "item_id": document.get("item_id"),
        "route_id": document.get("route_id"),
        "calibration_contrast_passed": contrast,
        "policy_outcomes": outcomes,
    }


def verify_execution_file(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    schema = document.get("schema")
    if schema == "evidence-query-thermal-diagnostic.v1":
        return _thermal(document)
    if schema == "evidence-query-native-diagnostic.v1":
        return _garage(document)
    raise ValueError(f"no current-code verifier for execution evidence schema: {schema!r}")
