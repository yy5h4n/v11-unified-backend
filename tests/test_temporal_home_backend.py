import pytest

from harness_v2.core import EpisodeSpec, Harness
from harness_v2.temporal_home_backend import (
    COUNT_UNIT,
    PUBLIC_ACTION_KINDS,
    TemporalHomeBackend,
)
from harness_v2.temporal_home_evaluator import evaluate_temporal_home

BANNED_KEYS = {"action_cost", "cost_unit", "ask_log"}


def episode(scenario="laundry_completion_notification", seed=7, horizon=3600, allowed=None):
    return EpisodeSpec(
        episode_id=f"temporal_home.{scenario}.{seed}",
        public_bootstrap={
            "query": "Handle this household responsibility.",
            "scenario_type": scenario,
            "horizon_seconds": horizon,
            "allowed_action_kinds": list(allowed) if allowed is not None else ["act", "wait", "install_rule", "cancel_rule"],
        },
        seed=seed,
        max_decisions=20,
    )


def command(device, capability, operation, parameters=None):
    return {"device_id": device, "capability": capability, "operation": operation, "parameters": parameters or {}}


def notify_command():
    return command(
        "notification.service",
        "notification.send",
        "send",
        {"message": "Laundry is finished.", "channel": "app", "recipients": ["resident.primary"]},
    )


