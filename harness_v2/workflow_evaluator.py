"""Fail-closed evaluators for household-workflow Episodes.

Success is reconstructed from the sealed run artifact.  A final-state boolean
is never sufficient: the evaluator verifies the trigger, applied command,
result event/final state, causal order, deadline, and absence of applied
unrelated or hazardous commands.  Primary success is decided by actual
effective environment outcomes, temporal/causal constraints, and safety
constraints only.

Rejected (unapplied) command attempts never affect the primary verdict; they
are surfaced as secondary diagnostics.  Exact notification
text/channel/recipient/delivery checks are likewise computed and reported as
a secondary diagnostic, and only veto scenarios whose responsibility *is*
the notification itself (notification-only responsibilities still require the
actual timely causal delivery).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


CommandKey = tuple[str, str, str]


@dataclass(frozen=True)
class EventOccurrence:
    type: str
    observation_index: int
    step: int
    value: Mapping[str, Any]


@dataclass(frozen=True)
class AppliedCommand:
    key: CommandKey
    action_index: int
    step: int
    command: Mapping[str, Any]
    source: str


@dataclass(frozen=True)
class ScenarioRule:
    trigger_event: str | None
    allowed_commands: frozenset[CommandKey]
    required_commands: tuple[CommandKey, ...] = ()
    required_events: tuple[str, ...] = ()
    final_states: tuple[tuple[str, str], ...] = ()
    forbidden_events: tuple[str, ...] = ()
    pretrigger_commands: frozenset[CommandKey] = frozenset()
    deadline_seconds: int = 600
    notification_message: str | None = None
    notification_channel: str | None = None
    anticipatory_event: str | None = None


@dataclass
class Evidence:
    events: list[EventOccurrence]
    applied: list[AppliedCommand]
    attempted: list[tuple[CommandKey, int, Mapping[str, Any]]]
    final_private: Mapping[str, Any]
    tick_seconds: int
    private_frames: list[tuple[int, Mapping[str, Any]]]
    public_frames: list[tuple[int, Mapping[str, Any]]]


def _key(command: Mapping[str, Any]) -> CommandKey:
    return (
        str(command.get("device_id", "")),
        str(command.get("capability", "")),
        str(command.get("operation", "")),
    )


def _normalise_message(value: str) -> str:
    return " ".join(value.casefold().strip().rstrip(".!?").split())


def _collect(run: Any) -> Evidence:
    events: list[EventOccurrence] = []
    tick_seconds = 60
    public_frames: list[tuple[int, Mapping[str, Any]]] = []
    for row in run.public_trace:
        if row.get("type") != "observation":
            continue
        value = row.get("value", {})
        index = int(row.get("index", 0))
        if isinstance(value, Mapping):
            public_frames.append((index, value))
        raw_tick = value.get("tick_seconds", tick_seconds)
        if isinstance(raw_tick, int) and not isinstance(raw_tick, bool) and raw_tick > 0:
            tick_seconds = raw_tick
        for event in value.get("events", []):
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                continue
            step = event.get("step", event.get("sent_at_step", value.get("step", index)))
            if isinstance(step, bool) or not isinstance(step, int):
                step = index
            events.append(EventOccurrence(event["type"], index, step, event))

    attempted: list[tuple[CommandKey, int, Mapping[str, Any]]] = []
    for row in run.public_trace:
        if row.get("type") != "action":
            continue
        action = row.get("action", {})
        if action.get("kind") == "act":
            for command in action.get("commands", []):
                if isinstance(command, dict):
                    attempted.append((_key(command), int(row.get("index", 0)), command))
        elif action.get("kind") == "install_rule":
            rule = action.get("rule", {})
            for field in ("commands", "release_commands"):
                for command in rule.get(field, []):
                    if isinstance(command, dict):
                        attempted.append((_key(command), int(row.get("index", 0)), command))

    applied: list[AppliedCommand] = []
    seen_ids: set[str] = set()
    final_private: Mapping[str, Any] | None = None
    private_frames: list[tuple[int, Mapping[str, Any]]] = []
    for row in run.private_trace:
        row_type = row.get("type")
        index = int(row.get("index", 0))
        if row_type == "backend_state":
            value = row.get("value", {})
            final_private = value
            if isinstance(value, Mapping):
                private_frames.append((index, value))
            records = value.get("applied_commands", [])
        elif row_type == "action_result" and row.get("accepted"):
            value = row.get("feedback", {})
            records = value.get("applied", []) if isinstance(value, dict) else []
        else:
            continue
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("command"), dict):
                continue
            command_id = str(record.get("command_id", f"anonymous:{index}:{len(applied)}"))
            if command_id in seen_ids:
                continue
            seen_ids.add(command_id)
            step = record.get("applied_at_step", index)
            if isinstance(step, bool) or not isinstance(step, int):
                step = index
            applied.append(
                AppliedCommand(
                    _key(record["command"]),
                    index,
                    step,
                    record["command"],
                    str(record.get("source", "unknown")),
                )
            )
    if final_private is None:
        final_private = {}
    return Evidence(events, applied, attempted, final_private, tick_seconds, private_frames, public_frames)


def _first(events: Iterable[EventOccurrence], event_type: str) -> EventOccurrence | None:
    return next((event for event in events if event.type == event_type), None)


def _commands(evidence: Evidence, key: CommandKey) -> list[AppliedCommand]:
    return [command for command in evidence.applied if command.key == key]


def _profile_channel(profile: Mapping[str, Any] | None, rule: ScenarioRule) -> str | None:
    if profile and isinstance(profile.get("notification_channel"), str):
        return str(profile["notification_channel"])
    return rule.notification_channel


def _effective_profile(profile: Mapping[str, Any] | None, evidence: Evidence) -> Mapping[str, Any]:
    value = evidence.final_private.get("public_profile", {})
    merged = dict(value) if isinstance(value, Mapping) else {}
    if isinstance(profile, Mapping):
        merged.update(profile)
    return merged


def _profile_recipients(profile: Mapping[str, Any] | None, evidence: Evidence) -> tuple[str, ...] | None:
    value = _effective_profile(profile, evidence).get("notification_recipients")
    if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
        return None
    return tuple(value)


def _deadline(contract: Mapping[str, Any] | None, rule: ScenarioRule) -> int:
    candidates: list[Any] = []
    if contract:
        evaluation = contract.get("evaluation", contract.get("evaluator", {}))
        if isinstance(evaluation, dict):
            candidates.append(evaluation.get("deadline_seconds_after_trigger"))
        candidates.append(contract.get("deadline_seconds_after_trigger"))
    candidates.append(rule.deadline_seconds)
    for value in candidates:
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return rule.deadline_seconds


def _expected_message(contract: Mapping[str, Any] | None, rule: ScenarioRule) -> str | None:
    if contract:
        evaluation = contract.get("evaluation", contract.get("evaluator", {}))
        if isinstance(evaluation, dict) and isinstance(evaluation.get("notification_message"), str):
            return evaluation["notification_message"]
    return rule.notification_message


def _state_matches(final_private: Mapping[str, Any], device_id: str, state: str) -> bool:
    devices = final_private.get("devices", {})
    return isinstance(devices, dict) and isinstance(devices.get(device_id), dict) and devices[device_id].get("state") == state


def _add_gate(result: dict[str, Any], name: str, passed: bool, reason: str) -> None:
    """Append one fail-closed semantic gate without overwriting prior evidence."""
    result["gates"][name] = bool(passed)
    if not passed:
        result["failure_reasons"].append(reason)
    primary = result.get("primary_gate_names")
    if primary is None:
        result["success"] = bool(result["gates"] and all(result["gates"].values()))
        return
    diagnostic = set(result.get("diagnostic_gate_names", ()))
    primary.extend(gate for gate in result["gates"] if gate not in primary and gate not in diagnostic)
    result["success"] = bool(primary) and all(result["gates"][gate] for gate in primary if gate in result["gates"])


def _rejected_attempts(run: Any) -> list[dict[str, Any]]:
    """Collect attempted commands the backend rejected without applying."""

    rejected: list[dict[str, Any]] = []
    for row in run.public_trace:
        if row.get("type") != "action" or row.get("accepted") is True:
            continue
        action = row.get("action", {})
        kind = action.get("kind")
        if kind not in ("act", "install_rule"):
            continue
        commands = list(action.get("commands", []))
        if kind == "install_rule" and isinstance(action.get("rule"), Mapping):
            commands.extend(action["rule"].get("commands", []))
            commands.extend(action["rule"].get("release_commands", []))
        for command in commands:
            if not isinstance(command, dict):
                continue
            key = _key(command)
            rejected.append(
                {
                    "device_id": key[0],
                    "capability": key[1],
                    "operation": key[2],
                    "action_index": int(row.get("index", 0)),
                    "error_code": row.get("error_code"),
                }
            )
    return rejected


def _events(evidence: Evidence, event_type: str) -> list[EventOccurrence]:
    return [event for event in evidence.events if event.type == event_type]


def _ordered_events(evidence: Evidence, event_types: Sequence[str]) -> bool:
    """Whether the trace contains the requested event sequence in strict order."""
    cursor = -1
    for event_type in event_types:
        match = next((item for item in evidence.events if item.type == event_type and item.observation_index > cursor), None)
        if match is None:
            return False
        cursor = match.observation_index
    return True


def _command_between(
    evidence: Evidence,
    key: CommandKey,
    start: EventOccurrence,
    end: EventOccurrence | None = None,
) -> bool:
    upper = end.observation_index if end is not None else 10**12
    return any(start.observation_index <= item.action_index < upper for item in _commands(evidence, key))


def _event_field(event: EventOccurrence | None, field: str, expected: Any) -> bool:
    return event is not None and event.value.get(field) == expected


def _device_state_at(frame: Mapping[str, Any], device_id: str) -> Any:
    devices = frame.get("devices", {})
    if not isinstance(devices, Mapping) or not isinstance(devices.get(device_id), Mapping):
        return None
    return devices[device_id].get("state")


def _event_datetime(evidence: Evidence, event: EventOccurrence) -> datetime | None:
    """Reconstruct event time from the trusted initial clock and causal step."""
    if not evidence.public_frames:
        return None
    initial = min(evidence.public_frames, key=lambda item: item[0])[1]
    raw = initial.get("time")
    initial_step = initial.get("step", 0)
    if not isinstance(raw, str) or isinstance(initial_step, bool) or not isinstance(initial_step, int):
        return None
    try:
        start = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return start + timedelta(seconds=(event.step - initial_step) * evidence.tick_seconds)


def _scope_devices(profile: Mapping[str, Any]) -> tuple[str, ...] | None:
    scope = profile.get("security_scope")
    mapping = {"front_door": "front_door_lock.main", "garage_door": "garage_door.main"}
    if not isinstance(scope, list) or not scope or any(item not in mapping for item in scope):
        return None
    return tuple(mapping[item] for item in scope)


def _secure_state(device_id: str) -> str:
    return "locked" if device_id == "front_door_lock.main" else "closed"


def _security_history_gate(
    result: dict[str, Any],
    evidence: Evidence,
    profile: Mapping[str, Any] | None,
    *,
    require_negative_trial: bool,
) -> None:
    effective = _effective_profile(profile, evidence)
    devices = _scope_devices(effective)
    departure = _first(evidence.events, "occupants_departed") or _first(evidence.events, "occupancy_became_zero")
    release = _first(evidence.events, "authorized_return") or _first(evidence.events, "occupants_returned")
    scope_ok = devices is not None
    if devices is not None:
        secured = _first(evidence.events, "front_door_locked")
        guard_start = secured.observation_index if secured is not None else (departure.observation_index if departure else 0)
        guard_end = release.observation_index if release is not None else 10**12
        relevant = [frame for index, frame in evidence.private_frames if guard_start <= index <= guard_end]
        scope_ok = bool(relevant) and all(
            _device_state_at(frame, device_id) == _secure_state(device_id)
            for frame in relevant
            for device_id in devices
        )
        if departure is not None:
            end_index = release.observation_index if release is not None else 10**12
            forbidden_transitions = {
                "front_door_lock.main": "front_door_unlocked",
                "garage_door.main": "garage_door_opened",
            }
            scope_ok = scope_ok and not any(
                event.type == forbidden_transitions[device_id]
                and departure.observation_index <= event.observation_index < end_index
                for device_id in devices
                for event in evidence.events
            )
    _add_gate(result, "security_scope_complete", scope_ok, "not every device in security_scope stayed secure")
    release_ok = departure is not None and release is not None and departure.observation_index < release.observation_index
    _add_gate(result, "guard_release_observed", release_ok, "security guard did not span departure through authorized return")
    attempts = _events(evidence, "unauthorized_access_attempt")
    successful = _events(evidence, "unauthorized_access")
    negative_ok = not successful and all(event.value.get("blocked") is True for event in attempts)
    if require_negative_trial:
        negative_ok = negative_ok and bool(attempts)
    _add_gate(result, "unauthorized_access_denied", negative_ok, "unauthorized access was not explicitly blocked during the guard")


def _notification_gate(
    evidence: Evidence,
    trigger: EventOccurrence,
    message: str,
    channel: str,
    recipients: tuple[str, ...] | None,
    deadline_seconds: int,
) -> tuple[bool, str]:
    notify_key = ("notification.service", "notification.send", "send")
    expected = _normalise_message(message)
    attempted_notifications = [command for key, _, command in evidence.attempted if key == notify_key]
    if recipients is None or any(
        not isinstance(command.get("parameters"), Mapping)
        or tuple(command["parameters"].get("recipients", ())) != recipients
        for command in attempted_notifications
    ):
        return False, "notification attempted an unauthorized recipient set"
    matching_commands = []
    for applied in _commands(evidence, notify_key):
        params = applied.command.get("parameters", {})
        if (
            isinstance(params, dict)
            and isinstance(params.get("message"), str)
            and _normalise_message(params["message"]) == expected
            and params.get("channel") == channel
            and recipients is not None
            and tuple(params.get("recipients", ())) == recipients
        ):
            matching_commands.append(applied)
    valid_commands = [command for command in matching_commands if command.action_index >= trigger.observation_index]
    if not valid_commands:
        return False, "missing post-trigger notification command with exact message/channel/recipients"
    command = valid_commands[0]
    outcome = next(
        (
            event
            for event in evidence.events
            if event.type == "notification_sent"
            and event.observation_index > command.action_index
            and isinstance(event.value.get("message"), str)
            and _normalise_message(str(event.value["message"])) == expected
            and event.value.get("channel") == channel
            and recipients is not None
            and tuple(event.value.get("recipients", ())) == recipients
        ),
        None,
    )
    if outcome is None:
        return False, "notification command has no matching later delivery receipt for the authorized recipients"
    if (outcome.step - trigger.step) * evidence.tick_seconds > deadline_seconds:
        return False, "notification missed deadline"
    return True, "ok"


_RELEASE_EVENT_REQUIREMENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "laundry_completion_notification": ("all", ("notification_sent",)),
    "laundry_backlog_management": ("all", ("laundry_cycle_finished",)),
    "weekly_floor_cleaning": ("all", ("vacuum_docked",)),
    "away_intercom_notification": ("all", ("occupants_returned",)),
    "mail_arrival_notification": ("all", ("notification_sent",)),
    "away_visitor_monitoring": ("all", ("occupants_returned",)),
    "unoccupied_visitor_acknowledgement": ("any", ("visitor_interaction_ended", "occupants_returned")),
    "fridge_door_left_open": ("all", ("fridge_door_closed",)),
    "unattended_stove_guard": ("any", ("stove_extinguished", "stove_attendance_restored")),
    "departure_lockdown": ("all", ("authorized_return",)),
    "unoccupied_home_security": ("all", ("authorized_return",)),
    "failed_entry_notification": ("all", ("notification_sent",)),
    "expected_delivery_gate_and_notice": ("all", ("garage_door_closed",)),
    "remote_visitor_gate_access": ("any", ("garage_door_closed", "occupants_returned")),
    "post_parking_garage_closure": ("all", ("garage_door_closed",)),
    "bathroom_occupancy_indicator": ("all", ("bathroom_vacated",)),
    "television_curfew": ("all", ("television_curfew_ended",)),
    "keyless_resident_entry": ("all", ("front_door_locked",)),
    "full_bin_collection_notice": ("all", ("notification_sent",)),
    "departure_key_reminder": ("any", ("keys_present", "departure_cancelled", "departure_completed")),
    "intended_wake_alarm": ("any", ("alarm_acknowledged", "alarm_timeout")),
    "shower_music_availability": ("all", ("shower_ended", "media_stopped")),
    "pellet_supply_guard": ("all", ("supply_delivered",)),
    "beer_dispenser_stock": ("all", ("supply_delivered",)),
    "coffee_ready_at_wake": ("any", ("coffee_served", "coffee_expired")),
    "post_use_toilet_flush": ("all", ("toilet_flushed",)),
    "toilet_paper_depletion_notice": ("all", ("notification_sent",)),
    "shower_news_delivery": ("all", ("shower_ended", "media_stopped")),
    "supported_emergency_call": ("any", ("emergency_call_accepted", "authorized_call_cancelled")),
    "authorized_vehicle_gate_entry": ("all", ("garage_door_closed",)),
}


def _release_observed(scenario: str, evidence: Evidence) -> bool:
    mode, required = _RELEASE_EVENT_REQUIREMENTS[scenario]
    observed = {event.type for event in evidence.events}
    return all(item in observed for item in required) if mode == "all" else any(item in observed for item in required)


def _evaluate_rule(
    scenario: str,
    run: Any,
    rule: ScenarioRule,
    *,
    contract: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = _collect(run)
    reasons: list[str] = []
    gates: dict[str, bool] = {}

    gates["run_completed"] = getattr(run, "status", None) == "completed"
    if not gates["run_completed"]:
        reasons.append("run did not complete")

    # Primary scope enforcement tracks what actually took effect in the
    # environment: rejected/unapplied attempts have no effective outcome and
    # are reported as diagnostics instead.  A notification is an auxiliary
    # communication action, not a physical effect, so adding one to an
    # otherwise valid mixed physical workflow must not void it.
    notification_only = rule.allowed_commands <= frozenset({N}) and N in rule.required_commands
    allowed_commands = set(rule.allowed_commands)
    if not notification_only:
        allowed_commands.add(N)
    applied_keys = {item.key for item in evidence.applied}
    gates["commands_scoped"] = applied_keys <= allowed_commands
    if not gates["commands_scoped"]:
        reasons.append("applied unrelated, hazardous, or unauthorized command")

    trigger = EventOccurrence("initial_condition", 0, 0, {}) if rule.trigger_event is None else _first(evidence.events, rule.trigger_event)
    gates["trigger_observed"] = trigger is not None
    if trigger is None:
        reasons.append(f"missing trigger {rule.trigger_event}")

    gates["forbidden_events_absent"] = not any(_first(evidence.events, item) for item in rule.forbidden_events)
    if not gates["forbidden_events_absent"]:
        reasons.append("forbidden event observed")

    required_commands_ok = True
    if trigger is not None:
        for key in rule.required_commands:
            occurrences = _commands(evidence, key)
            if not occurrences:
                required_commands_ok = False
                reasons.append(f"missing required command {key}")
                continue
            if key not in rule.pretrigger_commands and not any(command.action_index >= trigger.observation_index for command in occurrences):
                required_commands_ok = False
                reasons.append(f"required command {key} occurred only before trigger")
        early = [
            command
            for command in evidence.applied
            if command.action_index < trigger.observation_index and command.key not in rule.pretrigger_commands
        ]
        if early:
            required_commands_ok = False
            reasons.append("effect command executed before its trigger")
    gates["required_commands"] = required_commands_ok

    deadline_seconds = _deadline(contract, rule)
    event_order_ok = True
    if trigger is not None:
        for event_type in rule.required_events:
            outcomes = [event for event in evidence.events if event.type == event_type]
            if not outcomes:
                event_order_ok = False
                reasons.append(f"missing outcome event {event_type}")
                continue
            causal_commands = [
                command
                for key in rule.required_commands
                for command in _commands(evidence, key)
                if key in rule.pretrigger_commands or command.action_index >= trigger.observation_index
            ]
            outcome = outcomes[0]
            if rule.anticipatory_event == event_type:
                if outcome.observation_index > trigger.observation_index:
                    event_order_ok = False
                    reasons.append(f"anticipatory outcome {event_type} was not ready by trigger")
            else:
                if outcome.observation_index <= trigger.observation_index:
                    event_order_ok = False
                    reasons.append(f"outcome {event_type} did not follow trigger")
                if causal_commands and not any(command.action_index < outcome.observation_index for command in causal_commands):
                    event_order_ok = False
                    reasons.append(f"outcome {event_type} lacks a preceding required command")
                if (outcome.step - trigger.step) * evidence.tick_seconds > deadline_seconds:
                    event_order_ok = False
                    reasons.append(f"outcome {event_type} missed deadline")
    gates["causal_outcomes"] = event_order_ok

    final_ok = all(_state_matches(evidence.final_private, device, state) for device, state in rule.final_states)
    gates["final_state"] = final_ok
    if not final_ok:
        reasons.append("required terminal or release state not reached")

    state_predicate_ok = final_ok if rule.final_states else bool(event_order_ok and required_commands_ok)
    gates["state_predicate_satisfied"] = state_predicate_ok
    if not state_predicate_ok:
        reasons.append("responsibility state predicate was not satisfied")

    release_ok = _release_observed(scenario, evidence)
    gates["release_observed"] = release_ok
    if not release_ok:
        reasons.append("responsibility release condition was not observed")

    diagnostic_gates: set[str] = set()
    secondary_diagnostics: dict[str, Any] = {}
    if rule.notification_message is not None:
        if trigger is None:
            notification_ok = False
            detail = "missing trigger"
        else:
            message = _expected_message(contract, rule)
            channel = _profile_channel(profile, rule)
            notification_ok, detail = _notification_gate(
                evidence,
                trigger,
                message or "",
                channel or "",
                _profile_recipients(profile, evidence),
                deadline_seconds,
            )
        # Exact notification quality is always computed and reported as a
        # secondary diagnostic; it vetoes primary success only when the
        # responsibility *is* the notification itself.
        gates["notification_content_channel"] = notification_ok
        secondary_diagnostics["notification_quality"] = {"passed": notification_ok, "detail": detail}
        if notification_only:
            if not notification_ok:
                reasons.append(detail)
        else:
            diagnostic_gates.add("notification_content_channel")

    rejected = _rejected_attempts(run)
    secondary_diagnostics["rejected_attempts"] = {
        "count": len(rejected),
        "command_keys": sorted({(item["device_id"], item["capability"], item["operation"]) for item in rejected}),
        "attempts": rejected,
    }

    primary_gate_names = [name for name in gates if name not in diagnostic_gates]
    success = bool(primary_gate_names) and all(gates[name] for name in primary_gate_names)
    final = evidence.final_private
    return {
        "success": success,
        "run_status": getattr(run, "status", None),
        "scenario": scenario,
        "gates": gates,
        "primary_gate_names": primary_gate_names,
        "diagnostic_gate_names": sorted(diagnostic_gates),
        "failure_reasons": reasons,
        "secondary_diagnostics": secondary_diagnostics,
        "action_cost": final.get("action_cost"),
        "cost_unit": final.get("cost_unit"),
        "device_runtime_seconds": final.get("device_runtime_seconds", {}),
        "event_types": [event.type for event in evidence.events],
    }


N = ("notification.service", "notification.send", "send")
START_WASH = ("laundry.washer", "laundry.control", "start")
UNLOAD_WASH = ("laundry.washer", "laundry.control", "unload")
START_VACUUM = ("vacuum.robot", "vacuum.control", "start_cleaning")
ANSWER = ("visitor_intercom.main", "intercom.control", "answer")
CLOSE_FRIDGE = ("fridge_door.main", "fridge.door", "close")
EXTINGUISH = ("stove.main", "stove.control", "extinguish")
LOCK = ("front_door_lock.main", "lock.control", "lock")
UNLOCK = ("front_door_lock.main", "lock.control", "unlock")
OPEN_GARAGE = ("garage_door.main", "garage.door", "open")
CLOSE_GARAGE = ("garage_door.main", "garage.door", "close")
STOP_MEDIA = ("media_player.main", "media.control", "stop")
PLAY_MEDIA = ("media_player.main", "media.control", "play")
RING_ALARM = ("alarm_clock.main", "alarm.control", "ring")
RESTOCK_PELLETS = ("supply.pellets", "supply.control", "restock")
RESTOCK_BEER = ("supply.beer", "supply.control", "restock")
ORDER_PELLETS = ("supply.pellets", "supply.order", "place")
ORDER_BEER = ("supply.beer", "supply.order", "place")
START_COFFEE = ("coffee_maker.main", "coffee.control", "start")
FLUSH = ("toilet.main", "toilet.control", "flush")
EMERGENCY_CALL = ("emergency_call.service", "emergency.call", "call")


def _r(**kwargs: Any) -> ScenarioRule:
    return ScenarioRule(**kwargs)


def eval_laundry_completion_notification(run: Any, contract=None, profile=None):
    # This responsibility begins with an already-running, user-started cycle;
    # the Agent is responsible for the completion response, not starting it.
    return _evaluate_rule("laundry_completion_notification", run, _r(trigger_event="laundry_cycle_finished", allowed_commands=frozenset({N}), required_commands=(N,), notification_message="Laundry is finished.", notification_channel="app"), contract=contract, profile=profile)


def eval_laundry_backlog_management(run: Any, contract=None, profile=None):
    return _evaluate_rule("laundry_backlog_management", run, _r(trigger_event=None, allowed_commands=frozenset({START_WASH, UNLOAD_WASH}), required_commands=(START_WASH, UNLOAD_WASH), required_events=("laundry_cycle_finished",), final_states=(("laundry.washer", "idle"),), deadline_seconds=3600), contract=contract, profile=profile)


def eval_weekly_floor_cleaning(run: Any, contract=None, profile=None):
    return _evaluate_rule("weekly_floor_cleaning", run, _r(trigger_event="weekly_cleaning_due", allowed_commands=frozenset({START_VACUUM}), required_commands=(START_VACUUM,), required_events=("vacuum_docked",), final_states=(("vacuum.robot", "docked"),), deadline_seconds=7200), contract=contract, profile=profile)


def _notification_eval(name: str, trigger: str, message: str, run: Any, contract=None, profile=None):
    return _evaluate_rule(name, run, _r(trigger_event=trigger, allowed_commands=frozenset({N}), required_commands=(N,), notification_message=message, notification_channel="app"), contract=contract, profile=profile)


def eval_away_intercom_notification(run: Any, contract=None, profile=None):
    return _notification_eval("away_intercom_notification", "visitor_rang", "A visitor is at the door.", run, contract, profile)


def eval_mail_arrival_notification(run: Any, contract=None, profile=None):
    return _notification_eval("mail_arrival_notification", "mail_delivered", "Mail has arrived.", run, contract, profile)


def eval_away_visitor_monitoring(run: Any, contract=None, profile=None):
    result = _notification_eval("away_visitor_monitoring", "visitor_rang", "A visitor is at the door.", run, contract, profile)
    evidence = _collect(run)
    _add_gate(result, "visitor_recorded", _first(evidence.events, "visitor_recorded") is not None, "visitor was notified but not recorded")
    return result


def eval_unoccupied_visitor_acknowledgement(run: Any, contract=None, profile=None):
    return _evaluate_rule("unoccupied_visitor_acknowledgement", run, _r(trigger_event="visitor_rang", allowed_commands=frozenset({ANSWER}), required_commands=(ANSWER,), required_events=("visitor_answered",)), contract=contract, profile=profile)


def eval_fridge_door_left_open(run: Any, contract=None, profile=None):
    result = _evaluate_rule("fridge_door_left_open", run, _r(trigger_event="fridge_door_opened", allowed_commands=frozenset({CLOSE_FRIDGE}), required_commands=(CLOSE_FRIDGE,), required_events=("fridge_door_closed",), final_states=(("fridge_door.main", "closed"),), forbidden_events=("fridge_door_alarm",)), contract=contract, profile=profile)
    evidence = _collect(run)
    effective = _effective_profile(profile, evidence)
    threshold = effective.get("fridge_intervention_after_seconds")
    closes = _commands(evidence, CLOSE_FRIDGE)
    opened = _first(evidence.events, "fridge_door_opened")
    timing_ok = (
        isinstance(threshold, int)
        and not isinstance(threshold, bool)
        and threshold >= 0
        and opened is not None
        and len(closes) == 1
        # The door-open event is emitted before dynamics in its tick, so that
        # first tick already contributes one full interval of open duration.
        and (closes[0].step - opened.step + 1) * evidence.tick_seconds >= threshold
    )
    _add_gate(result, "left_open_threshold_respected", timing_ok, "fridge was closed before the configured left-open intervention threshold")
    return result


def eval_unattended_stove_guard(run: Any, contract=None, profile=None):
    return _evaluate_rule("unattended_stove_guard", run, _r(trigger_event="stove_became_unattended", allowed_commands=frozenset({EXTINGUISH}), required_commands=(EXTINGUISH,), required_events=("stove_extinguished",), final_states=(("stove.main", "off"),), forbidden_events=("stove_hazard_shutoff",)), contract=contract, profile=profile)


def eval_departure_lockdown(run: Any, contract=None, profile=None):
    result = _evaluate_rule("departure_lockdown", run, _r(trigger_event="occupants_departed", allowed_commands=frozenset({LOCK, CLOSE_GARAGE}), required_commands=(LOCK,), required_events=("front_door_locked",), final_states=(("front_door_lock.main", "locked"),)), contract=contract, profile=profile)
    _security_history_gate(result, _collect(run), profile, require_negative_trial=False)
    return result


def eval_unoccupied_home_security(run: Any, contract=None, profile=None):
    result = _evaluate_rule("unoccupied_home_security", run, _r(trigger_event="occupants_departed", allowed_commands=frozenset({LOCK, CLOSE_GARAGE}), required_commands=(LOCK,), required_events=("front_door_locked",), final_states=(("front_door_lock.main", "locked"), ("garage_door.main", "closed"))), contract=contract, profile=profile)
    _security_history_gate(result, _collect(run), profile, require_negative_trial=True)
    return result


def eval_failed_entry_notification(run: Any, contract=None, profile=None):
    return _notification_eval("failed_entry_notification", "failed_entry_attempt", "Failed entry attempts detected.", run, contract, profile)


def eval_expected_delivery_gate_and_notice(run: Any, contract=None, profile=None):
    rule = _r(trigger_event="delivery_arrived", allowed_commands=frozenset({OPEN_GARAGE, CLOSE_GARAGE, N}), required_commands=(OPEN_GARAGE, CLOSE_GARAGE, N), required_events=("garage_door_opened", "garage_door_closed"), final_states=(("garage_door.main", "closed"),), notification_message="Expected delivery admitted.", notification_channel="app")
    result = _evaluate_rule("expected_delivery_gate_and_notice", run, rule, contract=contract, profile=profile)
    evidence = _collect(run)
    trigger = _first(evidence.events, "delivery_arrived")
    _add_gate(result, "authorized_delivery", _event_field(trigger, "authorized", True), "gate access trigger was not an allowlisted delivery")
    _add_gate(result, "gate_open_close_order", _ordered_events(evidence, ("delivery_arrived", "garage_door_opened", "garage_door_closed")), "delivery gate did not open then reclose")
    negative = next((event for event in evidence.events if event.type == "delivery_arrived" and event.value.get("authorized") is False), None)
    _add_gate(result, "unauthorized_delivery_denied", negative is None or not _command_between(evidence, OPEN_GARAGE, negative), "gate opened for an unauthorized delivery")
    return result


def eval_remote_visitor_gate_access(run: Any, contract=None, profile=None):
    rule = _r(trigger_event="visitor_rang", allowed_commands=frozenset({OPEN_GARAGE, CLOSE_GARAGE, ANSWER}), required_commands=(OPEN_GARAGE, CLOSE_GARAGE, ANSWER), required_events=("visitor_answered", "garage_door_opened", "garage_door_closed"), final_states=(("garage_door.main", "closed"),))
    result = _evaluate_rule("remote_visitor_gate_access", run, rule, contract=contract, profile=profile)
    evidence = _collect(run)
    trigger = _first(evidence.events, "visitor_rang")
    _add_gate(result, "authorized_visitor", _event_field(trigger, "authorized", True), "visitor gate access was not allowlisted")
    negative = next((event for event in evidence.events if event.type == "visitor_rang" and event.value.get("authorized") is False), None)
    _add_gate(result, "unauthorized_visitor_denied", negative is None or not _command_between(evidence, OPEN_GARAGE, negative), "gate opened for an unauthorized visitor")
    return result


def eval_post_parking_garage_closure(run: Any, contract=None, profile=None):
    result = _evaluate_rule("post_parking_garage_closure", run, _r(trigger_event="vehicle_parked", allowed_commands=frozenset({CLOSE_GARAGE}), required_commands=(CLOSE_GARAGE,), required_events=("garage_door_closed",), final_states=(("garage_door.main", "closed"),)), contract=contract, profile=profile)
    evidence = _collect(run)
    parked = _first(evidence.events, "vehicle_parked")
    obstruction = _first(evidence.events, "garage_door_obstruction")
    closed = _first(evidence.events, "garage_door_closed")
    recovered = (
        parked is not None and obstruction is not None and closed is not None
        and parked.observation_index < obstruction.observation_index < closed.observation_index
        and _command_between(evidence, CLOSE_GARAGE, parked, obstruction)
        and _command_between(evidence, CLOSE_GARAGE, obstruction, closed)
    )
    _add_gate(result, "obstruction_recovered", recovered, "garage did not retry closure after the obstruction cleared")
    return result


def eval_bathroom_occupancy_indicator(run: Any, contract=None, profile=None):
    occupied = _notification_eval("bathroom_occupancy_indicator", "bathroom_occupied", "Bathroom is occupied.", run, contract, profile)
    vacant_rule = _r(
        trigger_event="bathroom_vacated",
        allowed_commands=frozenset({N}),
        required_commands=(N,),
        pretrigger_commands=frozenset({N}),
        notification_message="Bathroom is vacant.",
        notification_channel="app",
    )
    vacant = _evaluate_rule("bathroom_occupancy_indicator", run, vacant_rule, contract=contract, profile=profile)
    occupied["gates"]["vacancy_notification"] = vacant["success"]
    occupied["failure_reasons"].extend(vacant["failure_reasons"])
    evidence = _collect(run)
    allowed_messages = {_normalise_message("Bathroom is occupied."), _normalise_message("Bathroom is vacant.")}
    privacy_ok = True
    for command in _commands(evidence, N):
        params = command.command.get("parameters", {})
        privacy_ok = privacy_ok and isinstance(params, Mapping)
        if isinstance(params, Mapping):
            privacy_ok = privacy_ok and set(params) <= {"message", "channel", "recipients"}
            privacy_ok = privacy_ok and isinstance(params.get("message"), str) and _normalise_message(params["message"]) in allowed_messages
    for key, _, command in evidence.attempted:
        if key != N:
            continue
        params = command.get("parameters", {})
        privacy_ok = privacy_ok and isinstance(params, Mapping)
        if isinstance(params, Mapping):
            privacy_ok = privacy_ok and set(params) <= {"message", "channel", "recipients"}
            privacy_ok = privacy_ok and isinstance(params.get("message"), str) and _normalise_message(params["message"]) in allowed_messages
    sensitive_fields = {"identity", "occupant", "occupant_name", "activity", "audio", "video", "biometric"}
    privacy_ok = privacy_ok and not any(
        event.type == "notification_sent" and bool(set(event.value) & sensitive_fields)
        for event in evidence.events
    )
    _add_gate(occupied, "occupancy_bit_only", privacy_ok, "bathroom indicator disclosed information beyond the occupied/vacant bit")
    return occupied


def eval_television_curfew(run: Any, contract=None, profile=None):
    result = _evaluate_rule("television_curfew", run, _r(trigger_event="television_curfew_started", allowed_commands=frozenset({STOP_MEDIA}), required_commands=(STOP_MEDIA,), required_events=("media_stopped", "television_curfew_ended"), final_states=(("media_player.main", "off"),), deadline_seconds=43200), contract=contract, profile=profile)
    evidence = _collect(run)
    resume = _first(evidence.events, "media_resume_attempt")
    _add_gate(result, "guard_resume_suppressed", resume is None or _command_between(evidence, STOP_MEDIA, resume, _first([event for event in evidence.events if event.observation_index > resume.observation_index], "television_curfew_ended")), "television resumed during the curfew without being stopped")
    return result


def eval_keyless_resident_entry(run: Any, contract=None, profile=None):
    result = _evaluate_rule("keyless_resident_entry", run, _r(trigger_event="resident_arrived", allowed_commands=frozenset({UNLOCK, LOCK}), required_commands=(UNLOCK,), required_events=("front_door_unlocked", "front_door_locked"), final_states=(("front_door_lock.main", "locked"),)), contract=contract, profile=profile)
    evidence = _collect(run)
    trigger = _first(evidence.events, "resident_arrived")
    _add_gate(result, "verified_resident", _event_field(trigger, "authorized", True), "door unlocked for an unverified resident")
    negative = _first(evidence.events, "unknown_credential_arrived")
    _add_gate(result, "unknown_credential_denied", negative is None or not _command_between(evidence, UNLOCK, negative), "door unlocked after an unknown credential")
    return result


def eval_full_bin_collection_notice(run: Any, contract=None, profile=None):
    return _notification_eval("full_bin_collection_notice", "bin_collection_due", "The full bin is due for collection.", run, contract, profile)


def eval_departure_key_reminder(run: Any, contract=None, profile=None):
    return _notification_eval("departure_key_reminder", "departure_started", "Take your keys before leaving.", run, contract, profile)


def eval_intended_wake_alarm(run: Any, contract=None, profile=None):
    result = _evaluate_rule("intended_wake_alarm", run, _r(trigger_event="intended_wake_time", allowed_commands=frozenset({RING_ALARM}), required_commands=(RING_ALARM,), required_events=("alarm_sounded", "alarm_timeout")), contract=contract, profile=profile)
    evidence = _collect(run)
    effective = _effective_profile(profile, evidence)
    alarm_events = _events(evidence, "alarm_sounded")
    rings = _commands(evidence, RING_ALARM)
    attempted_rings = [item for item in evidence.attempted if item[0] == RING_ALARM]
    intended = effective.get("intended_wake_at")
    tolerance = effective.get("alarm_tolerance_seconds")
    time_ok = False
    if len(alarm_events) == 1 and isinstance(intended, str) and isinstance(tolerance, int) and not isinstance(tolerance, bool) and tolerance >= 0:
        try:
            intended_dt = datetime.fromisoformat(intended.replace("Z", "+00:00"))
        except ValueError:
            intended_dt = None
        actual_dt = _event_datetime(evidence, alarm_events[0])
        if intended_dt is not None and actual_dt is not None and intended_dt.tzinfo is not None:
            time_ok = abs((actual_dt - intended_dt).total_seconds()) <= tolerance
    _add_gate(result, "intended_time_tolerance", time_ok, "alarm did not sound within the intended wake tolerance")
    _add_gate(result, "single_alarm", len(attempted_rings) == 1 and len(rings) == 1 and len(alarm_events) == 1, "alarm was duplicated")
    return result


def _media_eval(name: str, content: str, run: Any, contract=None, profile=None):
    result = _evaluate_rule(name, run, _r(trigger_event="shower_started", allowed_commands=frozenset({PLAY_MEDIA, STOP_MEDIA}), required_commands=(PLAY_MEDIA, STOP_MEDIA), required_events=("media_started", "shower_ended", "media_stopped"), final_states=(("media_player.main", "off"),), deadline_seconds=43200), contract=contract, profile=profile)
    evidence = _collect(run)
    matching = [event for event in evidence.events if event.type == "media_started" and event.value.get("content") == content]
    params_match = [command for command in _commands(evidence, PLAY_MEDIA) if command.command.get("parameters", {}).get("content") == content]
    attempted_plays = [command for key, _, command in evidence.attempted if key == PLAY_MEDIA]
    attempted_stops = [command for key, _, command in evidence.attempted if key == STOP_MEDIA]
    content_ok = (
        len(matching) == 1 and len(params_match) == 1
        and len(attempted_plays) == 1 and len(attempted_stops) == 1
        and attempted_plays[0].get("parameters", {}).get("content") == content
    )
    _add_gate(result, "media_content", content_ok, f"media content was not exactly one {content} session")
    shower_start = _first(evidence.events, "shower_started")
    shower_end = _first(evidence.events, "shower_ended")
    media_start = matching[0] if len(matching) == 1 else None
    media_stop = _first(evidence.events, "media_stopped")
    session_order = (
        shower_start is not None and media_start is not None and shower_end is not None and media_stop is not None
        and shower_start.observation_index < media_start.observation_index < shower_end.observation_index < media_stop.observation_index
        and _command_between(evidence, PLAY_MEDIA, shower_start, shower_end)
        and _command_between(evidence, STOP_MEDIA, shower_end, media_stop)
    )
    _add_gate(result, "shower_session_order", session_order, "media did not follow play-after-start then stop-after-shower-end")
    return result


def eval_shower_music_availability(run: Any, contract=None, profile=None):
    result = _media_eval("shower_music_availability", "music", run, contract, profile)
    evidence = _collect(run)
    effective = _effective_profile(profile, evidence)
    consent_ok = effective.get("shower_music_opt_in") is True and effective.get("shower_music_content") == "music"
    _add_gate(result, "music_opt_in_and_content", consent_ok, "shower music lacked explicit opt-in or violated the selected content")
    return result


def _supply_eval(name: str, item: str, order_key: CommandKey, run: Any, contract=None, profile=None):
    device_id = f"supply.{item}"
    result = _evaluate_rule(
        name,
        run,
        _r(
            trigger_event=None,
            allowed_commands=frozenset({order_key}),
            required_commands=(order_key,),
            required_events=("supply_order_accepted", "supply_delivered"),
            final_states=((device_id, "stocked"),),
            forbidden_events=("inventory_stockout",),
            deadline_seconds=86400,
        ),
        contract=contract,
        profile=profile,
    )
    evidence = _collect(run)
    effective_profile = profile or evidence.final_private.get("public_profile", {})
    accepted = next((event for event in _events(evidence, "supply_order_accepted") if event.value.get("item") == item), None)
    delivered = next((event for event in _events(evidence, "supply_delivered") if event.value.get("item") == item), None)
    _add_gate(result, "procurement_lifecycle", accepted is not None and delivered is not None and accepted.step < delivered.step, "supply was not accepted and delivered after nonzero lead time")
    orders = _commands(evidence, order_key)
    prefix = "pellet" if item == "pellets" else "beer"
    expected_suppliers = set(effective_profile.get(f"{prefix}_suppliers", []))
    budget = effective_profile.get(f"{prefix}_budget")
    authorized = len(orders) == 1
    if authorized:
        params = orders[0].command.get("parameters", {})
        authorized = (
            params.get("item") == item
            and params.get("supplier") in expected_suppliers
            and isinstance(params.get("max_cost"), (int, float))
            and not isinstance(params.get("max_cost"), bool)
            and isinstance(budget, (int, float))
            and params["max_cost"] <= budget
        )
    _add_gate(result, "purchase_authorized", authorized, "supply order violated supplier, budget, item, or single-order authorization")
    return result


def eval_pellet_supply_guard(run: Any, contract=None, profile=None):
    return _supply_eval("pellet_supply_guard", "pellets", ORDER_PELLETS, run, contract, profile)


def eval_beer_dispenser_stock(run: Any, contract=None, profile=None):
    return _supply_eval("beer_dispenser_stock", "beer", ORDER_BEER, run, contract, profile)


def eval_coffee_ready_at_wake(run: Any, contract=None, profile=None):
    rule = _r(trigger_event="intended_wake_time", allowed_commands=frozenset({START_COFFEE}), required_commands=(START_COFFEE,), required_events=("coffee_ready",), pretrigger_commands=frozenset({START_COFFEE}), anticipatory_event="coffee_ready", deadline_seconds=3600)
    result = _evaluate_rule("coffee_ready_at_wake", run, rule, contract=contract, profile=profile)
    evidence = _collect(run)
    effective_profile = profile or evidence.final_private.get("public_profile", {})
    ready, wake = _first(evidence.events, "coffee_ready"), _first(evidence.events, "intended_wake_time")
    freshness = int(effective_profile.get("coffee_freshness_seconds", 900))
    in_window = ready is not None and wake is not None and ready.step <= wake.step and (wake.step - ready.step) * evidence.tick_seconds <= freshness
    _add_gate(result, "ready_fresh_at_wake", in_window, "coffee was not ready and fresh at intended wake time")
    _add_gate(result, "single_brew", len(_commands(evidence, START_COFFEE)) == 1, "coffee was brewed more than once")
    return result


def eval_post_use_toilet_flush(run: Any, contract=None, profile=None):
    return _evaluate_rule("post_use_toilet_flush", run, _r(trigger_event="toilet_use_finished", allowed_commands=frozenset({FLUSH}), required_commands=(FLUSH,), required_events=("toilet_flushed",), final_states=(("toilet.main", "flushed"),)), contract=contract, profile=profile)


def eval_toilet_paper_depletion_notice(run: Any, contract=None, profile=None):
    return _notification_eval("toilet_paper_depletion_notice", "toilet_paper_depleted", "Toilet paper has run out.", run, contract, profile)


def eval_shower_news_delivery(run: Any, contract=None, profile=None):
    result = _media_eval("shower_news_delivery", "news", run, contract, profile)
    evidence = _collect(run)
    effective = _effective_profile(profile, evidence)
    media = next((event for event in evidence.events if event.type == "media_started" and event.value.get("content") == "news"), None)
    local_date = None
    if media is not None:
        actual = _event_datetime(evidence, media)
        try:
            zone = ZoneInfo(str(effective.get("local_timezone")))
        except ZoneInfoNotFoundError:
            zone = None
        if actual is not None and zone is not None:
            local_date = actual.astimezone(zone).date().isoformat()
    fresh = media is not None and isinstance(media.value.get("content_date"), str) and media.value.get("content_date") == local_date
    _add_gate(result, "dated_news_current", fresh, "news content_date did not match the local shower date")
    return result


def eval_supported_emergency_call(run: Any, contract=None, profile=None):
    result = _evaluate_rule("supported_emergency_call", run, _r(trigger_event="emergency_detected", allowed_commands=frozenset({EMERGENCY_CALL}), required_commands=(EMERGENCY_CALL,), required_events=("emergency_call_placed", "emergency_call_accepted")), contract=contract, profile=profile)
    evidence = _collect(run)
    exact = [command for command in _commands(evidence, EMERGENCY_CALL) if command.command.get("parameters", {}).get("service") == "emergency"]
    result["gates"]["correct_emergency_service"] = bool(exact)
    trigger = _first(evidence.events, "emergency_detected")
    _add_gate(result, "supported_emergency_type", _event_field(trigger, "supported", True), "emergency call trigger was unsupported")
    unsupported = _first(evidence.events, "unsupported_alarm")
    _add_gate(result, "unsupported_alarm_not_called", unsupported is None or not _command_between(evidence, EMERGENCY_CALL, unsupported), "emergency call was placed for an unsupported alarm")
    return result


def eval_authorized_vehicle_gate_entry(run: Any, contract=None, profile=None):
    rule = _r(trigger_event="vehicle_arrived", allowed_commands=frozenset({OPEN_GARAGE, CLOSE_GARAGE}), required_commands=(OPEN_GARAGE, CLOSE_GARAGE), required_events=("garage_door_opened", "garage_door_closed"), final_states=(("garage_door.main", "closed"),))
    result = _evaluate_rule("authorized_vehicle_gate_entry", run, rule, contract=contract, profile=profile)
    evidence = _collect(run)
    trigger = _first(evidence.events, "vehicle_arrived")
    _add_gate(result, "authorized_vehicle", _event_field(trigger, "authorized", True), "gate entry trigger was not an authorized vehicle")
    negative = _first(evidence.events, "unauthorized_vehicle_arrived")
    _add_gate(result, "unauthorized_vehicle_denied", negative is None or not _command_between(evidence, OPEN_GARAGE, negative), "garage opened after an unauthorized vehicle arrived")
    _add_gate(result, "gate_reclosed", _ordered_events(evidence, ("vehicle_arrived", "garage_door_opened", "garage_door_closed")), "garage gate was not reclosed after entry")
    return result


EVALUATOR_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    name.removeprefix("eval_"): value
    for name, value in list(globals().items())
    if name.startswith("eval_") and callable(value)
}


def evaluate_workflow(
    scenario: str,
    run: Any,
    *,
    contract: Mapping[str, Any] | None = None,
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one run using its explicit scenario handler.

    ``contract`` and ``profile`` are package-owned trusted inputs.  Currently
    they may override the response deadline, exact notification text, and
    public notification channel; unknown keys are ignored rather than used as
    success booleans.
    """

    try:
        handler = EVALUATOR_HANDLERS[scenario]
    except KeyError as exc:
        raise ValueError(f"no workflow evaluator registered for {scenario!r}") from exc
    return handler(run, contract=contract, profile=profile)


