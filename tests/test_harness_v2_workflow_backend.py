import json

import pytest

from harness_v2.core import EpisodeSpec, Harness
from harness_v2.workflow_backend import DEVICE_SPECS, WorkflowBackend


def episode(scenario="laundry_completion_notification", seed=7, horizon=3600):
    return EpisodeSpec(
        episode_id=f"workflow.{scenario}.{seed}",
        public_bootstrap={
            "query": "Handle this household responsibility.",
            "scenario_type": scenario,
            "horizon_seconds": horizon,
            "allowed_action_kinds": ["act", "wait", "install_rule", "cancel_rule", "ask"],
        },
        seed=seed,
        max_decisions=20,
    )


def command(device, capability, operation, parameters=None):
    return {"device_id": device, "capability": capability, "operation": operation, "parameters": parameters or {}}


class LaundryPolicy:
    def decide(self, view):
        state = view["observation"]["devices"]["laundry.washer"]["state"]
        events = view["observation"]["events"]
        if any(event["type"] == "laundry_cycle_finished" for event in events):
            return {"kind": "act", "commands": [command("notification.service", "notification.send", "send", {"message": "Laundry is finished.", "channel": "app"})]}
        if state == "loaded":
            return {"kind": "act", "commands": [command("laundry.washer", "laundry.control", "start")]}
        return {"kind": "wait", "mode": "until_event", "event_filter": {"type": "laundry_cycle_finished"}, "timeout_seconds": 3600}


def test_public_bootstrap_contains_complete_inventory_and_no_energy_claim():
    step = WorkflowBackend().reset(episode())
    inventory = step.public_observation["inventory"]
    assert inventory["complete"] is True
    assert {item["device_id"] for item in inventory["devices"]} == set(DEVICE_SPECS)
    wire = json.dumps(step.public_observation)
    assert "notification.send" in wire
    assert "energy_kwh" not in wire and "measured_energy" not in wire


def test_laundry_cycle_notification_is_causal_and_deterministic():
    first = Harness(WorkflowBackend()).run_one(episode(), LaundryPolicy())
    second = Harness(WorkflowBackend()).run_one(episode(), LaundryPolicy())
    assert first.status == second.status == "completed"
    assert first.trace_digest == second.trace_digest
    events = [event for row in first.public_trace if row["type"] == "observation" for event in row["value"]["events"]]
    assert any(event["type"] == "laundry_cycle_finished" for event in events)
    assert any(event["type"] == "notification_sent" and event["message"] == "Laundry is finished." for event in events)
    final = [row["value"] for row in first.private_trace if row["type"] == "backend_state"][-1]
    # The completion responsibility begins with a user-started wash; the Agent
    # only sends the completion notice.
    assert final["action_cost"] == 1
    assert final["device_runtime_seconds"]["laundry.washer"] == 2700


def test_rejected_duplicate_device_batch_is_atomic():
    backend = WorkflowBackend()
    backend.reset(episode("weekly_floor_cleaning"))
    before = backend.state_digest()
    item = command("vacuum.robot", "vacuum.control", "start_cleaning")
    outcome = backend.execute_atomic({"kind": "act", "commands": [item, item]})
    assert outcome.accepted is False
    assert outcome.error_code == "DUPLICATE_DEVICE_COMMAND"
    assert backend.state_digest() == before


@pytest.mark.parametrize("scenario,event_type", [
    ("away_intercom_notification", "visitor_rang"),
    ("mail_arrival_notification", "mail_delivered"),
    ("bathroom_occupancy_indicator", "bathroom_occupied"),
    ("departure_lockdown", "occupants_departed"),
    ("failed_entry_notification", "failed_entry_attempt"),
    ("expected_delivery_gate_and_notice", "expected_delivery_arrived"),
    ("post_parking_garage_closure", "vehicle_parked"),
])
def test_scenario_specific_exogenous_event_is_seed_pinned(scenario, event_type):
    def first_event(seed):
        backend = WorkflowBackend()
        backend.reset(episode(scenario, seed))
        observed = []
        for _ in range(60):
            step = backend.advance({"kind": "wait", "mode": "for", "duration_seconds": 3600})
            observed.extend(step.public_observation["events"])
            if any(event["type"] == event_type for event in observed):
                return step.public_observation["step"], observed
        raise AssertionError("no event")
    assert first_event(11) == first_event(11)
    assert any(event["type"] == event_type for event in first_event(11)[1])


def test_wait_until_event_ignores_unrelated_event_without_hiding_it():
    backend = WorkflowBackend()
    backend.reset(episode("mail_arrival_notification", seed=3))
    step = backend.advance({"kind": "wait", "mode": "until_event", "event_filter": {"type": "never"}, "timeout_seconds": 3600})
    assert step.public_observation["events"]
    assert step.public_observation["step"] == 60
    assert any(event["type"] == "mail_delivered" for event in step.public_observation["events"])


def test_notification_parameters_fail_closed():
    backend = WorkflowBackend()
    backend.reset(episode())
    before = backend.state_digest()
    bad = command("notification.service", "notification.send", "send", {"message": "x", "channel": "telepathy"})
    outcome = backend.execute_atomic({"kind": "act", "commands": [bad]})
    assert outcome.accepted is False
    assert backend.state_digest() == before


def test_scenario_initial_conditions_create_real_intervention_opportunities():
    cases = {
        "fridge_door_left_open": ("fridge_door.main", "open"),
        "unattended_stove_guard": ("stove.main", "on"),
        "departure_lockdown": ("front_door_lock.main", "unlocked"),
        "post_parking_garage_closure": ("garage_door.main", "open"),
        "laundry_backlog_management": ("laundry.washer", "loaded"),
    }
    for scenario, (device, state) in cases.items():
        observation = WorkflowBackend().reset(episode(scenario)).public_observation
        assert observation["devices"][device]["state"] == state
