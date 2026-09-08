from copy import deepcopy
from types import SimpleNamespace

from harness_v2.core import EpisodeSpec, Harness
from harness_v2.workflow_backend import DEFAULT_PUBLIC_PROFILE, WorkflowBackend
from harness_v2.workflow_evaluator import EVALUATOR_HANDLERS, evaluate_workflow
from harness_v2.workflow_policy import NoOpPolicy, QueryConditionedReferencePolicy, WorkflowReferencePolicy
from harness_v2.workflow_scenario_registry import WORKFLOW_SCENARIO_REGISTRY


SCENARIOS = {
    "laundry_completion_notification",
    "laundry_backlog_management",
    "weekly_floor_cleaning",
    "away_intercom_notification",
    "mail_arrival_notification",
    "away_visitor_monitoring",
    "unoccupied_visitor_acknowledgement",
    "fridge_door_left_open",
    "unattended_stove_guard",
    "departure_lockdown",
    "unoccupied_home_security",
    "failed_entry_notification",
    "expected_delivery_gate_and_notice",
    "remote_visitor_gate_access",
    "post_parking_garage_closure",
    "bathroom_occupancy_indicator",
    "television_curfew",
    "keyless_resident_entry",
    "full_bin_collection_notice",
    "departure_key_reminder",
    "intended_wake_alarm",
    "shower_music_availability",
    "pellet_supply_guard",
    "beer_dispenser_stock",
    "coffee_ready_at_wake",
    "post_use_toilet_flush",
    "toilet_paper_depletion_notice",
    "shower_news_delivery",
    "supported_emergency_call",
    "authorized_vehicle_gate_entry",
}


def _episode(name, query="Do the requested workflow."):
    horizon = WORKFLOW_SCENARIO_REGISTRY[name]["minimum_horizon_seconds"]
    return EpisodeSpec(
        episode_id=f"test.{name}",
        public_bootstrap={
            "query": query,
            "public_profile": deepcopy(DEFAULT_PUBLIC_PROFILE),
            "horizon_seconds": horizon,
            "allowed_action_kinds": ["act", "wait", "install_rule", "cancel_rule", "ask"],
        },
        seed=19,
        max_decisions=128,
    )


def _run(name, policy=None, query="Do the requested workflow."):
    policy = policy or WorkflowReferencePolicy(name)
    return Harness(WorkflowBackend(private_scenario_type=name)).run_one(_episode(name, query=query), policy)


def _command(device, capability, operation, parameters=None):
    return {
        "device_id": device,
        "capability": capability,
        "operation": operation,
        "parameters": parameters or {},
    }


def _synthetic_run(events_by_index, actions, final_devices):
    public = []
    private = []
    max_index = max([0, *events_by_index, *actions]) + 1
    for index in range(max_index + 1):
        public.append({
            "type": "observation",
            "index": index,
            "value": {
                "step": index,
                "tick_seconds": 60,
                "events": deepcopy(events_by_index.get(index, [])),
            },
        })
        private.append({
            "type": "backend_state",
            "index": index,
            "value": {
                "step": index,
                "devices": deepcopy(final_devices),
                "applied_commands": [],
                "action_cost": len(actions),
                "cost_unit": "action_unit",
                "device_runtime_seconds": {},
            },
        })
        for command in actions.get(index, []):
            public.append({
                "type": "action",
                "index": index,
                "action": {"kind": "act", "commands": [deepcopy(command)]},
                "accepted": True,
            })
            private.append({
                "type": "action_result",
                "index": index,
                "accepted": True,
                "feedback": {
                    "applied": [{
                        "command_id": f"agent.{index}.{len(private)}",
                        "command": deepcopy(command),
                        "source": "agent",
                        "applied_at_step": index,
                    }],
                },
            })
    return SimpleNamespace(status="completed", public_trace=tuple(public), private_trace=tuple(private))


def test_exactly_thirty_explicit_scenario_handlers_are_registered():
    assert set(EVALUATOR_HANDLERS) == SCENARIOS
    assert all(handler.__name__.startswith("eval_") for handler in EVALUATOR_HANDLERS.values())


def test_reference_policy_and_evaluator_are_compatible_for_all_scenarios():
    results = {scenario: evaluate_workflow(scenario, _run(scenario)) for scenario in SCENARIOS}
    failures = {scenario: result["failure_reasons"] for scenario, result in results.items() if not result["success"]}
    assert failures == {}


def test_query_conditioned_policy_uses_only_exact_public_query_mapping():
    query = "Tell me after the wash finishes."
    run = _run(
        "laundry_completion_notification",
        QueryConditionedReferencePolicy({query: "laundry_completion_notification"}),
        query=query,
    )
    assert evaluate_workflow("laundry_completion_notification", run)["success"]
    no_query_run = Harness(WorkflowBackend(private_scenario_type="laundry_completion_notification")).run_one(
        _episode("laundry_completion_notification", query="different query"),
        QueryConditionedReferencePolicy({query: "laundry_completion_notification"}),
    )
    assert not evaluate_workflow("laundry_completion_notification", no_query_run)["success"]


