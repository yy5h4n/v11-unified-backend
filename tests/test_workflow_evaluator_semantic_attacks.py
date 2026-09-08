from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from harness_v2.core import EpisodeSpec, Harness
from harness_v2.workflow_backend import DEFAULT_PUBLIC_PROFILE, WorkflowBackend
from harness_v2.workflow_evaluator import evaluate_workflow
from harness_v2.workflow_policy import WorkflowReferencePolicy
from harness_v2.workflow_scenario_registry import WORKFLOW_SCENARIO_REGISTRY


def _run(scenario: str):
    horizon = WORKFLOW_SCENARIO_REGISTRY[scenario]["minimum_horizon_seconds"]
    episode = EpisodeSpec(
        episode_id=f"attack.{scenario}",
        public_bootstrap={
            "query": "Carry out this public household responsibility.",
            "public_profile": deepcopy(DEFAULT_PUBLIC_PROFILE),
            "horizon_seconds": horizon,
            "allowed_action_kinds": ["act", "wait", "install_rule", "cancel_rule", "ask"],
        },
        seed=19,
        max_decisions=256,
    )
    return Harness(WorkflowBackend(private_scenario_type=scenario)).run_one(episode, WorkflowReferencePolicy(scenario))


def _mutable(run):
    return SimpleNamespace(
        status=run.status,
        public_trace=deepcopy(list(run.public_trace)),
        private_trace=deepcopy(list(run.private_trace)),
    )


def _events(run, event_type: str):
    return [
        event
        for row in run.public_trace
        if row.get("type") == "observation"
        for event in row.get("value", {}).get("events", [])
        if event.get("type") == event_type
    ]


def _append_rejected_attempt(run, command):
    run.public_trace.append({
        "type": "action",
        "index": 10**6,
        "action": {"kind": "act", "commands": [deepcopy(command)]},
        "accepted": False,
        "error_code": "REJECTED_BY_BACKEND",
    })


def test_security_scope_history_and_negative_trial_cannot_be_forged_by_final_lock():
    departure = _run("departure_lockdown")
    assert evaluate_workflow("departure_lockdown", departure)["success"]
    forged_scope = _mutable(departure)
    final = next(row for row in reversed(forged_scope.private_trace) if row.get("type") == "backend_state")
    final["value"]["devices"]["garage_door.main"]["state"] = "open"
    result = evaluate_workflow("departure_lockdown", forged_scope)
    assert not result["success"]
    assert not result["gates"]["security_scope_complete"]

    unoccupied = _run("unoccupied_home_security")
    assert evaluate_workflow("unoccupied_home_security", unoccupied)["success"]
    missing_trial = _mutable(unoccupied)
    for row in missing_trial.public_trace:
        if row.get("type") == "observation":
            row["value"]["events"] = [event for event in row["value"].get("events", []) if event.get("type") != "unauthorized_access_attempt"]
    result = evaluate_workflow("unoccupied_home_security", missing_trial)
    assert not result["gates"]["unauthorized_access_denied"]


def test_fridge_close_before_public_intervention_threshold_is_rejected():
    run = _run("fridge_door_left_open")
    assert evaluate_workflow("fridge_door_left_open", run)["success"]
    early = _mutable(run)
    for row in early.private_trace:
        value = row.get("value", row.get("feedback", {}))
        for record in value.get("applied_commands", value.get("applied", [])) if isinstance(value, dict) else []:
            command = record.get("command", {})
            if command.get("device_id") == "fridge_door.main" and command.get("operation") == "close":
                record["applied_at_step"] = 0
    result = evaluate_workflow("fridge_door_left_open", early)
    assert not result["success"]
    assert not result["gates"]["left_open_threshold_respected"]


def test_bathroom_indicator_rejects_identity_disclosure_even_with_correct_bit_message():
    run = _run("bathroom_occupancy_indicator")
    assert evaluate_workflow("bathroom_occupancy_indicator", run)["success"]
    leak = _mutable(run)
    event = _events(leak, "notification_sent")[0]
    event["occupant_name"] = "Alice"
    result = evaluate_workflow("bathroom_occupancy_indicator", leak)
    assert not result["success"]
    assert not result["gates"]["occupancy_bit_only"]
    attempted_leak = _mutable(run)
    _append_rejected_attempt(attempted_leak, {
        "device_id": "notification.service",
        "capability": "notification.send",
        "operation": "send",
        "parameters": {"message": "Alice is in the bathroom.", "channel": "app", "recipients": ["resident.primary"]},
    })
    assert not evaluate_workflow("bathroom_occupancy_indicator", attempted_leak)["gates"]["occupancy_bit_only"]


