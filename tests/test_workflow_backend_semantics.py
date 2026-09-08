from __future__ import annotations

from harness_v2.core import EpisodeSpec
from harness_v2.workflow_backend import DEFAULT_PUBLIC_PROFILE, WorkflowBackend
from harness_v2.workflow_scenario_registry import WORKFLOW_SCENARIO_REGISTRY, match_current_backend


def _episode(scenario: str, *, seed: int = 12, horizon: int | None = None, profile=None) -> EpisodeSpec:
    minimum = WORKFLOW_SCENARIO_REGISTRY[scenario]["minimum_horizon_seconds"]
    return EpisodeSpec(
        episode_id=f"semantic.{scenario}.{seed}",
        public_bootstrap={
            "query": "A public household request.",
            "public_profile": profile or {},
            "horizon_seconds": horizon or minimum,
            "allowed_action_kinds": ["act", "wait", "install_rule", "cancel_rule", "ask"],
        },
        seed=seed,
        max_decisions=1000,
    )


def _backend(scenario: str, *, seed: int = 12, horizon: int | None = None, profile=None):
    spec = _episode(scenario, seed=seed, horizon=horizon, profile=profile)
    backend = WorkflowBackend(private_scenario_type=scenario, private_horizon_seconds=spec.public_bootstrap["horizon_seconds"])
    return backend, backend.reset(spec)


def _command(device: str, capability: str, operation: str, parameters=None):
    return {"device_id": device, "capability": capability, "operation": operation, "parameters": parameters or {}}


def _tick(backend: WorkflowBackend, count: int = 1):
    events = []
    step = None
    for _ in range(count):
        step = backend.advance({"kind": "act", "commands": []})
        events.extend(step.public_observation["events"])
    return step, events


def _until(backend: WorkflowBackend, event_type: str, timeout_seconds: int = 604800):
    step = backend.advance({
        "kind": "wait",
        "mode": "until_event",
        "event_filter": {"type": event_type},
        "timeout_seconds": timeout_seconds,
    })
    return step, step.public_observation["events"]


def test_capability_claims_are_backed_by_public_profile_and_all_30_match_full():
    assert all(match_current_backend(name)["status"] == "FULL" for name in WORKFLOW_SCENARIO_REGISTRY)
    required_profile = {
        key
        for spec in WORKFLOW_SCENARIO_REGISTRY.values()
        for key in spec["profile_requirements"]
    }
    assert required_profile <= set(DEFAULT_PUBLIC_PROFILE)
    backend, initial = _backend("coffee_ready_at_wake")
    assert required_profile <= set(initial.public_observation["public_context"])
    assert initial.public_observation["public_context"]["coffee_brew_seconds"] > 0
    assert "remaining_seconds" in initial.public_observation["devices"]["coffee_maker.main"]["attributes"]


def test_away_lifecycle_visitor_log_and_intercom_session_are_causal():
    backend, _ = _backend("away_visitor_monitoring")
    step, events = _until(backend, "visitor_rang")
    assert [event["type"] for event in events if event["type"] in {"away_started", "visitor_rang", "visitor_recorded"}] == [
        "away_started", "visitor_rang", "visitor_recorded"
    ]
    assert step.public_observation["household"]["occupancy"] == {"status": "away", "count": 0}
    assert step.public_observation["workflow"]["visitor_event_log"][0]["authorized"] is True
    answer = _command("visitor_intercom.main", "intercom.control", "answer")
    assert backend.execute_atomic({"kind": "act", "commands": [answer]}).accepted
    _, ended = _tick(backend)
    assert {event["type"] for event in ended} >= {"visitor_answered", "visitor_interaction_ended"}


def test_weekly_occurrence_is_due_once_and_completed_by_real_vacuum_cycle():
    backend, _ = _backend("weekly_floor_cleaning")
    step, events = _tick(backend, 2)
    due = next(event for event in events if event["type"] == "weekly_cleaning_due")
    occurrence_id = due["occurrence_id"]
    start = _command("vacuum.robot", "vacuum.control", "start_cleaning")
    assert backend.execute_atomic({"kind": "act", "commands": [start]}).accepted
    step, events = _tick(backend, 32)
    assert any(event["type"] == "vacuum_docked" for event in events)
    assert step.public_observation["workflow"]["occurrences"][occurrence_id]["status"] == "completed"


def test_delivery_allowlist_requires_identity_and_gate_is_explicitly_reclosed():
    backend, _ = _backend("expected_delivery_gate_and_notice")
    open_gate = _command("garage_door.main", "garage.door", "open")
    assert backend.execute_atomic({"kind": "act", "commands": [open_gate]}).error_code == "IDENTITY_NOT_AUTHORIZED"
    _, events = _until(backend, "delivery_arrived")
    delivery = next(event for event in events if event["type"] == "delivery_arrived")
    assert delivery["delivery_id"] == "delivery.expected" and delivery["authorized"] is True
    assert backend.execute_atomic({"kind": "act", "commands": [open_gate]}).accepted
    _tick(backend)
    close_gate = _command("garage_door.main", "garage.door", "close")
    assert backend.execute_atomic({"kind": "act", "commands": [close_gate]}).accepted
    step, closed = _tick(backend)
    assert any(event["type"] == "garage_door_closed" for event in closed)
    assert step.public_observation["workflow"]["sessions"]["garage_access"]["state"] == "closed"
    _, later = _until(backend, "delivery_arrived")
    unauthorized = next(event for event in later if event["type"] == "delivery_arrived" and not event["authorized"])
    assert unauthorized["delivery_id"] == "delivery.unknown"
    assert backend.execute_atomic({"kind": "act", "commands": [open_gate]}).error_code == "IDENTITY_NOT_AUTHORIZED"


