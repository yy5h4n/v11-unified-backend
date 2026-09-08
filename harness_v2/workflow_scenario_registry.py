"""Fail-closed semantic contracts for the household-workflow T2 backend.

This registry describes what a backend *must* expose to support each scenario.
It intentionally contains requirements which the current backend does not yet
meet.  ``match_backend`` reports those gaps as PARTIAL instead of weakening a
responsibility to fit the implementation.
"""

from __future__ import annotations

from typing import Any, Mapping

from .workflow_evaluator import evaluator_primitive_evidence


COMMON_OBSERVATIONS = (
    "time",
    "complete_device_inventory",
    "workflow_state",
    "public_exogenous_events",
    "action_receipts",
)
COMMON_DYNAMICS = ("task_state_machine", "seed_pinned_exogenous_event_process")
COMMON_EVALUATOR = (
    "state_predicate",
    "event_order",
    "deadline_check",
    "forbidden_action_check",
    "release_check",
)


def _event(name: str, **equals: Any) -> dict[str, Any]:
    return {"kind": "event", "type": name, "equals": equals}


def _state(path: str, op: str, value: Any) -> dict[str, Any]:
    return {"kind": "state", "path": path, "op": op, "value": value}


def _spec(
    responsibility_id: str,
    scenario_type: str,
    *,
    activation: Mapping[str, Any],
    window: Mapping[str, Any],
    outcomes: tuple[Mapping[str, Any], ...],
    forbidden: tuple[Mapping[str, Any], ...],
    release: Mapping[str, Any],
    authorization: Mapping[str, Any],
    invariants: Mapping[str, Any],
    actions: tuple[str, ...],
    dynamics: tuple[str, ...] = (),
    events: tuple[str, ...] = (),
    evaluator: tuple[str, ...] = (),
    observations: tuple[str, ...] = (),
    horizon_seconds: int = 3600,
    profile: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "responsibility_id": responsibility_id,
        "scenario_type": scenario_type,
        "activation": dict(activation),
        "window": dict(window),
        "required_outcomes": [dict(item) for item in outcomes],
        "forbidden": [dict(item) for item in forbidden],
        "release": dict(release),
        "authorization": dict(authorization),
        "query_invariants": dict(invariants),
        "required_observations": list(dict.fromkeys(COMMON_OBSERVATIONS + observations)),
        "required_actions": list(actions),
        "required_dynamics": list(dict.fromkeys(COMMON_DYNAMICS + dynamics)),
        "required_events": list(events),
        "required_evaluator_primitives": list(dict.fromkeys(COMMON_EVALUATOR + evaluator)),
        "minimum_horizon_seconds": horizon_seconds,
        "profile_requirements": list(profile),
    }