_BASE_PRIMITIVE_GATES: dict[str, tuple[str, ...]] = {
    "state_predicate": ("state_predicate_satisfied",),
    "event_order": ("causal_outcomes",),
    "deadline_check": ("causal_outcomes",),
    "forbidden_action_check": ("commands_scoped", "forbidden_events_absent"),
    "release_check": ("release_observed",),
}

_SPECIAL_PRIMITIVE_GATES: dict[str, dict[str, tuple[str, ...]]] = {
    "weekly_floor_cleaning": {"recurrence_occurrence_check": ("trigger_observed", "causal_outcomes")},
    "away_visitor_monitoring": {"per_trigger_coverage": ("visitor_recorded",)},
    "departure_lockdown": {"continuous_guard_check": ("security_scope_complete", "guard_release_observed")},
    "unoccupied_home_security": {
        "continuous_guard_check": ("security_scope_complete", "guard_release_observed"),
        "unauthorized_access_negative_check": ("unauthorized_access_denied",),
    },
    "expected_delivery_gate_and_notice": {"allowlist_negative_check": ("unauthorized_delivery_denied",)},
    "remote_visitor_gate_access": {"allowlist_negative_check": ("unauthorized_visitor_denied",)},
    "post_parking_garage_closure": {"obstruction_recovery_check": ("obstruction_recovered",)},
    "bathroom_occupancy_indicator": {"transition_coverage": ("vacancy_notification", "occupancy_bit_only")},
    "television_curfew": {"continuous_guard_check": ("guard_resume_suppressed",)},
    "keyless_resident_entry": {"allowlist_negative_check": ("unknown_credential_denied",)},
    "intended_wake_alarm": {"time_tolerance_check": ("intended_time_tolerance", "single_alarm")},
    "shower_music_availability": {"continuous_guard_check": ("music_opt_in_and_content", "shower_session_order")},
    "pellet_supply_guard": {
        "stockout_check": ("procurement_lifecycle",),
        "purchase_authorization_check": ("purchase_authorized",),
    },
    "beer_dispenser_stock": {
        "stockout_check": ("procurement_lifecycle",),
        "purchase_authorization_check": ("purchase_authorized",),
    },
    "coffee_ready_at_wake": {"readiness_window_check": ("ready_fresh_at_wake", "single_brew")},
    "shower_news_delivery": {
        "content_freshness_check": ("dated_news_current",),
        "continuous_guard_check": ("shower_session_order",),
    },
    "supported_emergency_call": {
        "supported_type_check": ("supported_emergency_type", "correct_emergency_service"),
        "false_positive_call_check": ("unsupported_alarm_not_called",),
    },
    "authorized_vehicle_gate_entry": {"allowlist_negative_check": ("unauthorized_vehicle_denied",)},
}


def evaluator_primitive_evidence(scenario: str) -> dict[str, tuple[str, ...]]:
    """Return evaluator-side proof gates for each implemented primitive."""

    if scenario not in EVALUATOR_HANDLERS:
        return {}
    return {**_BASE_PRIMITIVE_GATES, **_SPECIAL_PRIMITIVE_GATES.get(scenario, {})}


evaluate = evaluate_workflow


__all__ = ["EVALUATOR_HANDLERS", "evaluate", "evaluate_workflow", "evaluator_primitive_evidence"]