def test_keyless_entry_has_positive_and_negative_credentials_and_relock_receipt():
    backend, _ = _backend("keyless_resident_entry")
    _, events = _until(backend, "resident_arrived")
    assert any(event["type"] == "resident_arrived" and event["authorized"] for event in events)
    unlock = _command("front_door_lock.main", "lock.control", "unlock")
    assert backend.execute_atomic({"kind": "act", "commands": [unlock]}).accepted
    step, relock_events = _tick(backend, 5)
    assert step.public_observation["devices"]["front_door_lock.main"]["state"] == "locked"
    assert any(event["type"] == "front_door_locked" and event["automatic"] for event in relock_events)
    _, negative = _until(backend, "unknown_credential_arrived")
    assert any(event["type"] == "unknown_credential_arrived" for event in negative)
    assert backend.execute_atomic({"kind": "act", "commands": [unlock]}).error_code == "IDENTITY_NOT_AUTHORIZED"


def test_supply_order_has_authorization_consumption_and_nonzero_delivery_lead_time():
    backend, initial = _backend("pellet_supply_guard", horizon=86400)
    before = initial.public_observation["devices"]["supply.pellets"]["attributes"]["level"]
    bad = _command("supply.pellets", "supply.order", "place", {"item": "pellets", "supplier": "supplier.unknown", "quantity": 50, "max_cost": 40})
    assert backend.execute_atomic({"kind": "act", "commands": [bad]}).error_code == "PURCHASE_NOT_AUTHORIZED"
    order = _command("supply.pellets", "supply.order", "place", {"item": "pellets", "supplier": "supplier.home_heat", "quantity": 50, "max_cost": 40})
    assert backend.execute_atomic({"kind": "act", "commands": [order]}).accepted
    assert backend._devices["supply.pellets"]["level"] == before
    step, events = _tick(backend, 14)
    assert any(event["type"] == "supply_order_accepted" for event in events)
    assert not any(event["type"] == "supply_delivered" for event in events)
    step, delivered = _tick(backend)
    assert any(event["type"] == "supply_delivered" for event in delivered)
    assert step.public_observation["workflow"]["orders"]["pellets"]["status"] == "delivered"
    assert step.public_observation["devices"]["supply.pellets"]["attributes"]["level"] > before


def test_shower_session_and_dated_morning_news_have_release_events():
    backend, _ = _backend("shower_news_delivery", horizon=43200)
    step, events = _until(backend, "shower_started")
    assert any(event["type"] == "shower_started" for event in events)
    assert step.public_observation["time"].endswith("Z")
    play = _command("media_player.main", "media.control", "play", {"content": "news"})
    assert backend.execute_atomic({"kind": "act", "commands": [play]}).accepted
    _, receipt = _tick(backend)
    media = next(event for event in receipt if event["type"] == "media_started")
    assert media["content_date"] == "2026-01-02"
    _, ended = _tick(backend, 9)
    assert any(event["type"] == "shower_ended" for event in ended)
    stop = _command("media_player.main", "media.control", "stop")
    assert backend.execute_atomic({"kind": "act", "commands": [stop]}).accepted


def test_local_curfew_wake_coffee_and_emergency_negative_trials_are_real():
    tv, _ = _backend("television_curfew", horizon=43200)
    _, events = _tick(tv, 330)
    start = next(event for event in events if event["type"] == "television_curfew_started")
    assert start["step"] == 330
    _, through_end = _tick(tv, 390)
    assert any(event["type"] == "television_curfew_ended" for event in through_end)

    coffee, initial = _backend("coffee_ready_at_wake")
    assert initial.public_observation["public_context"]["intended_wake_at"] == "2026-01-01T18:15:00Z"
    brew = _command("coffee_maker.main", "coffee.control", "start")
    assert coffee.execute_atomic({"kind": "act", "commands": [brew]}).accepted
    _, coffee_events = _tick(coffee, 15)
    assert any(event["type"] == "coffee_ready" for event in coffee_events)
    assert any(event["type"] == "intended_wake_time" for event in coffee_events)

    emergency, _ = _backend("supported_emergency_call")
    _, detected = _until(emergency, "emergency_detected")
    assert any(event["type"] == "emergency_detected" and event["supported"] for event in detected)
    call = _command("emergency_call.service", "emergency.call", "call", {"service": "emergency"})
    assert emergency.execute_atomic({"kind": "act", "commands": [call]}).accepted
    _, accepted = _tick(emergency)
    assert any(event["type"] == "emergency_call_accepted" for event in accepted)
    _, unsupported = _until(emergency, "unsupported_alarm")
    assert any(event["type"] == "unsupported_alarm" for event in unsupported)
    assert emergency.execute_atomic({"kind": "act", "commands": [call]}).error_code == "UNAUTHORIZED_EMERGENCY_TYPE"