WORKFLOW_SCENARIO_REGISTRY: dict[str, dict[str, Any]] = {
    "laundry_completion_notification": _spec(
        "rd_05920f8dade6", "laundry_completion_notification",
        activation={"all": [_state("devices.laundry.washer.state", "eq", "washing")]},
        window={"start": "laundry_cycle_finished", "deadline_profile": "notification_sla_seconds"},
        outcomes=(_event("notification_sent", purpose="laundry_finished"),),
        forbidden=({"kind": "action", "operation": "laundry.control.start", "unless_profile": "washer_start_authorized"}, {"kind": "notification_before", "event": "laundry_cycle_finished"}),
        release=_event("notification_sent", purpose="laundry_finished"),
        authorization={"actions": ["notification.send"], "recipient_profile": "notification_recipients"},
        invariants={"object": "laundry", "trigger": "cycle_finished", "outcome": "notify", "beneficiary": "household"},
        actions=("notification.send",), events=("laundry_cycle_finished", "notification_sent"),
        profile=("notification_sla_seconds", "notification_recipients"),
    ),
    "laundry_backlog_management": _spec(
        "rd_split_94be9427a8c4", "laundry_backlog_management",
        activation={"any": [_state("devices.laundry.washer.state", "eq", "loaded"), _state("laundry.backlog_count", "gt", 0)]},
        window={"deadline_profile": "laundry_due_at"},
        outcomes=(_state("devices.laundry.washer.state", "eq", "idle"), _state("laundry.backlog_count", "eq", 0)),
        forbidden=({"kind": "action", "operation": "laundry.control.start", "when": "washer_empty"},),
        release={"kind": "predicate", "all": ["cycle_finished", "washer_idle", "backlog_zero"]},
        authorization={"actions": ["laundry.control.start", "laundry.control.unload"]},
        invariants={"object": "household_laundry", "outcome": "backlog_cleared"},
        actions=("laundry.control",), dynamics=("laundry_cycle",), events=("laundry_cycle_finished",), profile=("laundry_due_at",),
    ),
    "weekly_floor_cleaning": _spec(
        "rd_9fa396d3191a", "weekly_floor_cleaning",
        activation={"all": [_event("weekly_cleaning_due"), _state("floor.cleaning_occurrence.status", "eq", "due")]},
        window={"calendar_period": "week", "deadline_profile": "weekly_cleaning_due_at", "timezone_profile": "local_timezone"},
        outcomes=(_event("vacuum_docked", occurrence="current_week"),),
        forbidden=({"kind": "duplicate_completion", "key": "weekly_occurrence_id"}, {"kind": "action_outside_window", "operation": "vacuum.control.start_cleaning"}),
        release={"kind": "occurrence_completed", "key": "weekly_occurrence_id"},
        authorization={"actions": ["vacuum.control.start_cleaning"], "scope_profile": "cleaning_zone_scope"},
        invariants={"cadence": "weekly", "object": "floors", "outcome": "cleaned"},
        actions=("vacuum.control",), dynamics=("vacuum_cycle", "weekly_calendar_recurrence"), events=("weekly_cleaning_due", "vacuum_docked"),
        evaluator=("recurrence_occurrence_check",), horizon_seconds=604800, profile=("weekly_cleaning_due_at", "local_timezone", "cleaning_zone_scope"),
    ),
    "away_intercom_notification": _spec(
        "rd_4c9f419a786a", "away_intercom_notification",
        activation={"all": [_state("occupancy.household", "eq", "away"), _event("visitor_rang")]},
        window={"guard_start": "away_started", "guard_end": "occupants_returned", "deadline_profile": "notification_sla_seconds"},
        outcomes=(_event("notification_sent", purpose="away_intercom_visitor"),),
        forbidden=({"kind": "notification_without", "event": "visitor_rang"}, {"kind": "action_outside_guard"}),
        release=_event("occupants_returned"), authorization={"actions": ["notification.send"], "recipient_profile": "notification_recipients"},
        invariants={"condition": "away", "trigger": "intercom_visitor", "outcome": "inform_resident"},
        actions=("notification.send",), dynamics=("occupancy_lifecycle",), events=("away_started", "visitor_rang", "occupants_returned", "notification_sent"),
        observations=("household_occupancy",), profile=("notification_sla_seconds", "notification_recipients"),
    ),
    "mail_arrival_notification": _spec(
        "rd_6010f46a03e0", "mail_arrival_notification",
        activation={"all": [_event("mail_delivered")]}, window={"start": "mail_delivered", "deadline_profile": "notification_sla_seconds"},
        outcomes=(_event("notification_sent", purpose="mail_arrival"),), forbidden=({"kind": "notification_before", "event": "mail_delivered"},),
        release=_event("notification_sent", purpose="mail_arrival"), authorization={"actions": ["notification.send"], "recipient_profile": "notification_recipients"},
        invariants={"trigger": "mail_arrival", "beneficiary": "occupants", "outcome": "notify"}, actions=("notification.send",),
        events=("mail_delivered", "notification_sent"), profile=("notification_sla_seconds", "notification_recipients"),
    ),
    "away_visitor_monitoring": _spec(
        "rd_782fea3cbba2", "away_visitor_monitoring",
        activation={"all": [_state("occupancy.household", "eq", "away"), _event("visitor_rang")]},
        window={"guard_start": "away_started", "guard_end": "occupants_returned", "deadline_profile": "notification_sla_seconds"},
        outcomes=(_event("visitor_recorded"), _event("notification_sent", purpose="away_door_visitor")),
        forbidden=({"kind": "unmatched_visitor_event"}, {"kind": "action_outside_guard"}), release=_event("occupants_returned"),
        authorization={"actions": ["notification.send"], "recipient_profile": "notification_recipients"},
        invariants={"condition": "family_away", "object": "door_visitors", "outcome": "monitor_and_update"},
        actions=("notification.send",), dynamics=("occupancy_lifecycle", "visitor_event_log"), events=("away_started", "visitor_rang", "visitor_recorded", "occupants_returned"),
        observations=("household_occupancy", "visitor_event_log"), evaluator=("per_trigger_coverage",), profile=("notification_sla_seconds", "notification_recipients"),
    ),
    "unoccupied_visitor_acknowledgement": _spec(
        "rd_fd30c82cb52f", "unoccupied_visitor_acknowledgement",
        activation={"all": [_state("occupancy.household", "eq", "away"), _event("visitor_rang")]},
        window={"guard_start": "away_started", "guard_end": "occupants_returned", "deadline_profile": "intercom_response_sla_seconds"},
        outcomes=(_event("visitor_answered"),), forbidden=({"kind": "action_without", "event": "visitor_rang"}, {"kind": "action", "operation": "garage.door.open"}),
        release={"any": [_event("visitor_interaction_ended"), _event("occupants_returned")]}, authorization={"actions": ["intercom.control.answer"], "gate_access": False},
        invariants={"condition": "nobody_home", "trigger": "visitor", "outcome": "acknowledge"}, actions=("intercom.control",),
        dynamics=("occupancy_lifecycle", "intercom_session"), events=("away_started", "visitor_rang", "visitor_answered", "visitor_interaction_ended", "occupants_returned"),
        observations=("household_occupancy",), profile=("intercom_response_sla_seconds",),
    ),
    "fridge_door_left_open": _spec(
        "rd_099294a66240", "fridge_door_left_open",
        activation={"all": [_state("devices.fridge_door.main.state", "eq", "open"), {"kind": "duration", "profile": "fridge_intervention_after_seconds"}]},
        window={"deadline_profile": "fridge_alarm_seconds"}, outcomes=(_state("devices.fridge_door.main.state", "eq", "closed"),),
        forbidden=(_event("fridge_door_alarm"), {"kind": "action", "operation": "fridge.door.open"}), release=_event("fridge_door_closed"),
        authorization={"actions": ["fridge.door.close"]}, invariants={"object": "fridge_door", "condition": "left_open", "outcome": "prevent_hazard"},
        actions=("fridge.door",), dynamics=("fridge_open_duration",), events=("fridge_door_closed", "fridge_door_alarm"),
        profile=("fridge_intervention_after_seconds", "fridge_alarm_seconds"),
    ),
    "unattended_stove_guard": _spec(
        "rd_1783c33e2747", "unattended_stove_guard",
        activation={"all": [_state("devices.stove.main.state", "eq", "on"), _state("stove.attendance", "eq", "unattended")]},
        window={"guard_start": "stove_became_unattended", "deadline_profile": "stove_safety_sla_seconds"}, outcomes=(_state("devices.stove.main.state", "eq", "off"),),
        forbidden=(_event("stove_hazard_shutoff"), {"kind": "action_when", "condition": "attended", "operation": "stove.control.extinguish"}),
        release={"any": [_event("stove_extinguished"), _event("stove_attendance_restored")]}, authorization={"actions": ["stove.control.extinguish"]},
        invariants={"object": "lit_stove", "condition": "unattended", "outcome": "promptly_off"}, actions=("stove.control",),
        dynamics=("stove_attendance_lifecycle",), events=("stove_became_unattended", "stove_extinguished", "stove_attendance_restored", "stove_hazard_shutoff"),
        observations=("stove_attendance",), profile=("stove_safety_sla_seconds",),
    ),
    "departure_lockdown": _spec(
        "rd_49465c88e9af", "departure_lockdown",
        activation={"all": [_event("occupants_departed"), _state("occupancy.count", "eq", 0)]},
        window={"guard_start": "occupants_departed", "guard_end": "authorized_return", "deadline_profile": "lockdown_sla_seconds"},
        outcomes=({"kind": "all_scopes_secure", "scope_profile": "security_scope"},), forbidden=({"kind": "scope_unsecured_during_guard"}, {"kind": "lock_before_last_departure"}),
        release=_event("authorized_return"), authorization={"actions": ["lock.control.lock", "garage.door.close"], "scope_profile": "security_scope"},
        invariants={"trigger": "departure", "object": "home", "outcome": "secure"}, actions=("lock.control", "garage.door"),
        dynamics=("occupancy_lifecycle",), events=("occupants_departed", "authorized_return"), observations=("household_occupancy",),
        evaluator=("continuous_guard_check",), profile=("lockdown_sla_seconds", "security_scope"),
    ),
    "unoccupied_home_security": _spec(
        "rd_5dd0c995ea28", "unoccupied_home_security",
        activation={"all": [_state("occupancy.count", "eq", 0)]}, window={"guard_start": "occupancy_became_zero", "guard_end": "authorized_return"},
        outcomes=({"kind": "all_scopes_secure", "scope_profile": "security_scope"},), forbidden=({"kind": "scope_unsecured_during_guard"}, {"kind": "unauthorized_access"}),
        release=_event("authorized_return"), authorization={"actions": ["lock.control.lock", "garage.door.close"], "scope_profile": "security_scope"},
        invariants={"condition": "unoccupied", "object": "home", "outcome": "maintain_security"}, actions=("lock.control", "garage.door"),
        dynamics=("occupancy_lifecycle",), events=("occupancy_became_zero", "authorized_return"), observations=("household_occupancy",),
        evaluator=("continuous_guard_check", "unauthorized_access_negative_check"), profile=("security_scope",),
    ),
    "failed_entry_notification": _spec(
        "rd_89b104a21351", "failed_entry_notification",
        activation={"all": [_event("failed_entry_attempt")]}, window={"start": "failed_entry_attempt", "deadline_profile": "notification_sla_seconds"},
        outcomes=(_event("notification_sent", purpose="failed_entry"),), forbidden=({"kind": "notification_without", "event": "failed_entry_attempt"},),
        release=_event("notification_sent", purpose="failed_entry"), authorization={"actions": ["notification.send"], "recipient_profile": "notification_recipients"},
        invariants={"trigger": "failed_entry", "beneficiary": "occupants", "outcome": "notify"}, actions=("notification.send",),
        events=("failed_entry_attempt", "notification_sent"), profile=("notification_sla_seconds", "notification_recipients"),
    ),
    "expected_delivery_gate_and_notice": _spec(
        "rd_8b7dca2e69c7", "expected_delivery_gate_and_notice",
        activation={"all": [_event("delivery_arrived"), {"kind": "allowlist_membership", "profile": "expected_delivery_allowlist"}]},
        window={"profile": "delivery_access_window", "deadline_profile": "gate_access_sla_seconds"},
        outcomes=(_event("garage_door_opened"), _event("notification_sent", purpose="expected_delivery"), _event("garage_door_closed")),
        forbidden=({"kind": "access_for_nonmember", "profile": "expected_delivery_allowlist"}, {"kind": "gate_left_open"}), release=_event("garage_door_closed"),
        authorization={"actions": ["garage.door.open", "garage.door.close", "notification.send"], "allowlist_profile": "expected_delivery_allowlist"},
        invariants={"subject": "expected_delivery", "outcomes": ["gate_access", "notice"]}, actions=("garage.door", "notification.send"),
        dynamics=("delivery_identity_lifecycle",), events=("delivery_arrived", "garage_door_opened", "garage_door_closed", "notification_sent"),
        observations=("delivery_identity",), evaluator=("allowlist_negative_check",), profile=("expected_delivery_allowlist", "delivery_access_window", "gate_access_sla_seconds"),
    ),
    "remote_visitor_gate_access": _spec(
        "rd_af2688f9787c", "remote_visitor_gate_access",
        activation={"all": [_state("occupancy.household", "eq", "away"), _event("visitor_rang"), {"kind": "visitor_authorized"}]},
        window={"guard_start": "away_started", "guard_end": "occupants_returned", "deadline_profile": "gate_access_sla_seconds"},
        outcomes=(_event("visitor_answered"), _event("garage_door_opened"), _event("garage_door_closed")),
        forbidden=({"kind": "gate_open_for_unauthorized_visitor"}, {"kind": "gate_left_open"}), release={"any": [_event("garage_door_closed"), _event("occupants_returned")]},
        authorization={"actions": ["intercom.control.answer", "garage.door.open", "garage.door.close"], "allowlist_profile": "visitor_allowlist"},
        invariants={"condition": "away", "subject": "authorized_visitor", "outcome": "temporary_gate_access"}, actions=("intercom.control", "garage.door"),
        dynamics=("occupancy_lifecycle", "visitor_identity_lifecycle"), events=("away_started", "visitor_rang", "visitor_answered", "garage_door_opened", "garage_door_closed", "occupants_returned"),
        observations=("household_occupancy", "visitor_identity"), evaluator=("allowlist_negative_check",), profile=("visitor_allowlist", "gate_access_sla_seconds"),
    ),
    "post_parking_garage_closure": _spec(
        "rd_split_272d269ff0c7", "post_parking_garage_closure",
        activation={"all": [_event("vehicle_parked")]}, window={"start": "vehicle_parked", "deadline_profile": "garage_close_sla_seconds"},
        outcomes=(_state("devices.garage_door.main.state", "eq", "closed"),), forbidden=({"kind": "gate_left_open"}, {"kind": "close_before", "event": "vehicle_parked"}),
        release=_event("garage_door_closed"), authorization={"actions": ["garage.door.close"]}, invariants={"trigger": "after_parking", "object": "garage", "outcome": "closed"},
        actions=("garage.door",), dynamics=("garage_obstruction_recovery",), events=("vehicle_parked", "garage_door_closed", "garage_door_obstruction"),
        evaluator=("obstruction_recovery_check",), profile=("garage_close_sla_seconds",),
    ),
    "bathroom_occupancy_indicator": _spec(
        "rd_split_a525cb032b19", "bathroom_occupancy_indicator",
        activation={"any": [_event("bathroom_occupied"), _event("bathroom_vacated")]}, window={"deadline_profile": "indicator_update_sla_seconds"},
        outcomes=({"kind": "indicator_matches", "state_path": "bathroom.occupancy"},), forbidden=({"kind": "stale_or_inverted_indicator"}, {"kind": "privacy_disclosure_beyond_occupancy_bit"}),
        release=_event("bathroom_vacated"), authorization={"actions": ["notification.send"], "data_scope": ["occupied_bit"]},
        invariants={"object": "bathroom", "state": "occupancy", "outcome": "current_indicator"}, actions=("notification.send",),
        dynamics=("bathroom_occupancy_lifecycle",), events=("bathroom_occupied", "bathroom_vacated", "notification_sent"), observations=("bathroom_occupancy",),
        evaluator=("transition_coverage",), profile=("indicator_update_sla_seconds",),
    ),
    "television_curfew": _spec(
        "rd_05eb866a5fcf", "television_curfew",
        activation={"all": [{"kind": "local_time", "equals": "23:30", "timezone_profile": "local_timezone"}]},
        window={"guard_start_local": "23:30", "guard_end_profile": "television_curfew_end", "timezone_profile": "local_timezone"},
        outcomes=(_state("devices.media_player.main.television_playing", "eq", False),),
        forbidden=({"kind": "media_content_during_guard", "content": "television"}, {"kind": "early_stop_before", "local_time": "23:30"}),
        release={"kind": "local_time", "profile": "television_curfew_end"}, authorization={"actions": ["media.control.stop"], "content": "television"},
        invariants={"object": "television", "curfew_local_time": "23:30", "lifecycle": "guard"}, actions=("media.control",),
        dynamics=("local_calendar_clock", "curfew_guard"), events=("television_curfew_started", "television_curfew_ended", "media_stopped"),
        observations=("local_time",), evaluator=("continuous_guard_check",), horizon_seconds=43200, profile=("local_timezone", "television_curfew_end"),
    ),
    "keyless_resident_entry": _spec(
        "rd_06f5ac8a120d", "keyless_resident_entry",
        activation={"all": [_event("resident_arrived"), {"kind": "credential_allowlist_membership", "profile": "resident_credential_allowlist"}]},
        window={"deadline_profile": "entry_sla_seconds", "relock_profile": "entry_relock_seconds"}, outcomes=(_event("front_door_unlocked"), _event("front_door_locked")),
        forbidden=({"kind": "unlock_for_unknown_credential"}, {"kind": "unlock_exceeds", "profile": "entry_relock_seconds"}), release=_event("front_door_locked"),
        authorization={"actions": ["lock.control.unlock", "lock.control.lock"], "allowlist_profile": "resident_credential_allowlist"},
        invariants={"subject": "verified_resident", "trigger": "arrival", "outcome": "keyless_entry"}, actions=("lock.control",),
        dynamics=("credential_verification", "entry_session"), events=("resident_arrived", "front_door_unlocked", "front_door_locked", "unknown_credential_arrived"),
        observations=("presented_credential",), evaluator=("allowlist_negative_check",), profile=("resident_credential_allowlist", "entry_sla_seconds", "entry_relock_seconds"),
    ),
    "full_bin_collection_notice": _spec(
        "rd_5e0e9919d03e", "full_bin_collection_notice",
        activation={"all": [_state("devices.trash_bin.main.state", "eq", "full"), _event("bin_collection_due")]},
        window={"deadline_profile": "notification_sla_seconds"}, outcomes=(_event("notification_sent", purpose="full_bin_collection"),),
        forbidden=({"kind": "notification_unless_all", "conditions": ["bin_full", "collection_due"]},), release=_event("notification_sent", purpose="full_bin_collection"),
        authorization={"actions": ["notification.send"], "recipient_profile": "notification_recipients"},
        invariants={"object": "full_bin", "condition": "collection_due", "outcome": "notify"}, actions=("notification.send",),
        events=("bin_collection_due", "notification_sent"), profile=("notification_sla_seconds", "notification_recipients"),
    ),
    "departure_key_reminder": _spec(
        "rd_7f75346b5a7f", "departure_key_reminder",
        activation={"all": [_event("departure_started"), _state("departure.keys_present", "eq", False)]},
        window={"deadline_event": "departure_completed"}, outcomes=(_event("notification_sent", purpose="keys_missing"),),
        forbidden=({"kind": "notification_when", "condition": "keys_present"}, {"kind": "notification_after", "event": "departure_completed"}),
        release={"any": [_event("keys_present"), _event("departure_cancelled"), _event("departure_completed")]},
        authorization={"actions": ["notification.send"], "recipient": "departing_resident"}, invariants={"trigger": "departure", "condition": "keys_missing", "outcome": "remind"},
        actions=("notification.send",), dynamics=("departure_lifecycle",), events=("departure_started", "keys_present", "departure_cancelled", "departure_completed", "notification_sent"),
        observations=("keys_present",),
    ),
    "intended_wake_alarm": _spec(
        "rd_ebe806fd446f", "intended_wake_alarm",
        activation={"all": [{"kind": "timestamp", "profile": "intended_wake_at"}]},
        window={"center_profile": "intended_wake_at", "tolerance_profile": "alarm_tolerance_seconds"}, outcomes=(_event("alarm_sounded"),),
        forbidden=({"kind": "alarm_outside_window"}, {"kind": "duplicate_alarm"}), release={"any": [_event("alarm_acknowledged"), _event("alarm_timeout")]},
        authorization={"actions": ["alarm.control.ring"]}, invariants={"time_slot": "intended_wake_at", "outcome": "wake_alarm"}, actions=("alarm.control",),
        dynamics=("scheduled_alarm_clock",), events=("alarm_sounded", "alarm_acknowledged", "alarm_timeout"), observations=("local_time",),
        evaluator=("time_tolerance_check",), profile=("intended_wake_at", "alarm_tolerance_seconds", "local_timezone"),
    ),
    "shower_music_availability": _spec(
        "rd_5d75d19a4827", "shower_music_availability",
        activation={"all": [_event("shower_started"), _state("profile.shower_music_opt_in", "eq", True)]},
        window={"guard_start": "shower_started", "guard_end": "shower_ended"}, outcomes=(_event("media_started", content="music"),),
        forbidden=({"kind": "media_outside_guard", "content": "music"}, {"kind": "wrong_media_content"}), release={"all": [_event("shower_ended"), _event("media_stopped")]},
        authorization={"actions": ["media.control.play", "media.control.stop"], "content_profile": "shower_music_content"},
        invariants={"context": "shower", "content": "music", "mode": "available_or_opted_in"}, actions=("media.control",),
        dynamics=("shower_session",), events=("shower_started", "shower_ended", "media_started", "media_stopped"),
        evaluator=("continuous_guard_check",), profile=("shower_music_opt_in", "shower_music_content"),
    ),
    "pellet_supply_guard": _spec(
        "rd_bd6dfd6a2c77", "pellet_supply_guard",
        activation={"all": [_state("inventory.pellets.level", "lte_profile", "pellet_reorder_point")]},
        window={"deadline": "before_projected_stockout", "lead_time_profile": "pellet_delivery_lead_seconds"},
        outcomes=(_event("supply_order_accepted", item="pellets"), _event("supply_delivered", item="pellets")),
        forbidden=({"kind": "inventory_stockout", "item": "pellets"}, {"kind": "instant_inventory_mutation"}, {"kind": "purchase_outside_authorization"}),
        release=_event("supply_delivered", item="pellets"), authorization={"action": "supply.order", "budget_profile": "pellet_budget", "supplier_profile": "pellet_suppliers"},
        invariants={"item": "pellets", "outcome": "replenish_before_empty"}, actions=("supply.order",),
        dynamics=("inventory_consumption", "procurement_order", "delivery_lead_time"), events=("supply_order_accepted", "supply_delivered", "inventory_stockout"),
        observations=("inventory_projection", "order_state"), evaluator=("stockout_check", "purchase_authorization_check"),
        horizon_seconds=86400, profile=("pellet_reorder_point", "pellet_delivery_lead_seconds", "pellet_budget", "pellet_suppliers", "pellet_order_quantity"),
    ),
    "beer_dispenser_stock": _spec(
        "rd_a07aecceb886", "beer_dispenser_stock",
        activation={"all": [_state("inventory.beer.level", "lte_profile", "beer_reorder_point")]},
        window={"deadline": "before_projected_stockout", "lead_time_profile": "beer_delivery_lead_seconds"},
        outcomes=(_event("supply_order_accepted", item="beer"), _event("supply_delivered", item="beer")),
        forbidden=({"kind": "inventory_stockout", "item": "beer"}, {"kind": "instant_inventory_mutation"}, {"kind": "purchase_outside_authorization"}),
        release=_event("supply_delivered", item="beer"), authorization={"action": "supply.order", "budget_profile": "beer_budget", "supplier_profile": "beer_suppliers"},
        invariants={"item": "beer", "object": "dispenser", "outcome": "keep_stocked"}, actions=("supply.order",),
        dynamics=("inventory_consumption", "procurement_order", "delivery_lead_time"), events=("supply_order_accepted", "supply_delivered", "inventory_stockout"),
        observations=("inventory_projection", "order_state"), evaluator=("stockout_check", "purchase_authorization_check"),
        horizon_seconds=86400, profile=("beer_reorder_point", "beer_delivery_lead_seconds", "beer_budget", "beer_suppliers", "beer_order_quantity"),
    ),
    "coffee_ready_at_wake": _spec(
        "rd_f540da710bc1", "coffee_ready_at_wake",
        activation={"all": [{"kind": "schedule_known", "profile": "intended_wake_at"}, _state("devices.coffee_maker.main.state", "eq", "idle")]},
        window={"center_profile": "intended_wake_at", "early_profile": "coffee_freshness_seconds", "late_profile": "coffee_lateness_tolerance_seconds"},
        outcomes=(_event("coffee_ready"), {"kind": "ready_at_wake_window"}),
        forbidden=({"kind": "coffee_ready_too_early"}, {"kind": "coffee_ready_after_tolerance"}, {"kind": "duplicate_brew"}),
        release={"any": [_event("coffee_served"), _event("coffee_expired")]}, authorization={"actions": ["coffee.control.start", "coffee.control.serve"]},
        invariants={"object": "coffee", "deadline": "wake_time", "quality": "ready_and_fresh"}, actions=("coffee.control",),
        dynamics=("coffee_brew_cycle", "coffee_freshness_decay", "scheduled_readiness"), events=("coffee_ready", "coffee_served", "coffee_expired"),
        observations=("local_time", "coffee_freshness"), evaluator=("readiness_window_check",),
        profile=("intended_wake_at", "coffee_brew_seconds", "coffee_freshness_seconds", "coffee_lateness_tolerance_seconds", "local_timezone"),
    ),
    "post_use_toilet_flush": _spec(
        "rd_f61b22e412c4", "post_use_toilet_flush",
        activation={"all": [_event("toilet_use_finished")]}, window={"start": "toilet_use_finished", "deadline_profile": "flush_sla_seconds"},
        outcomes=(_state("devices.toilet.main.state", "eq", "flushed"),), forbidden=({"kind": "flush_during_use"}, {"kind": "unflushed_after_deadline"}),
        release=_event("toilet_flushed"), authorization={"actions": ["toilet.control.flush"]}, invariants={"trigger": "after_use", "object": "toilet", "outcome": "flushed"},
        actions=("toilet.control",), events=("toilet_use_finished", "toilet_flushed"), profile=("flush_sla_seconds",),
    ),
    "toilet_paper_depletion_notice": _spec(
        "rd_fcd3c198a00d", "toilet_paper_depletion_notice",
        activation={"all": [_event("toilet_paper_depleted")]}, window={"start": "toilet_paper_depleted", "deadline_profile": "notification_sla_seconds"},
        outcomes=(_event("notification_sent", purpose="toilet_paper_depleted"),), forbidden=({"kind": "notification_before", "event": "toilet_paper_depleted"}, {"kind": "action", "operation": "supply.control.restock"}),
        release=_event("notification_sent", purpose="toilet_paper_depleted"), authorization={"actions": ["notification.send"], "recipient_profile": "notification_recipients"},
        invariants={"item": "toilet_paper", "condition": "depleted", "outcome": "notify_not_restock"}, actions=("notification.send",),
        events=("toilet_paper_depleted", "notification_sent"), profile=("notification_sla_seconds", "notification_recipients"),
    ),
    "shower_news_delivery": _spec(
        "rd_split_137db9864548", "shower_news_delivery",
        activation={"all": [_event("shower_started"), {"kind": "local_period", "equals": "morning"}]},
        window={"guard_start": "shower_started", "guard_end": "shower_ended"}, outcomes=(_event("media_started", content="news"), {"kind": "content_fresh_for_date"}),
        forbidden=({"kind": "media_outside_guard", "content": "news"}, {"kind": "stale_news"}), release={"all": [_event("shower_ended"), _event("media_stopped")]},
        authorization={"actions": ["media.control.play", "media.control.stop"], "content": "news"}, invariants={"content": "day_news", "context": "morning_shower"},
        actions=("media.control",), dynamics=("shower_session", "dated_content_catalog"), events=("shower_started", "shower_ended", "media_started", "media_stopped"),
        observations=("local_time", "content_date"), evaluator=("content_freshness_check", "continuous_guard_check"), horizon_seconds=43200,
        profile=("local_timezone", "morning_period"),
    ),
    "supported_emergency_call": _spec(
        "rd_split_44332c24ebca", "supported_emergency_call",
        activation={"all": [_event("emergency_detected"), {"kind": "membership", "field": "emergency_type", "profile": "supported_emergency_types"}]},
        window={"start": "emergency_detected", "deadline_profile": "emergency_call_sla_seconds"}, outcomes=(_event("emergency_call_placed"),),
        forbidden=({"kind": "call_for_unsupported_emergency"}, {"kind": "call_without_emergency"}, {"kind": "duplicate_call"}), release={"any": [_event("emergency_call_accepted"), _event("authorized_call_cancelled")]},
        authorization={"actions": ["emergency.call"], "types_profile": "supported_emergency_types", "service_profile": "emergency_service"},
        invariants={"condition": "supported_emergency", "outcome": "place_call"}, actions=("emergency.call",),
        dynamics=("emergency_detection_lifecycle",), events=("emergency_detected", "emergency_call_placed", "emergency_call_accepted", "unsupported_alarm"),
        observations=("emergency_type",), evaluator=("supported_type_check", "false_positive_call_check"),
        profile=("supported_emergency_types", "emergency_service", "emergency_call_sla_seconds"),
    ),
    "authorized_vehicle_gate_entry": _spec(
        "rd_6debc9157765", "authorized_vehicle_gate_entry",
        activation={"all": [_event("vehicle_arrived"), {"kind": "credential_allowlist_membership", "profile": "authorized_vehicle_ids"}]},
        window={"deadline_profile": "gate_access_sla_seconds", "reclose_profile": "gate_reclose_seconds"},
        outcomes=(_event("garage_door_opened"), _event("garage_door_closed")),
        forbidden=({"kind": "gate_open_for_unauthorized_vehicle"}, {"kind": "gate_left_open"}), release=_event("garage_door_closed"),
        authorization={"actions": ["garage.door.open", "garage.door.close"], "allowlist_profile": "authorized_vehicle_ids"},
        invariants={"object": "garage_gate", "authorization": "authorized_vehicles_only", "positive": "admit", "negative": "deny"},
        actions=("garage.door",), dynamics=("vehicle_credential_verification", "vehicle_entry_session"),
        events=("vehicle_arrived", "unauthorized_vehicle_arrived", "garage_door_opened", "garage_door_closed"), observations=("vehicle_identity",),
        evaluator=("allowlist_negative_check",), profile=("authorized_vehicle_ids", "gate_access_sla_seconds", "gate_reclose_seconds"),
    ),
}


