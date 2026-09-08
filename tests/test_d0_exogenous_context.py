from __future__ import annotations

import json
from pathlib import Path

import pytest

from probe_d0_exogenous_context import build_evidence
from unified_compiler.adapters.d0_exogenous_context import (
    DEFAULT_HORIZON_STEPS,
    DEFAULT_SCHEDULE,
    D0ActionError,
    D0ContextError,
    D0ContextTrajectory,
    D0ExogenousContextAdapter,
    ExternalContextEvent,
    ExogenousContextSchedule,
    PROVIDES,
)
from unified_compiler.types import ProcessRequirement


def _action(target: str = "interior_lights", operation: str = "on") -> dict:
    return {"kind": "act", "command": {"target": target, "operation": operation}}


def test_external_schedule_is_canonical_and_agent_independent() -> None:
    schedule = ExogenousContextSchedule(
        (ExternalContextEvent(4, "context_update", {"key": "weather", "value": "rain"}), ExternalContextEvent(2, "occupancy_change", {"status": "away", "count": 0}))
    )
    assert schedule.events[0].step == 2
    assert schedule.schedule_id == ExogenousContextSchedule(schedule.events).schedule_id
    trajectory = D0ContextTrajectory(schedule, horizon_steps=4)
    initial = trajectory.reset(seed=9)
    assert "schedule" not in initial
    trajectory.step(_action())
    assert trajectory.private_state()["schedule"]["schedule_id"] == schedule.schedule_id


def test_external_event_changes_observable_context_and_device_state() -> None:
    trajectory = D0ContextTrajectory(DEFAULT_SCHEDULE, DEFAULT_HORIZON_STEPS)
    trajectory.reset()
    trajectory.step(_action())
    result = trajectory.step(_action())
    observation = result["observation"]
    assert observation["context"]["occupancy_status"] == "away"
    assert any(event["source"] == "external" for event in observation["events"])
    result = trajectory.step(_action())
    result = trajectory.step(_action())
    assert result["observation"]["devices"]["front_door"] == "open"


def test_agent_action_changes_state_under_same_schedule() -> None:
    on = D0ContextTrajectory(DEFAULT_SCHEDULE, DEFAULT_HORIZON_STEPS)
    off = D0ContextTrajectory(DEFAULT_SCHEDULE, DEFAULT_HORIZON_STEPS)
    on.reset(seed=1)
    off.reset(seed=1)
    for _ in range(DEFAULT_HORIZON_STEPS):
        on.step(_action(operation="on"))
        off.step(_action(operation="off"))
    assert on.state_digest() != off.state_digest()


def test_reset_replay_is_exact() -> None:
    actions = [_action("front_door", "open"), _action("interior_lights", "on")] * 6
    first = D0ContextTrajectory(DEFAULT_SCHEDULE, DEFAULT_HORIZON_STEPS)
    second = D0ContextTrajectory(DEFAULT_SCHEDULE, DEFAULT_HORIZON_STEPS)
    first_trace = [first.reset(seed=42)]
    second_trace = [second.reset(seed=42)]
    for action in actions[:DEFAULT_HORIZON_STEPS]:
        first_trace.append(first.step(action))
        second_trace.append(second.step(action))
    assert first_trace == second_trace


def test_action_validation_is_fail_closed() -> None:
    trajectory = D0ContextTrajectory(DEFAULT_SCHEDULE, DEFAULT_HORIZON_STEPS)
    trajectory.reset()
    with pytest.raises(D0ActionError):
        trajectory.step({"kind": "wait"})
    with pytest.raises(D0ActionError):
        trajectory.step(_action("front_door", "on"))


def test_probe_gate_has_backend_only_boundary() -> None:
    report = build_evidence()
    assert report["verified"] is True
    assert report["evidence_boundary"] == {
        "backend_only": True,
        "responsibilities_used": False,
        "episodes_selected": False,
        "evaluator_used": False,
        "does_not_claim": ["physical_dynamics", "device_failure", "weather_or_grid_physics", "benchmark_readiness"],
    }


def test_adapter_gate_exposes_only_verified_process(tmp_path: Path) -> None:
    report = build_evidence()
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps(report), encoding="utf-8")
    adapter = D0ExogenousContextAdapter(replay_gate_path=gate)
    assert adapter.capabilities()[0].status.value == "EXECUTABLE_REPLAY_VERIFIED"
    requirement = ProcessRequirement(
        requirement_id="d0", responsibility_lifecycle="MAINTAIN", physical_topology="THERMAL_DYNAMICS", required_capabilities=PROVIDES, state_variables=(), action_types=()
    )
    processes = adapter.scan((requirement,))
    assert len(processes) == 1
    assert processes[0].manifest["backend_only"] is True
    assert processes[0].manifest["agent_cannot_modify_schedule"] is True
    assert adapter.open_trajectory().reset()["step"] == 0


def test_adapter_rejects_stale_gate(tmp_path: Path) -> None:
    report = build_evidence()
    report["adapter_sha256"] = "0" * 64
    gate = tmp_path / "gate.json"
    gate.write_text(json.dumps(report), encoding="utf-8")
    adapter = D0ExogenousContextAdapter(replay_gate_path=gate)
    with pytest.raises(D0ContextError, match="stale"):
        adapter.capabilities()