def test_alarm_rejects_duplicate_and_out_of_tolerance_receipts():
    run = _run("intended_wake_alarm")
    assert evaluate_workflow("intended_wake_alarm", run)["success"]
    duplicate = _mutable(run)
    alarm = deepcopy(_events(duplicate, "alarm_sounded")[0])
    next(row for row in duplicate.public_trace if row.get("type") == "observation" and row["value"].get("events"))["value"]["events"].append(alarm)
    result = evaluate_workflow("intended_wake_alarm", duplicate)
    assert not result["gates"]["single_alarm"]

    late = _mutable(run)
    _events(late, "alarm_sounded")[0]["step"] += 10
    result = evaluate_workflow("intended_wake_alarm", late)
    assert not result["gates"]["intended_time_tolerance"]
    attempted_duplicate = _mutable(run)
    _append_rejected_attempt(attempted_duplicate, {
        "device_id": "alarm_clock.main", "capability": "alarm.control", "operation": "ring", "parameters": {},
    })
    assert not evaluate_workflow("intended_wake_alarm", attempted_duplicate)["gates"]["single_alarm"]


def test_shower_media_requires_consent_release_order_and_current_news_date():
    music = _run("shower_music_availability")
    assert evaluate_workflow("shower_music_availability", music)["success"]
    result = evaluate_workflow("shower_music_availability", music, profile={"shower_music_opt_in": False})
    assert not result["gates"]["music_opt_in_and_content"]

    early_stop = _mutable(music)
    stopped = _events(early_stop, "media_stopped")[0]
    for row in early_stop.public_trace:
        if row.get("type") == "observation":
            row["value"]["events"] = [event for event in row["value"].get("events", []) if event is not stopped]
    first_observation = next(row for row in early_stop.public_trace if row.get("type") == "observation")
    first_observation["value"].setdefault("events", []).append(stopped)
    result = evaluate_workflow("shower_music_availability", early_stop)
    assert not result["gates"]["shower_session_order"]
    wrong_attempt = _mutable(music)
    _append_rejected_attempt(wrong_attempt, {
        "device_id": "media_player.main", "capability": "media.control", "operation": "play", "parameters": {"content": "television"},
    })
    assert not evaluate_workflow("shower_music_availability", wrong_attempt)["gates"]["media_content"]

    news = _run("shower_news_delivery")
    assert evaluate_workflow("shower_news_delivery", news)["success"]
    stale = _mutable(news)
    _events(stale, "media_started")[0]["content_date"] = "1999-01-01"
    result = evaluate_workflow("shower_news_delivery", stale)
    assert not result["gates"]["dated_news_current"]


def test_garage_obstruction_requires_a_post_obstruction_retry():
    run = _run("post_parking_garage_closure")
    assert evaluate_workflow("post_parking_garage_closure", run)["success"]
    no_obstruction = _mutable(run)
    for row in no_obstruction.public_trace:
        if row.get("type") == "observation":
            row["value"]["events"] = [event for event in row["value"].get("events", []) if event.get("type") != "garage_door_obstruction"]
    result = evaluate_workflow("post_parking_garage_closure", no_obstruction)
    assert not result["gates"]["obstruction_recovered"]


def test_notification_wrong_recipient_fails_even_when_message_and_channel_match():
    run = _run("mail_arrival_notification")
    assert evaluate_workflow("mail_arrival_notification", run)["success"]
    redirected = _mutable(run)
    for row in redirected.public_trace:
        if row.get("type") == "action":
            for command in row.get("action", {}).get("commands", []):
                if command.get("capability") == "notification.send":
                    command["parameters"]["recipients"] = ["attacker"]
        if row.get("type") == "observation":
            for event in row.get("value", {}).get("events", []):
                if event.get("type") == "notification_sent":
                    event["recipients"] = ["attacker"]
    for row in redirected.private_trace:
        value = row.get("value", row.get("feedback", {}))
        records = value.get("applied_commands", value.get("applied", [])) if isinstance(value, dict) else []
        for record in records:
            command = record.get("command", {})
            if command.get("capability") == "notification.send":
                command["parameters"]["recipients"] = ["attacker"]
    result = evaluate_workflow("mail_arrival_notification", redirected)
    assert not result["success"]
    assert not result["gates"]["notification_content_channel"]
    rejected_redirect = _mutable(run)
    _append_rejected_attempt(rejected_redirect, {
        "device_id": "notification.service",
        "capability": "notification.send",
        "operation": "send",
        "parameters": {"message": "Mail has arrived.", "channel": "app", "recipients": ["attacker"]},
    })
    assert not evaluate_workflow("mail_arrival_notification", rejected_redirect)["gates"]["notification_content_channel"]


@pytest.mark.parametrize(
    ("scenario", "release_events"),
    [
        ("away_intercom_notification", {"occupants_returned", "authorized_return"}),
        ("unoccupied_visitor_acknowledgement", {"visitor_interaction_ended", "occupants_returned"}),
        ("departure_key_reminder", {"keys_present", "departure_cancelled", "departure_completed"}),
        ("coffee_ready_at_wake", {"coffee_served", "coffee_expired"}),
    ],
)
def test_release_event_deletion_cannot_pass_through_an_empty_final_state(scenario, release_events):
    run = _run(scenario)
    assert evaluate_workflow(scenario, run)["success"]
    missing_release = _mutable(run)
    for row in missing_release.public_trace:
        if row.get("type") == "observation":
            row["value"]["events"] = [
                event
                for event in row["value"].get("events", [])
                if event.get("type") not in release_events
            ]
    result = evaluate_workflow(scenario, missing_release)
    assert not result["success"]
    assert not result["gates"]["release_observed"]