CURRENT_WORKFLOW_BACKEND_CAPABILITIES: dict[str, Any] = {
    "observations": set(COMMON_OBSERVATIONS) | {
        "bathroom_occupancy", "coffee_freshness", "content_date", "delivery_identity",
        "emergency_type", "household_occupancy", "inventory_projection", "keys_present",
        "local_time", "order_state", "presented_credential", "stove_attendance",
        "vehicle_identity", "visitor_event_log", "visitor_identity",
    },
    "actions": {
        "laundry.control", "vacuum.control", "garage.door", "lock.control", "intercom.control",
        "fridge.door", "stove.control", "notification.send", "media.control", "alarm.control",
        "coffee.control", "toilet.control", "supply.control", "supply.order", "trash.control", "emergency.call",
    },
    "dynamics": set(COMMON_DYNAMICS) | {
        "laundry_cycle", "vacuum_cycle", "fridge_open_duration", "garage_obstruction_recovery",
        "bathroom_occupancy_lifecycle", "coffee_brew_cycle", "coffee_freshness_decay",
        "credential_verification", "curfew_guard", "dated_content_catalog", "delivery_identity_lifecycle",
        "delivery_lead_time", "departure_lifecycle", "emergency_detection_lifecycle", "entry_session",
        "intercom_session", "inventory_consumption", "local_calendar_clock", "occupancy_lifecycle",
        "procurement_order", "scheduled_alarm_clock", "scheduled_readiness", "shower_session",
        "stove_attendance_lifecycle", "vehicle_credential_verification", "vehicle_entry_session",
        "visitor_event_log", "visitor_identity_lifecycle", "weekly_calendar_recurrence",
    },
    "events": {
        "alarm_acknowledged", "alarm_sounded", "alarm_timeout", "authorized_return", "away_started",
        "bathroom_occupied", "bathroom_vacated", "bin_collection_due", "coffee_expired", "coffee_ready",
        "coffee_served", "departure_cancelled", "departure_completed", "departure_started", "delivery_arrived",
        "emergency_call_accepted", "emergency_call_placed", "emergency_detected", "failed_entry_attempt",
        "fridge_door_alarm", "fridge_door_closed", "front_door_locked", "front_door_unlocked",
        "garage_door_closed", "garage_door_obstruction", "garage_door_opened", "inventory_stockout",
        "keys_present", "laundry_cycle_finished", "mail_delivered", "media_started", "media_stopped",
        "notification_sent", "occupancy_became_zero", "occupants_departed", "occupants_returned",
        "resident_arrived", "shower_ended", "shower_started", "stove_attendance_restored",
        "stove_became_unattended", "stove_extinguished", "stove_hazard_shutoff", "supply_delivered",
        "supply_order_accepted", "television_curfew_ended", "television_curfew_started",
        "toilet_flushed", "toilet_paper_depleted", "toilet_use_finished", "unauthorized_vehicle_arrived",
        "unknown_credential_arrived", "unsupported_alarm", "vacuum_docked", "vehicle_arrived",
        "vehicle_parked", "visitor_answered", "visitor_interaction_ended", "visitor_rang", "visitor_recorded",
        "weekly_cleaning_due",
    },
    "evaluator_primitives": {
        "state_predicate", "event_order", "deadline_check", "forbidden_action_check", "release_check",
        "allowlist_negative_check", "content_freshness_check", "continuous_guard_check",
        "false_positive_call_check", "obstruction_recovery_check", "per_trigger_coverage",
        "purchase_authorization_check", "readiness_window_check", "recurrence_occurrence_check",
        "stockout_check", "supported_type_check", "time_tolerance_check", "transition_coverage",
        "unauthorized_access_negative_check",
    },
    "profile_requirements": {
        "alarm_tolerance_seconds", "authorized_vehicle_ids", "beer_budget", "beer_delivery_lead_seconds",
        "beer_order_quantity", "beer_reorder_point", "beer_suppliers", "cleaning_zone_scope",
        "coffee_brew_seconds", "coffee_freshness_seconds", "coffee_lateness_tolerance_seconds",
        "delivery_access_window", "emergency_call_sla_seconds", "emergency_service", "entry_relock_seconds",
        "entry_sla_seconds", "expected_delivery_allowlist", "flush_sla_seconds", "fridge_alarm_seconds",
        "fridge_intervention_after_seconds", "garage_close_sla_seconds", "gate_access_sla_seconds",
        "gate_reclose_seconds", "indicator_update_sla_seconds", "intended_wake_at",
        "intercom_response_sla_seconds", "laundry_due_at", "local_timezone", "lockdown_sla_seconds",
        "morning_period", "notification_recipients", "notification_sla_seconds", "pellet_budget",
        "pellet_delivery_lead_seconds", "pellet_order_quantity", "pellet_reorder_point", "pellet_suppliers",
        "resident_credential_allowlist", "security_scope", "shower_music_content", "shower_music_opt_in",
        "stove_safety_sla_seconds", "supported_emergency_types", "television_curfew_end",
        "visitor_allowlist", "weekly_cleaning_due_at",
    },
    "max_horizon_seconds": 604800,
}