def test_hazard_transition_close_receipt_and_occupancy_security_release_are_observable():
    fridge, _ = _backend("fridge_door_left_open")
    _until(fridge, "fridge_door_opened")
    _tick(fridge, 2)
    close = _command("fridge_door.main", "fridge.door", "close")
    assert fridge.execute_atomic({"kind": "act", "commands": [close]}).accepted
    _, fridge_events = _tick(fridge)
    assert any(event["type"] == "fridge_door_closed" for event in fridge_events)
    assert not any(event["type"] == "fridge_door_alarm" for event in fridge_events)

    stove, initial = _backend("unattended_stove_guard")
    assert initial.public_observation["semantic_observations"]["stove_attendance"] == "attended"
    step, stove_events = _until(stove, "stove_became_unattended")
    assert step.public_observation["semantic_observations"]["stove_attendance"] == "unattended"
    assert any(event["type"] == "stove_became_unattended" for event in stove_events)
    extinguish = _command("stove.main", "stove.control", "extinguish")
    assert stove.execute_atomic({"kind": "act", "commands": [extinguish]}).accepted
    _, receipt = _tick(stove)
    assert any(event["type"] == "stove_extinguished" for event in receipt)

    security, _ = _backend("unoccupied_home_security")
    step, away = _until(security, "occupants_departed")
    assert any(event["type"] == "occupancy_became_zero" for event in away)
    assert step.public_observation["semantic_observations"]["household_occupancy"]["count"] == 0
    lock = _command("front_door_lock.main", "lock.control", "lock")
    assert security.execute_atomic({"kind": "act", "commands": [lock]}).accepted
    _, guard_events = _until(security, "unauthorized_access_attempt")
    trial = next(event for event in guard_events if event["type"] == "unauthorized_access_attempt")
    assert trial["blocked"] is True
    step, returned = _until(security, "occupants_returned")
    assert any(event["type"] == "authorized_return" for event in returned)
    assert step.public_observation["semantic_observations"]["household_occupancy"]["status"] == "home"


def test_alarm_timeout_departure_release_and_vehicle_negative_trial_are_explicit():
    alarm, _ = _backend("intended_wake_alarm")
    _, wake = _tick(alarm, 15)
    assert any(event["type"] == "intended_wake_time" for event in wake)
    ring = _command("alarm_clock.main", "alarm.control", "ring")
    assert alarm.execute_atomic({"kind": "act", "commands": [ring]}).accepted
    _, timeout = _tick(alarm, 5)
    assert any(event["type"] == "alarm_timeout" for event in timeout)

    departure, _ = _backend("departure_key_reminder", seed=13)
    _, lifecycle = _until(departure, "departure_cancelled")
    assert {event["type"] for event in lifecycle} >= {"departure_started", "keys_present", "departure_cancelled"}

    vehicle, _ = _backend("authorized_vehicle_gate_entry")
    _, arrival = _until(vehicle, "vehicle_arrived")
    assert any(event["type"] == "vehicle_arrived" and event["authorized"] for event in arrival)
    open_gate = _command("garage_door.main", "garage.door", "open")
    assert vehicle.execute_atomic({"kind": "act", "commands": [open_gate]}).accepted
    _tick(vehicle)
    close_gate = _command("garage_door.main", "garage.door", "close")
    assert vehicle.execute_atomic({"kind": "act", "commands": [close_gate]}).accepted
    _tick(vehicle)
    _, negative = _until(vehicle, "unauthorized_vehicle_arrived")
    assert any(event["type"] == "unauthorized_vehicle_arrived" for event in negative)
    assert vehicle.execute_atomic({"kind": "act", "commands": [open_gate]}).error_code == "IDENTITY_NOT_AUTHORIZED"


def test_until_event_ignores_unrelated_events_without_hiding_them():
    backend, _ = _backend("mail_arrival_notification", horizon=3600)
    step = backend.advance({
        "kind": "wait",
        "mode": "until_event",
        "event_filter": {"type": "event.that.never.occurs"},
        "timeout_seconds": 3600,
    })
    assert step.public_observation["step"] == 60
    assert any(event["type"] == "mail_delivered" for event in step.public_observation["events"])


def test_until_event_stops_exactly_on_matching_event_and_keeps_prior_receipts():
    backend, _ = _backend("away_intercom_notification", horizon=3600)
    step = backend.advance({
        "kind": "wait",
        "mode": "until_event",
        "event_filter": {"type": "visitor_rang"},
        "timeout_seconds": 3600,
    })
    visitor = next(event for event in step.public_observation["events"] if event["type"] == "visitor_rang")
    assert step.public_observation["step"] == visitor["step"]
    types = [event["type"] for event in step.public_observation["events"]]
    assert "away_started" in types
    assert "visitor_rang" in types