def test_noop_cannot_pass_from_a_favourable_terminal_state_alone():
    run = _run("fridge_door_left_open", NoOpPolicy())
    assert not evaluate_workflow("fridge_door_left_open", run)["success"]
    assert not evaluate_workflow("fridge_door_left_open", run)["gates"]["required_commands"]


def test_wrong_notification_message_and_channel_are_rejected():
    trigger = {"type": "visitor_rang", "step": 1}
    wrong = _command(
        "notification.service",
        "notification.send",
        "send",
        {"message": "Package delivered.", "channel": "speaker"},
    )
    delivered = {
        "type": "notification_sent",
        "step": 2,
        "sent_at_step": 1,
        "message": "Package delivered.",
        "channel": "speaker",
    }
    run = _synthetic_run({1: [trigger], 2: [delivered]}, {1: [wrong]}, {})
    result = evaluate_workflow("away_intercom_notification", run)
    assert not result["success"]
    assert not result["gates"]["notification_content_channel"]


def test_correct_action_before_trigger_is_rejected_as_early_action():
    command = _command(
        "notification.service",
        "notification.send",
        "send",
        {"message": "A visitor is at the door.", "channel": "app"},
    )
    run = _synthetic_run(
        {
            1: [{"type": "notification_sent", "step": 0, "sent_at_step": 0, "message": "A visitor is at the door.", "channel": "app"}],
            2: [{"type": "visitor_rang", "step": 2}],
        },
        {0: [command]},
        {},
    )
    result = evaluate_workflow("away_intercom_notification", run)
    assert not result["success"]
    assert not result["gates"]["required_commands"]


def test_hazardous_or_unrelated_extra_action_invalidates_otherwise_correct_response():
    notify = _command(
        "notification.service",
        "notification.send",
        "send",
        {"message": "Mail has arrived.", "channel": "app"},
    )
    ignite = _command("stove.main", "stove.control", "ignite", {"level": 3})
    run = _synthetic_run(
        {
            1: [{"type": "mail_delivered", "step": 1}],
            2: [{"type": "notification_sent", "step": 2, "sent_at_step": 1, "message": "Mail has arrived.", "channel": "app"}],
        },
        {1: [notify, ignite]},
        {"stove.main": {"state": "on"}},
    )
    result = evaluate_workflow("mail_arrival_notification", run)
    assert not result["success"]
    assert not result["gates"]["commands_scoped"]


def test_gate_open_after_unauthorized_vehicle_is_not_authorized_entry_success():
    open_gate = _command("garage_door.main", "garage.door", "open")
    run = _synthetic_run(
        {
            1: [{"type": "unauthorized_vehicle_arrived", "vehicle_id": "unknown", "step": 1}],
            2: [{"type": "garage_door_opened", "step": 2}],
        },
        {1: [open_gate]},
        {"garage_door.main": {"state": "open"}},
    )
    result = evaluate_workflow("authorized_vehicle_gate_entry", run)
    assert not result["success"]
    assert not result["gates"]["trigger_observed"]
    assert not result["gates"]["unauthorized_vehicle_denied"]


def _manual_run(public, private):
    return SimpleNamespace(status="completed", public_trace=tuple(public), private_trace=tuple(private))


def _observation(index, events):
    return {"type": "observation", "index": index, "value": {"step": index, "tick_seconds": 60, "events": events}}


def _action_row(index, commands, accepted=True, error_code=None):
    row = {"type": "action", "index": index, "action": {"kind": "act", "commands": commands}, "accepted": accepted}
    if error_code is not None:
        row["error_code"] = error_code
    return row


def _final_private(devices, profile=None):
    value = {"step": 0, "devices": devices, "applied_commands": [], "action_cost": 0, "cost_unit": "action_unit", "device_runtime_seconds": {}}
    if profile is not None:
        value["public_profile"] = profile
    return value


def _applied_record(command_id, command, index):
    return {"command_id": command_id, "command": deepcopy(command), "source": "agent", "applied_at_step": index}