REQUIRED_SPEC_FIELDS = {
    "responsibility_id", "scenario_type", "activation", "window", "required_outcomes", "forbidden",
    "release", "authorization", "query_invariants", "required_observations", "required_actions",
    "required_dynamics", "required_events", "required_evaluator_primitives", "minimum_horizon_seconds",
    "profile_requirements",
}


def validate_registry(registry: Mapping[str, Mapping[str, Any]] = WORKFLOW_SCENARIO_REGISTRY) -> None:
    """Raise ``ValueError`` if the registry is incomplete or ambiguous."""
    if len(registry) != 30:
        raise ValueError(f"workflow registry must contain 30 scenarios, got {len(registry)}")
    ids: set[str] = set()
    for key, spec in registry.items():
        missing = REQUIRED_SPEC_FIELDS - set(spec)
        if missing:
            raise ValueError(f"{key} missing fields: {sorted(missing)}")
        if spec["scenario_type"] != key:
            raise ValueError(f"{key} scenario_type mismatch")
        if spec["responsibility_id"] in ids:
            raise ValueError(f"duplicate responsibility_id: {spec['responsibility_id']}")
        ids.add(str(spec["responsibility_id"]))
        for field in ("activation", "window", "release", "authorization", "query_invariants"):
            if not isinstance(spec[field], Mapping) or not spec[field]:
                raise ValueError(f"{key}.{field} must be a non-empty object")
        for field in ("required_outcomes", "forbidden", "required_observations", "required_actions", "required_dynamics", "required_events", "required_evaluator_primitives"):
            if not isinstance(spec[field], list) or not spec[field]:
                raise ValueError(f"{key}.{field} must be a non-empty list")
        if not isinstance(spec["minimum_horizon_seconds"], int) or spec["minimum_horizon_seconds"] <= 0:
            raise ValueError(f"{key}.minimum_horizon_seconds must be positive")


