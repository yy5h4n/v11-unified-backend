"""Backend-only D1 tests for real Harness V2 discrete device state machines."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_v2.core import EpisodeSpec
from unified_compiler.adapters.d1_discrete_device_fault import (
    D1DiscreteFaultError,
    DiscreteDeviceFaultAdapter,
    DiscreteDeviceFaultBackend,
    DiscreteFaultSchedule,
    DiscreteFaultWindow,
)


def _episode(scenario: str = "generic_household_workflow") -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="d1-discrete-test",
        public_bootstrap={"scenario_type": scenario, "horizon_seconds": 600},
        seed=19,
    )


def _command(device: str, capability: str, operation: str) -> dict:
    return {"kind": "act", "commands": [{
        "device_id": device,
        "capability": capability,
        "operation": operation,
        "parameters": {},
    }]}


def test_schedule_is_canonical_non_overlapping_and_fail_closed():
    schedule = DiscreteFaultSchedule([
        DiscreteFaultWindow("garage_door.main", 2, 4, "jammed"),
        DiscreteFaultWindow("laundry.washer", 1, 3, "slowdown", slowdown_factor=2),
    ])
    assert schedule.active("garage_door.main", 1) is None
    assert schedule.active("garage_door.main", 2).mode == "jammed"
    assert schedule.active("garage_door.main", 4) is None
    assert schedule.as_dict()["schedule_id"] == schedule.schedule_id
    with pytest.raises(D1DiscreteFaultError, match="overlap"):
        DiscreteFaultSchedule([
            DiscreteFaultWindow("laundry.washer", 1, 4, "offline"),
            DiscreteFaultWindow("laundry.washer", 3, 5, "jammed"),
        ])
    with pytest.raises(D1DiscreteFaultError):
        DiscreteFaultWindow("unknown", 0, 1, "offline")
    with pytest.raises(D1DiscreteFaultError):
        DiscreteFaultWindow("laundry.washer", 0, 1, "slowdown")


@pytest.mark.parametrize("mode,code", [
    ("offline", "FAULT_DEVICE_OFFLINE"),
    ("stuck", "FAULT_DEVICE_STUCK"),
    ("jammed", "FAULT_DEVICE_JAMMED"),
])
@pytest.mark.parametrize("device,capability,operation", [
    ("front_door_lock.main", "lock.control", "unlock"),
    ("garage_door.main", "garage.door", "open"),
    ("laundry.washer", "laundry.control", "load"),
    ("dishwasher.main", "dishwasher.control", "load"),
])
def test_offline_stuck_jammed_reject_command_atomically(device, capability, operation, mode, code):
    schedule = DiscreteFaultSchedule([DiscreteFaultWindow(device, 0, 2, mode)])
    backend = DiscreteDeviceFaultBackend(schedule)
    initial = backend.reset(_episode())
    digest = backend.state_digest()
    outcome = backend.execute_atomic(_command(device, capability, operation))
    assert not outcome.accepted
    assert outcome.error_code == code
    assert backend.state_digest() == digest
    assert initial.public_observation["active_device_faults"][device]["mode"] == mode


def test_jammed_garage_freezes_real_motion_then_recovers():
    faulty = DiscreteDeviceFaultBackend(DiscreteFaultSchedule([
        DiscreteFaultWindow("garage_door.main", 1, 3, "jammed"),
    ]))
    healthy = DiscreteDeviceFaultBackend()
    for backend in (faulty, healthy):
        backend.reset(_episode())
        assert backend.execute_atomic(_command("garage_door.main", "garage.door", "open")).accepted
    fault_step = faulty.advance({"kind": "act", "commands": []})
    healthy_step = healthy.advance({"kind": "act", "commands": []})
    assert fault_step.public_observation["devices"]["garage_door.main"]["state"] == "opening"
    assert healthy_step.public_observation["devices"]["garage_door.main"]["state"] == "open"
    assert fault_step.public_observation["active_device_faults"]["garage_door.main"]["mode"] == "jammed"
    recovered = faulty.advance({"kind": "act", "commands": []})
    assert recovered.public_observation["devices"]["garage_door.main"]["state"] == "opening"


def test_slowdown_washer_changes_real_cycle_duration_and_same_action_diverges():
    faulty = DiscreteDeviceFaultBackend(DiscreteFaultSchedule([
        DiscreteFaultWindow("laundry.washer", 0, 4, "slowdown", slowdown_factor=2),
    ]))
    healthy = DiscreteDeviceFaultBackend()
    for backend in (faulty, healthy):
        backend.reset(_episode())
        assert backend.execute_atomic(_command("laundry.washer", "laundry.control", "load")).accepted
        backend.advance({"kind": "act", "commands": []})
    action = _command("laundry.washer", "laundry.control", "start")
    assert faulty.execute_atomic(action).accepted
    assert healthy.execute_atomic(action).accepted
    fault_step = faulty.advance({"kind": "act", "commands": []})
    healthy_step = healthy.advance({"kind": "act", "commands": []})
    fault_remaining = fault_step.public_observation["devices"]["laundry.washer"]["attributes"]["remaining_seconds"]
    healthy_remaining = healthy_step.public_observation["devices"]["laundry.washer"]["attributes"]["remaining_seconds"]
    assert fault_remaining > healthy_remaining
    assert fault_step.public_observation["active_device_faults"]["laundry.washer"]["slowdown_factor"] == 2.0


def test_non_faultable_workflow_command_passes_through():
    backend = DiscreteDeviceFaultBackend(DiscreteFaultSchedule([
        DiscreteFaultWindow("laundry.washer", 0, 4, "slowdown", slowdown_factor=2),
    ]))
    backend.reset(_episode("laundry_completion_notification"))
    command = {
        "kind": "act",
        "commands": [{
            "device_id": "notification.service",
            "capability": "notification.send",
            "operation": "send",
            "parameters": {
                "message": "Laundry is finished.",
                "channel": "app",
                "recipients": ["resident.primary"],
            },
        }],
    }
    assert backend.execute_atomic(command).accepted


def test_future_schedule_not_leaked_and_replay_is_deterministic():
    schedule = DiscreteFaultSchedule([
        DiscreteFaultWindow("dishwasher.main", 2, 5, "offline"),
    ])

    def rollout():
        backend = DiscreteDeviceFaultBackend(schedule)
        first = backend.reset(_episode())
        rows = [first.public_observation]
        for _ in range(3):
            backend.advance({"kind": "act", "commands": []})
            rows.append(backend._step_view(False).public_observation)  # only observation, no evaluator
        return rows

    assert rollout() == rollout()
    assert rollout()[0]["active_device_faults"] == {}
    assert rollout()[2]["active_device_faults"]["dishwasher.main"]["mode"] == "offline"


def test_adapter_is_backend_only_and_provenance_is_source_bound():
    schedule = DiscreteFaultSchedule([DiscreteFaultWindow("front_door_lock.main", 0, 1, "offline")])
    adapter = DiscreteDeviceFaultAdapter(schedule)
    backend = adapter.open_backend()
    assert isinstance(backend, DiscreteDeviceFaultBackend)
    provenance = adapter.provenance()
    assert provenance["source_backend"] == "harness_v2.workflow_backend.WorkflowBackend"
    assert provenance["backend_only"] is True
    assert provenance["agent_can_modify_schedule"] is False
    assert set(provenance["supported_devices"]) == {
        "front_door_lock.main", "garage_door.main", "laundry.washer", "dishwasher.main"
    }


def test_replay_gate_is_compact_target_device_evidence_only():
    gate_path = Path(__file__).parents[1] / "generated" / "d1_discrete_device_fault_v1" / "replay_gate.json"
    assert gate_path.stat().st_size < 100_000
    gate = json.loads(gate_path.read_text(encoding="utf-8"))
    assert gate["verified"] is True
    for case in gate["cases"].values():
        assert "target_device_state_sequence" in case["fault"]
        assert "actions" in case["fault"]
        assert "witness" in case
        serialized = json.dumps(case, sort_keys=True)
        assert "inventory" not in serialized
        assert "public_profile" not in serialized
        assert '"observation": {' not in serialized