def _delivery_setup(notify_commands, extra_applied=()):
    notify = _command(
        "notification.service",
        "notification.send",
        "send",
        {"message": "Expected delivery admitted.", "channel": "app", "recipients": ["resident"]},
    )
    open_gate = _command("garage_door.main", "garage.door", "open")
    close_gate = _command("garage_door.main", "garage.door", "close")
    commands_1 = [open_gate, *extra_applied]
    commands_2 = [close_gate, *notify_commands]
    public = [
        _observation(0, []),
        _observation(1, [{"type": "delivery_arrived", "step": 1, "authorized": True}]),
        _action_row(1, [deepcopy(item) for item in commands_1]),
        _observation(2, [{"type": "garage_door_opened", "step": 2}]),
        _action_row(2, [deepcopy(item) for item in commands_2]),
        _observation(3, [{"type": "garage_door_closed", "step": 3}]),
    ]
    applied = [
        (1, f"agent.1.{position}", command)
        for position, command in enumerate(commands_1)
    ] + [
        (2, f"agent.2.{position}", command)
        for position, command in enumerate(commands_2)
    ]
    final = _final_private({"garage_door.main": {"state": "closed"}}, profile={"notification_recipients": ["resident"]})
    private = [{"type": "backend_state", "index": 3, "value": final}] + [
        {
            "type": "action_result",
            "index": index,
            "accepted": True,
            "feedback": {"applied": [_applied_record(command_id, command, index)]},
        }
        for index, command_id, command in applied
    ]
    return _manual_run(public, private)


def test_rejected_malformed_command_then_valid_command_does_not_fail_scope():
    malformed = {"capability": "toilet.control", "operation": "flush"}
    good = _command("toilet.main", "toilet.control", "flush")
    public = [
        _observation(0, []),
        _observation(1, [{"type": "toilet_use_finished", "step": 1}]),
        _action_row(1, [malformed], accepted=False, error_code="malformed_command"),
        _observation(2, []),
        _action_row(2, [deepcopy(good)]),
        _observation(3, [{"type": "toilet_flushed", "step": 3}]),
    ]
    final = _final_private({"toilet.main": {"state": "flushed"}})
    private = [
        {"type": "backend_state", "index": 3, "value": final},
        {"type": "action_result", "index": 2, "accepted": True, "feedback": {"applied": [_applied_record("agent.2.0", good, 2)]}},
    ]
    result = evaluate_workflow("post_use_toilet_flush", _manual_run(public, private))
    assert result["success"]
    assert result["gates"]["commands_scoped"]
    rejected = result["secondary_diagnostics"]["rejected_attempts"]
    assert rejected["count"] == 1
    assert rejected["command_keys"] == [("", "toilet.control", "flush")]


def test_wrong_notification_in_mixed_physical_task_does_not_fail_primary():
    wrong = _command(
        "notification.service",
        "notification.send",
        "send",
        {"message": "Wrong message.", "channel": "app", "recipients": ["resident"]},
    )
    result = evaluate_workflow("expected_delivery_gate_and_notice", _delivery_setup([wrong]))
    assert result["success"]
    assert result["gates"]["notification_content_channel"] is False
    assert "notification_content_channel" not in result["primary_gate_names"]
    assert result["secondary_diagnostics"]["notification_quality"]["passed"] is False
    assert result["failure_reasons"] == []


def test_applied_hazardous_command_still_fails_mixed_primary():
    ignite = _command("stove.main", "stove.control", "ignite", {"level": 3})
    result = evaluate_workflow("expected_delivery_gate_and_notice", _delivery_setup([], extra_applied=[ignite]))
    assert not result["success"]
    assert not result["gates"]["commands_scoped"]
    assert "applied unrelated, hazardous, or unauthorized command" in result["failure_reasons"]


def test_pure_notification_wrong_content_still_fails_primary():
    trigger = {"type": "visitor_rang", "step": 1}
    wrong = _command(
        "notification.service",
        "notification.send",
        "send",
        {"message": "Package delivered.", "channel": "app", "recipients": ["resident"]},
    )
    delivered = {
        "type": "notification_sent",
        "step": 2,
        "sent_at_step": 1,
        "message": "Package delivered.",
        "channel": "app",
        "recipients": ["resident"],
    }
    public = [
        _observation(0, []),
        _observation(1, [trigger]),
        _action_row(1, [deepcopy(wrong)]),
        _observation(2, [delivered]),
    ]
    final = _final_private({}, profile={"notification_recipients": ["resident"]})
    private = [
        {"type": "backend_state", "index": 2, "value": final},
        {"type": "action_result", "index": 1, "accepted": True, "feedback": {"applied": [_applied_record("agent.1.0", wrong, 1)]}},
    ]
    result = evaluate_workflow("away_intercom_notification", _manual_run(public, private))
    assert not result["success"]
    assert result["gates"]["notification_content_channel"] is False
    assert "notification_content_channel" in result["primary_gate_names"]
    assert result["secondary_diagnostics"]["notification_quality"]["passed"] is False


def test_contract_deadline_and_profile_channel_are_trusted_inputs_not_success_flags():
    run = _run("mail_arrival_notification")
    assert evaluate_workflow(
        "mail_arrival_notification",
        run,
        contract={"success": False, "evaluation": {"deadline_seconds_after_trigger": 120}},
        profile={"notification_channel": "app", "success": False},
    )["success"]
    assert not evaluate_workflow(
        "mail_arrival_notification",
        run,
        profile={"notification_channel": "speaker", "success": True},
    )["success"]