def match_backend(spec: Mapping[str, Any], capabilities: Mapping[str, Any]) -> dict[str, Any]:
    """Return a fail-closed FULL/PARTIAL/UNSUPPORTED capability match."""
    dimensions = {
        "required_observations": "observations",
        "required_actions": "actions",
        "required_dynamics": "dynamics",
        "required_events": "events",
        "required_evaluator_primitives": "evaluator_primitives",
        "profile_requirements": "profile_requirements",
    }
    missing: dict[str, list[str]] = {}
    overlap = 0
    for required_field, capability_field in dimensions.items():
        required = set(spec[required_field])
        available = set(capabilities.get(capability_field, ()))
        absent = sorted(required - available)
        if absent:
            missing[required_field] = absent
        overlap += len(required & available)
    maximum = capabilities.get("max_horizon_seconds")
    if not isinstance(maximum, int) or maximum < spec["minimum_horizon_seconds"]:
        missing["minimum_horizon_seconds"] = [str(spec["minimum_horizon_seconds"])]
    if not missing:
        status = "FULL"
    elif overlap == 0:
        status = "UNSUPPORTED"
    else:
        status = "PARTIAL"
    return {
        "status": status,
        "responsibility_id": spec["responsibility_id"],
        "scenario_type": spec["scenario_type"],
        "missing_requirements": missing,
    }


def match_current_backend(scenario_type: str) -> dict[str, Any]:
    if scenario_type not in WORKFLOW_SCENARIO_REGISTRY:
        return {"status": "UNSUPPORTED", "scenario_type": scenario_type, "missing_requirements": {"scenario": [scenario_type]}}
    capabilities = dict(CURRENT_WORKFLOW_BACKEND_CAPABILITIES)
    capabilities["evaluator_primitives"] = set(evaluator_primitive_evidence(scenario_type))
    return match_backend(WORKFLOW_SCENARIO_REGISTRY[scenario_type], capabilities)


validate_registry()


__all__ = [
    "CURRENT_WORKFLOW_BACKEND_CAPABILITIES",
    "REQUIRED_SPEC_FIELDS",
    "WORKFLOW_SCENARIO_REGISTRY",
    "match_backend",
    "match_current_backend",
    "validate_registry",
]