def assert_no_legacy_keys(value, path="root"):
    if isinstance(value, dict):
        for key, item in value.items():
            assert key not in BANNED_KEYS, f"legacy key at {path}.{key}"
            assert_no_legacy_keys(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            assert_no_legacy_keys(item, f"{path}[{index}]")


class NotifyOnFinishPolicy:
    def decide(self, view):
        events = view["observation"]["events"]
        if any(event["type"] == "laundry_cycle_finished" for event in events):
            return {"kind": "act", "commands": [notify_command()]}
        return {"kind": "wait", "mode": "until_event", "event_filter": {"type": "laundry_cycle_finished"}, "timeout_seconds": 3600}


def test_public_action_kinds_are_exact_and_exclude_ask():
    assert PUBLIC_ACTION_KINDS == frozenset({"act", "wait", "install_rule", "cancel_rule"})
    assert TemporalHomeBackend.action_kinds == PUBLIC_ACTION_KINDS
    assert "ask" not in PUBLIC_ACTION_KINDS


def test_reset_rejects_bootstrap_that_includes_ask():
    backend = TemporalHomeBackend()
    with pytest.raises(ValueError):
        backend.reset(episode(allowed=["act", "wait", "ask"]))
    with pytest.raises(ValueError):
        backend.reset(episode(allowed=["ask"]))
    missing_declaration = episode()
    missing_declaration.public_bootstrap.pop("allowed_action_kinds")
    with pytest.raises(ValueError):
        backend.reset(missing_declaration)
    with pytest.raises(ValueError):
        backend.reset(episode(allowed=["act", "wait"]))


def test_execute_atomic_ask_is_rejected_unsupported_and_atomic():
    backend = TemporalHomeBackend()
    backend.reset(episode())
    before = backend.state_digest()
    outcome = backend.execute_atomic({"kind": "ask", "question": "is the bathroom free?"})
    assert outcome.accepted is False
    assert outcome.error_code == "UNSUPPORTED_ACTION"
    assert outcome.public_feedback["status"] == "rejected"
    assert_no_legacy_keys(outcome.public_feedback)
    assert_no_legacy_keys(outcome.private_feedback)
    assert backend.state_digest() == before


def test_reset_and_advance_views_are_fully_renamed():
    backend = TemporalHomeBackend()
    step = backend.reset(episode())
    assert_no_legacy_keys(step.public_observation)
    assert_no_legacy_keys(step.private_state)
    assert step.public_observation["metrics"]["device_command_count"] == 0
    assert step.public_observation["metrics"]["count_unit"] == COUNT_UNIT
    assert step.private_state["device_command_count"] == 0
    assert step.private_state["count_unit"] == COUNT_UNIT
    step = backend.advance({"kind": "wait", "mode": "for", "duration_seconds": 60})
    assert_no_legacy_keys(step.public_observation)
    assert_no_legacy_keys(step.private_state)


def test_immediate_command_increments_device_command_count():
    backend = TemporalHomeBackend()
    backend.reset(episode())
    outcome = backend.execute_atomic({"kind": "act", "commands": [notify_command()]})
    assert outcome.accepted is True
    assert outcome.public_feedback["device_command_count"] == 1
    assert outcome.public_feedback["count_unit"] == COUNT_UNIT
    assert "action_cost" not in outcome.public_feedback
    assert_no_legacy_keys(outcome.public_feedback)
    assert_no_legacy_keys(outcome.private_feedback)
    step = backend.advance({"kind": "wait", "mode": "for", "duration_seconds": 60})
    assert step.private_state["device_command_count"] == 1
    assert step.public_observation["metrics"]["device_command_count"] == 1


def test_installed_rule_commands_increment_device_command_count():
    backend = TemporalHomeBackend()
    backend.reset(episode())
    outcome = backend.execute_atomic({"kind": "act", "commands": [notify_command()]})
    assert outcome.accepted is True
    rule = {"rule_id": "rule.notify.again", "fire_at_step": 2, "commands": [notify_command()]}
    install = backend.execute_atomic({"kind": "install_rule", "rule": rule})
    assert install.accepted is True
    step = backend.advance({"kind": "wait", "mode": "for", "duration_seconds": 120})
    assert any(event["type"] == "rule_fired" and event["rule_id"] == "rule.notify.again" for event in step.public_observation["events"])
    assert step.private_state["device_command_count"] == 2
    assert step.public_observation["metrics"]["device_command_count"] == 2
    applied = step.private_state["applied_commands"]
    assert sum(1 for record in applied if record["source"].startswith("rule")) >= 1
    assert all(record["device_command_count"] == 1 and record["count_unit"] == COUNT_UNIT for record in applied)
    assert_no_legacy_keys(step.private_state)


def test_cancelled_rule_never_fires_and_count_stays():
    backend = TemporalHomeBackend()
    backend.reset(episode())
    rule = {"rule_id": "rule.cancelled", "fire_at_step": 1, "commands": [notify_command()]}
    assert backend.execute_atomic({"kind": "install_rule", "rule": rule}).accepted is True
    cancel = backend.execute_atomic({"kind": "cancel_rule", "rule_id": "rule.cancelled"})
    assert cancel.accepted is True
    step = backend.advance({"kind": "wait", "mode": "for", "duration_seconds": 120})
    assert not any(event["type"] == "rule_fired" for event in step.public_observation["events"])
    assert step.private_state["device_command_count"] == 0


def test_closed_loop_replay_is_deterministic_and_renamed():
    first = Harness(TemporalHomeBackend()).run_one(episode(), NotifyOnFinishPolicy())
    second = Harness(TemporalHomeBackend()).run_one(episode(), NotifyOnFinishPolicy())
    assert first.status == second.status == "completed"
    assert first.trace_digest == second.trace_digest
    for row in list(first.public_trace) + list(first.private_trace):
        assert_no_legacy_keys(row)
    final = [row["value"] for row in first.private_trace if row["type"] == "backend_state"][-1]
    assert final["device_command_count"] == 1


def test_evaluator_returns_device_command_count_without_action_cost():
    run = Harness(TemporalHomeBackend()).run_one(episode(), NotifyOnFinishPolicy())
    result = evaluate_temporal_home("laundry_completion_notification", run)
    assert result["run_status"] == "completed"
    assert result["success"] is True
    assert "action_cost" not in result
    assert "cost_unit" not in result
    terminal = [row["value"] for row in run.private_trace if row["type"] == "backend_state"][-1]
    assert result["device_command_count"] == terminal["device_command_count"] == 1
    assert result["count_unit"] == COUNT_UNIT
