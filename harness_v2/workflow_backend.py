"""Deterministic household workflow backend for Harness V2.

Implements the Backend protocol from harness_v2/core.py with ten household
device state machines (laundry, vacuum, dishwasher, garage door, front door
lock, visitor intercom, fridge door, stove, mailbox, bathroom occupancy).

Time advances in fixed-tick causal steps: per tick the backend processes rule
firings, seeded exogenous events, then device dynamics. All commands are
validated atomically before any mutation, so a rejected ``act`` or
``install_rule`` leaves the state digest untouched.

Accounting is recorded as ``task_elapsed_seconds``, ``device_runtime_seconds``
and a cumulative ``action_cost`` in abstract ``action_unit`` (commands per
unit); no physical energy claim is made anywhere.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from math import ceil
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .core import ActionOutcome, BackendStep, EpisodeSpec


TICK_SECONDS = 60
HORIZON_SECONDS = 3600
MAX_TICKS = 10080
MAX_ACTIONS_PER_TICK = 8
START = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)

DEFAULT_CONFIG: dict[str, int] = {
    "washer_cycle_seconds": 2700,
    "dishwasher_cycle_seconds": 1800,
    "vacuum_clean_seconds": 1800,
    "vacuum_return_seconds": 120,
    "vacuum_drain_percent_per_tick": 1,
    "vacuum_charge_percent_per_tick": 5,
    "vacuum_min_battery_to_start": 30,
    "vacuum_low_battery": 20,
    "garage_move_seconds": 60,
    "lock_autolock_seconds": 300,
    "intercom_ring_timeout_seconds": 120,
    "intercom_answered_clear_seconds": 60,
    "fridge_alarm_seconds": 180,
    "stove_hazard_seconds": 600,
    "bathroom_occupancy_seconds": 300,
    "coffee_brew_seconds": 300,
    "coffee_freshness_seconds": 900,
    "alarm_timeout_seconds": 300,
    "occupancy_away_seconds": 2400,
    "shower_session_seconds": 600,
    "departure_session_seconds": 600,
    "entry_relock_seconds": 300,
    "gate_reclose_seconds": 300,
    "pellet_delivery_lead_seconds": 900,
    "beer_delivery_lead_seconds": 900,
    "inventory_consumption_seconds": 3600,
    "inventory_consumption_units": 1,
    "initial_supply_level": 10,
}

# These are public household facts/preferences, not evaluator targets.  They are
# exposed for every scenario so that their presence cannot reveal which hidden
# responsibility is being evaluated.
DEFAULT_PUBLIC_PROFILE: dict[str, Any] = {
    "local_timezone": "UTC",
    "notification_channel": "app",
    "notification_recipients": ["resident.primary"],
    "notification_sla_seconds": 300,
    "laundry_due_at": "2026-01-01T19:00:00Z",
    "intercom_response_sla_seconds": 300,
    "weekly_cleaning_due_at": "2026-01-01T18:02:00Z",
    "cleaning_zone_scope": ["living_room", "kitchen", "hall"],
    "fridge_intervention_after_seconds": 120,
    "fridge_alarm_seconds": 180,
    "stove_safety_sla_seconds": 300,
    "lockdown_sla_seconds": 300,
    "security_scope": ["front_door", "garage_door"],
    "expected_delivery_allowlist": ["delivery.expected"],
    "delivery_access_window": {"start": "18:00", "end": "19:00"},
    "visitor_allowlist": ["visitor.authorized"],
    "resident_credential_allowlist": ["resident.mobile"],
    "authorized_vehicle_ids": ["resident.vehicle"],
    "gate_access_sla_seconds": 300,
    "entry_sla_seconds": 300,
    "entry_relock_seconds": 300,
    "gate_reclose_seconds": 300,
    "garage_close_sla_seconds": 300,
    "indicator_update_sla_seconds": 300,
    "television_curfew_end": "06:00",
    "intended_wake_at": "2026-01-01T18:15:00Z",
    "alarm_tolerance_seconds": 60,
    "shower_music_opt_in": True,
    "shower_music_content": "music",
    "pellet_reorder_point": 10,
    "pellet_delivery_lead_seconds": 900,
    "pellet_budget": 50,
    "pellet_suppliers": ["supplier.home_heat"],
    "pellet_order_quantity": 50,
    "beer_reorder_point": 10,
    "beer_delivery_lead_seconds": 900,
    "beer_budget": 40,
    "beer_suppliers": ["supplier.beverage"],
    "beer_order_quantity": 50,
    "coffee_brew_seconds": 300,
    "coffee_freshness_seconds": 900,
    "coffee_lateness_tolerance_seconds": 120,
    "morning_period": {"start": "05:00", "end": "12:00"},
    "supported_emergency_types": ["fall", "smoke"],
    "emergency_service": "emergency",
    "emergency_call_sla_seconds": 120,
    "flush_sla_seconds": 300,
}

DEVICE_SPECS: dict[str, dict[str, Any]] = {
    "laundry.washer": {"device_type": "laundry", "capabilities": ["laundry.control"], "active_states": {"washing"}},
    "vacuum.robot": {"device_type": "vacuum", "capabilities": ["vacuum.control"], "active_states": {"cleaning", "returning"}},
    "dishwasher.main": {"device_type": "dishwasher", "capabilities": ["dishwasher.control"], "active_states": {"running"}},
    "garage_door.main": {"device_type": "garage_door", "capabilities": ["garage.door"], "active_states": {"opening", "closing"}},
    "front_door_lock.main": {"device_type": "front_door_lock", "capabilities": ["lock.control"], "active_states": set()},
    "visitor_intercom.main": {"device_type": "visitor_intercom", "capabilities": ["intercom.control"], "active_states": {"ringing", "answered"}},
    "fridge_door.main": {"device_type": "fridge_door", "capabilities": ["fridge.door"], "active_states": {"open"}},
    "stove.main": {"device_type": "stove", "capabilities": ["stove.control"], "active_states": {"on"}},
    "mailbox.main": {"device_type": "mailbox", "capabilities": ["mailbox.control"], "active_states": set()},
    "bathroom_occupancy.main": {"device_type": "bathroom_occupancy", "capabilities": [], "active_states": {"occupied"}},
    "notification.service": {"device_type": "notification_service", "capabilities": ["notification.send"], "active_states": set()},
    "media_player.main": {"device_type": "media_player", "capabilities": ["media.control"], "active_states": {"playing"}},
    "alarm_clock.main": {"device_type": "alarm_clock", "capabilities": ["alarm.control"], "active_states": {"ringing"}},
    "coffee_maker.main": {"device_type": "coffee_maker", "capabilities": ["coffee.control"], "active_states": {"brewing"}},
    "toilet.main": {"device_type": "toilet", "capabilities": ["toilet.control"], "active_states": set()},
    "toilet_paper.main": {"device_type": "toilet_paper", "capabilities": ["supply.control"], "active_states": set()},
    "trash_bin.main": {"device_type": "trash_bin", "capabilities": ["trash.control"], "active_states": set()},
    "supply.pellets": {"device_type": "supply", "capabilities": ["supply.order"], "active_states": set()},
    "supply.beer": {"device_type": "supply", "capabilities": ["supply.order"], "active_states": set()},
    "emergency_call.service": {"device_type": "emergency_call", "capabilities": ["emergency.call"], "active_states": set()},
}

COMMAND_COST = 1
COST_UNIT = "action_unit"

ALLOWED_COMMANDS: dict[str, dict[str, dict[str, Any]]] = {
    "laundry.washer": {"laundry.control": {"load": "idle", "start": "loaded", "unload": "done"}},
    "dishwasher.main": {"dishwasher.control": {"load": "idle", "start": "loaded", "unload": "done"}},
    "vacuum.robot": {"vacuum.control": {"start_cleaning": "docked", "dock": "cleaning"}},
    "garage_door.main": {"garage.door": {"open": "closed", "close": "open"}},
    "front_door_lock.main": {"lock.control": {"lock": "unlocked", "unlock": "locked"}},
    "visitor_intercom.main": {"intercom.control": {"answer": "ringing"}},
    "fridge_door.main": {"fridge.door": {"open": "closed", "close": "open"}},
    "stove.main": {"stove.control": {"ignite": "off", "set_level": "on", "extinguish": "on"}},
    "mailbox.main": {"mailbox.control": {"collect": ("flagged", "full")}},
    "notification.service": {"notification.send": {"send": "ready"}},
    "media_player.main": {"media.control": {"play": ("off", "playing"), "stop": "playing"}},
    "alarm_clock.main": {"alarm.control": {"ring": "idle", "acknowledge": "ringing"}},
    "coffee_maker.main": {"coffee.control": {"start": "idle", "serve": "ready"}},
    "toilet.main": {"toilet.control": {"flush": "unflushed"}},
    "toilet_paper.main": {"supply.control": {"restock": "empty"}},
    "trash_bin.main": {"trash.control": {"empty": ("needs_emptying", "full")}},
    "supply.pellets": {"supply.order": {"place": ("low", "empty")}},
    "supply.beer": {"supply.order": {"place": ("low", "empty")}},
    "emergency_call.service": {"emergency.call": {"call": "ready"}},
}

PARAMETER_SCHEMAS: dict[tuple[str, str], dict[str, Any]] = {
    ("stove.control", "ignite"): {"type": "object", "required": ["level"], "additionalProperties": False, "properties": {"level": {"type": "integer", "minimum": 1, "maximum": 3}}},
    ("stove.control", "set_level"): {"type": "object", "required": ["level"], "additionalProperties": False, "properties": {"level": {"type": "integer", "minimum": 1, "maximum": 3}}},
    ("notification.send", "send"): {
        "type": "object",
        "required": ["message", "channel", "recipients"],
        "additionalProperties": False,
        "properties": {
            "message": {"type": "string", "minLength": 1, "maxLength": 240},
            "channel": {"enum": ["app", "display", "speaker"]},
            "recipients": {
                "type": "array",
                "minItems": 1,
                "uniqueItems": True,
                "items": {"type": "string", "minLength": 1},
            },
        },
    },
    ("media.control", "play"): {"type": "object", "required": ["content"], "additionalProperties": False, "properties": {"content": {"enum": ["music", "news", "television"]}}},
    ("emergency.call", "call"): {"type": "object", "required": ["service"], "additionalProperties": False, "properties": {"service": {"enum": ["emergency", "caregiver"]}}},
    ("supply.order", "place"): {
        "type": "object",
        "required": ["item", "supplier", "quantity", "max_cost"],
        "additionalProperties": False,
        "properties": {
            "item": {"enum": ["pellets", "beer"]},
            "supplier": {"type": "string", "minLength": 1},
            "quantity": {"type": "integer", "minimum": 1, "maximum": 100},
            "max_cost": {"type": "number", "exclusiveMinimum": 0},
        },
    },
}


def _public_interfaces(device_id: str) -> list[dict[str, Any]]:
    interfaces = []
    for capability, operations in ALLOWED_COMMANDS.get(device_id, {}).items():
        for operation in operations:
            interfaces.append({
                "capability": capability,
                "operation": operation,
                "parameters_schema": deepcopy(PARAMETER_SCHEMAS.get((capability, operation), {"type": "object", "maxProperties": 0})),
            })
    return interfaces


def _initial_devices() -> dict[str, dict[str, Any]]:
    return {
        "laundry.washer": {"state": "idle", "remaining_seconds": 0},
        "vacuum.robot": {"state": "docked", "remaining_seconds": 0, "battery_percent": 100},
        "dishwasher.main": {"state": "idle", "remaining_seconds": 0},
        "garage_door.main": {"state": "closed", "remaining_seconds": 0},
        "front_door_lock.main": {"state": "locked", "unlocked_seconds": 0},
        "visitor_intercom.main": {"state": "idle", "ring_seconds": 0, "answered_seconds": 0},
        "fridge_door.main": {"state": "closed", "temperature_c": 4.0, "open_seconds": 0, "alarm_sent": False},
        "stove.main": {"state": "off", "burner_level": None, "on_seconds": 0},
        "mailbox.main": {"state": "empty", "deliveries": 0},
        "bathroom_occupancy.main": {"state": "vacant", "occupied_seconds": 0},
        "notification.service": {"state": "ready", "messages": []},
        "media_player.main": {"state": "off", "content": None},
        "alarm_clock.main": {"state": "idle"},
        "coffee_maker.main": {"state": "idle", "remaining_seconds": 0, "ready_at_step": None, "freshness_remaining_seconds": 0},
        "toilet.main": {"state": "flushed"},
        "toilet_paper.main": {"state": "stocked", "level": 100},
        "trash_bin.main": {"state": "empty", "fill_percent": 0},
        "supply.pellets": {"state": "stocked", "level": 100, "order_id": None, "delivery_remaining_seconds": 0},
        "supply.beer": {"state": "stocked", "level": 100, "order_id": None, "delivery_remaining_seconds": 0},
        "emergency_call.service": {"state": "ready", "calls": []},
    }


def _seeded_ticks(seed: int, stream: str, count: int, lo: int, hi: int) -> list[int]:
    """Deterministically derive ``count`` tick indices in [lo, hi] from the seed."""
    span = hi - lo + 1
    stream_offset = int.from_bytes(sha256(f"workflow-exog|{stream}".encode()).digest()[:8], "big") % span
    return sorted({lo + (seed + stream_offset + index) % span for index in range(count)}) or [lo]


class WorkflowBackend:
    """Deterministic household workflow backend implementing the Backend protocol."""

    def __init__(self, *, private_scenario_type: str | None = None, private_config: dict[str, int] | None = None, private_horizon_seconds: int | None = None):
        self._private_scenario_type = private_scenario_type
        self._private_config = deepcopy(private_config) if private_config is not None else None
        self._private_horizon_seconds = private_horizon_seconds

    def reset(self, episode: EpisodeSpec) -> BackendStep:
        bootstrap = episode.public_bootstrap if isinstance(episode.public_bootstrap, dict) else {}
        supplied_profile = bootstrap.get("public_profile", {})
        if not isinstance(supplied_profile, dict):
            raise ValueError("public_profile must be an object when present")
        self._profile = deepcopy(DEFAULT_PUBLIC_PROFILE)
        self._profile.update(deepcopy(supplied_profile))
        config = self._private_config if self._private_config is not None else bootstrap.get("workflow_config", {})
        if config is not None and not isinstance(config, dict):
            raise ValueError("workflow_config must be an object when present")
        self._config = dict(DEFAULT_CONFIG)
        for key, value in (config or {}).items():
            if key not in DEFAULT_CONFIG or isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"workflow_config.{key!r} must be a positive integer overriding a known duration")
            self._config[key] = value
        published_timing = {
            "coffee_brew_seconds": "coffee_brew_seconds",
            "coffee_freshness_seconds": "coffee_freshness_seconds",
            "entry_relock_seconds": "entry_relock_seconds",
            "gate_reclose_seconds": "gate_reclose_seconds",
            "pellet_delivery_lead_seconds": "pellet_delivery_lead_seconds",
            "beer_delivery_lead_seconds": "beer_delivery_lead_seconds",
            "fridge_alarm_seconds": "fridge_alarm_seconds",
        }
        for profile_key, config_key in published_timing.items():
            if profile_key in supplied_profile:
                value = supplied_profile[profile_key]
                if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                    raise ValueError(f"public_profile.{profile_key} must be a positive integer")
                self._config[config_key] = value
            else:
                self._profile[profile_key] = self._config[config_key]
        self._validate_public_profile()
        self._tick = TICK_SECONDS
        horizon = self._private_horizon_seconds if self._private_horizon_seconds is not None else bootstrap.get("horizon_seconds", HORIZON_SECONDS)
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < self._tick or horizon > MAX_TICKS * self._tick:
            raise ValueError(f"horizon_seconds must be an integer in [{self._tick}, {MAX_TICKS * self._tick}]")
        self._horizon = horizon
        self._episode_id = episode.episode_id
        self._seed = episode.seed
        self._scenario_type = self._private_scenario_type or bootstrap.get("scenario_type", "generic_household_workflow")
        if not isinstance(self._scenario_type, str) or not self._scenario_type:
            raise ValueError("scenario_type must be a non-empty string")
        self._step = 0
        self._devices = _initial_devices()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._completed_tasks: list[dict[str, Any]] = []
        self._household: dict[str, Any] = {
            "occupancy": {"status": "home", "count": 2},
            "keys_present": True,
            "stove_attendance": "attended",
            "bathroom_occupied": False,
            "visitor_identity": None,
            "delivery_identity": None,
            "presented_credential": None,
            "vehicle_identity": None,
            "emergency_type": None,
        }
        self._workflow: dict[str, Any] = {
            "visitor_event_log": [],
            "orders": {},
            "sessions": {},
            "occurrences": {},
            "content_date": self._now().date().isoformat(),
        }
        self._apply_scenario_initial_state()
        self._rules: dict[str, dict[str, Any]] = {}
        self._rule_sequence = 0
        self._events: list[dict[str, Any]] = []
        self._pending_events: list[dict[str, Any]] = []
        self._applied: list[dict[str, Any]] = []
        self._device_runtime: dict[str, int] = {device_id: 0 for device_id in DEVICE_SPECS}
        self._action_cost = 0
        self._ask_log: list[dict[str, Any]] = []
        self._exog = self._build_exogenous_schedule(episode.seed)
        self._phase = "execute"
        return self._step_view(terminal=False)

    def state_digest(self) -> str:
        import rfc8785

        state = {
            "episode_id": self._episode_id,
            "seed": self._seed,
            "scenario_type": self._scenario_type,
            "config": self._config,
            "tick_seconds": self._tick,
            "horizon_seconds": self._horizon,
            "step": self._step,
            "devices": self._devices,
            "rules": self._rules,
            "rule_sequence": self._rule_sequence,
            "tasks": self._tasks,
            "completed_tasks": self._completed_tasks,
            "household": self._household,
            "workflow": self._workflow,
            "public_profile": self._profile,
            "device_runtime_seconds": self._device_runtime,
            "action_cost": self._action_cost,
            "exogenous_pending": {str(tick): events for tick, events in sorted(self._exog.items())},
            "pending_events": self._pending_events,
        }
        return sha256(rfc8785.dumps(state)).hexdigest()

    def execute_atomic(self, action: dict[str, Any]) -> ActionOutcome:
        kind = action["kind"]
        if kind == "wait":
            return ActionOutcome(True, {"status": "accepted", "wait": {"mode": action["mode"]}}, {"status": "accepted"})
        if kind == "ask":
            answer = self._answer(action["question"])
            self._ask_log.append({"question": action["question"], "answer": answer, "at_step": self._step})
            return ActionOutcome(True, {"status": "answered", "answer": answer}, {"answer_source": "deterministic_lookup"})
        if kind == "cancel_rule":
            rule_id = action["rule_id"]
            if rule_id not in self._rules:
                return ActionOutcome(False, {"status": "rejected", "error_code": "UNKNOWN_RULE"}, {"status": "rejected"}, "UNKNOWN_RULE")
            del self._rules[rule_id]
            self._pending_events.append({"type": "rule_cancelled", "rule_id": rule_id, "step": self._step})
            return ActionOutcome(True, {"status": "cancelled", "rule_id": rule_id}, {"status": "cancelled", "rule_id": rule_id})
        if kind == "act":
            commands = action["commands"]
            error = self._validate_commands(commands)
            if error:
                return ActionOutcome(False, {"status": "rejected", "error_code": error}, {"status": "rejected"}, error)
            if not commands:
                return ActionOutcome(True, {"status": "accepted", "applied": [], "action_cost": 0, "cost_unit": COST_UNIT}, {"status": "accepted", "applied": []})
            applied = self._apply_commands(commands, source="agent")
            self._action_cost += COMMAND_COST * len(applied)
            return ActionOutcome(
                True,
                {"status": "accepted", "applied": [item["command_id"] for item in applied], "action_cost": COMMAND_COST * len(applied), "cost_unit": COST_UNIT},
                {"status": "accepted", "applied": deepcopy(applied)},
            )
        error = self._validate_rule(action["rule"])
        if error:
            return ActionOutcome(False, {"status": "rejected", "error_code": error}, {"status": "rejected"}, error)
        rule = deepcopy(action["rule"])
        rule.setdefault("release_at_step", None)
        rule.setdefault("release_commands", [])
        rule["installed_at_step"] = self._step
        rule["sequence"] = self._rule_sequence
        self._rule_sequence += 1
        self._rules[rule["rule_id"]] = rule
        self._pending_events.append({"type": "rule_installed", "rule_id": rule["rule_id"], "step": self._step})
        return ActionOutcome(True, {"status": "installed", "rule_id": rule["rule_id"]}, {"status": "installed", "rule_id": rule["rule_id"]})

    def advance(self, triggering_action: dict[str, Any] | None = None) -> BackendStep:
        triggering_action = triggering_action or {"kind": "act", "commands": []}
        steps = 1
        event_filter: dict[str, Any] | None = None
        if triggering_action["kind"] == "wait":
            mode = triggering_action["mode"]
            remaining = self._horizon - self._step * self._tick
            if mode == "for":
                steps = max(1, ceil(triggering_action["duration_seconds"] / self._tick))
            elif mode == "until":
                target = datetime.fromisoformat(triggering_action["timestamp"].replace("Z", "+00:00"))
                if target.tzinfo is None:
                    raise ValueError("INVALID_WAIT_TIMESTAMP")
                gap = (target - self._now()).total_seconds()
                if gap <= 0:
                    raise ValueError("INVALID_WAIT_TIMESTAMP")
                steps = max(1, ceil(gap / self._tick))
            else:
                steps = max(1, ceil(triggering_action["timeout_seconds"] / self._tick))
                event_filter = triggering_action["event_filter"]
            if remaining <= 0:
                raise ValueError("WAIT_BEYOND_HORIZON")
            steps = min(steps, max(1, ceil(remaining / self._tick)))
        observed_events: list[dict[str, Any]] = []
        observed_applied: list[dict[str, Any]] = []
        for _ in range(steps):
            if self._step * self._tick >= self._horizon:
                break
            self._advance_once()
            observed_events.extend(deepcopy(self._events))
            observed_applied.extend(deepcopy(self._applied))
            if event_filter is not None and self._event_matches(event_filter):
                break
        # A long wait is one Harness transition.  Preserve every causal receipt
        # produced inside it rather than exposing only the final micro-tick.
        self._events = observed_events
        self._applied = observed_applied
        terminal = self._step * self._tick >= self._horizon
        return self._step_view(terminal)

    # ------------------------------------------------------------------
    # Tick processing
    # ------------------------------------------------------------------

    def _advance_once(self) -> None:
        self._step += 1
        self._phase = "tick"
        self._events = self._pending_events
        self._pending_events = []
        self._applied = []
        self._process_rules()
        self._process_exogenous()
        specs_active = DEVICE_SPECS
        for device_id, device in self._devices.items():
            if device["state"] in specs_active[device_id]["active_states"]:
                self._device_runtime[device_id] += self._tick
        for task in self._tasks.values():
            task["elapsed_seconds"] += self._tick
        self._process_dynamics()
        self._phase = "execute"

    def _process_rules(self) -> None:
        fired = [rule for rule in self._rules.values() if rule["fire_at_step"] == self._step]
        fired.sort(key=lambda rule: rule["sequence"])
        for rule in fired:
            applied = self._apply_rule_commands(rule, "commands", "rule_firing")
            self._action_cost += COMMAND_COST * len(applied)
            self._events.append({"type": "rule_fired", "rule_id": rule["rule_id"], "step": self._step})
            if rule["release_at_step"] is None:
                del self._rules[rule["rule_id"]]
                self._events.append({"type": "rule_retired", "rule_id": rule["rule_id"], "step": self._step})
        released = [rule for rule in list(self._rules.values()) if rule["release_at_step"] == self._step]
        released.sort(key=lambda rule: rule["sequence"])
        for rule in released:
            applied = self._apply_rule_commands(rule, "release_commands", "rule_release")
            self._action_cost += COMMAND_COST * len(applied)
            del self._rules[rule["rule_id"]]
            self._events.append({"type": "rule_released", "rule_id": rule["rule_id"], "step": self._step})

    def _apply_rule_commands(self, rule: dict[str, Any], field: str, source: str) -> list[dict[str, Any]]:
        """Apply a rule's commands at firing time; state-invalid ones are skipped deterministically."""
        applied = []
        for command in rule[field]:
            if self._validate_command(command) is not None:
                self._events.append({"type": "rule_command_skipped", "rule_id": rule["rule_id"], "device_id": command.get("device_id", ""), "step": self._step})
                continue
            applied.extend(self._apply_commands([command], source=source, rule_id=rule["rule_id"]))
        return applied

    def _emit(self, event: dict[str, Any]) -> None:
        """Command-induced events: delivered at the tick boundary the policy observes."""
        if self._phase == "tick":
            self._events.append(event)
        else:
            self._pending_events.append(event)

    def _process_exogenous(self) -> None:
        for event in self._exog.get(self._step, []):
            stream = event["stream"]
            if stream == "away_start":
                self._household["occupancy"] = {"status": "away", "count": 0}
                self._events.append({"type": "away_started", "step": self._step})
                self._events.append({"type": "occupancy_became_zero", "step": self._step})
            elif stream == "occupants_return":
                self._household["occupancy"] = {"status": "home", "count": 2}
                self._events.append({"type": "occupants_returned", "step": self._step})
                self._events.append({"type": "authorized_return", "step": self._step})
            elif stream in {"visitor", "unauthorized_visitor"}:
                authorized = stream == "visitor"
                visitor_id = "visitor.authorized" if authorized else "visitor.unknown"
                self._household["visitor_identity"] = visitor_id
                intercom = self._devices["visitor_intercom.main"]
                if intercom["state"] == "idle":
                    intercom["state"] = "ringing"
                    intercom["ring_seconds"] = 0
                    self._start_task("visitor_response", "visitor_intercom.main")
                    ring = {"type": "visitor_rang", "device_id": "visitor_intercom.main", "visitor_id": visitor_id, "authorized": authorized, "step": self._step}
                    self._events.append(ring)
                    self._workflow["visitor_event_log"].append(deepcopy(ring))
                    self._events.append({"type": "visitor_recorded", "visitor_id": visitor_id, "authorized": authorized, "step": self._step})
            elif stream == "mail":
                mailbox = self._devices["mailbox.main"]
                if mailbox["state"] == "empty":
                    mailbox["state"] = "flagged"
                    mailbox["deliveries"] += 1
                    self._events.append({"type": "mail_delivered", "device_id": "mailbox.main", "step": self._step})
                elif mailbox["state"] == "flagged":
                    mailbox["state"] = "full"
                    mailbox["deliveries"] += 1
                    self._events.append({"type": "mailbox_full", "device_id": "mailbox.main", "step": self._step})
            elif stream == "laundry_loaded":
                washer = self._devices["laundry.washer"]
                if washer["state"] == "idle":
                    washer["state"] = "loaded"
                    self._events.append({"type": "laundry_loaded", "device_id": "laundry.washer", "step": self._step})
            elif stream == "fridge_opened":
                fridge = self._devices["fridge_door.main"]
                if fridge["state"] == "closed":
                    fridge["state"] = "open"
                    fridge["open_seconds"] = 0
                    self._events.append({"type": "fridge_door_opened", "device_id": "fridge_door.main", "step": self._step})
            elif stream == "bathroom_enter":
                bathroom = self._devices["bathroom_occupancy.main"]
                if bathroom["state"] == "vacant":
                    bathroom["state"] = "occupied"
                    bathroom["occupied_seconds"] = 0
                    self._household["bathroom_occupied"] = True
                    self._events.append({"type": "bathroom_occupied", "device_id": "bathroom_occupancy.main", "step": self._step})
            elif stream == "bathroom_leave":
                bathroom = self._devices["bathroom_occupancy.main"]
                if bathroom["state"] == "occupied":
                    bathroom["state"] = "vacant"
                    bathroom["occupied_seconds"] = 0
                    self._household["bathroom_occupied"] = False
                    self._events.append({"type": "bathroom_vacated", "device_id": "bathroom_occupancy.main", "step": self._step})
            elif stream == "garage_obstruction":
                garage = self._devices["garage_door.main"]
                if garage["state"] == "closing":
                    garage["state"] = "open"
                    garage["remaining_seconds"] = 0
                    self._events.append({"type": "garage_door_obstruction", "device_id": "garage_door.main", "step": self._step})
            elif stream == "departure":
                self._household["occupancy"] = {"status": "away", "count": 0}
                self._events.append({"type": "occupants_departed", "step": self._step})
                self._events.append({"type": "occupancy_became_zero", "step": self._step})
            elif stream == "failed_entry":
                self._events.append({"type": "failed_entry_attempt", "attempts": 3, "step": self._step})
            elif stream == "expected_delivery":
                delivery_id = "delivery.expected"
                self._household["delivery_identity"] = delivery_id
                payload = {"delivery_id": delivery_id, "authorized": True, "step": self._step}
                self._events.append({"type": "delivery_arrived", **payload})
                self._events.append({"type": "expected_delivery_arrived", **payload})
            elif stream == "unexpected_delivery":
                delivery_id = "delivery.unknown"
                self._household["delivery_identity"] = delivery_id
                self._events.append({"type": "delivery_arrived", "delivery_id": delivery_id, "authorized": False, "step": self._step})
            elif stream == "parked":
                self._events.append({"type": "vehicle_parked", "vehicle_id": "resident.vehicle", "step": self._step})
            elif stream == "curfew":
                self._events.append({"type": "television_curfew_started", "step": self._step})
                self._workflow["sessions"]["television_curfew"] = {"active": True, "started_at_step": self._step}
            elif stream == "curfew_end":
                self._events.append({"type": "television_curfew_ended", "step": self._step})
                self._workflow["sessions"]["television_curfew"] = {"active": False, "ended_at_step": self._step}
            elif stream == "resident_arrival":
                credential = "resident.mobile"
                self._household["presented_credential"] = credential
                payload = {"credential": credential, "authorized": True, "step": self._step}
                self._events.append({"type": "resident_arrived", **payload})
                self._events.append({"type": "authorized_resident_arrived", **payload})
                self._workflow["sessions"]["resident_entry"] = {"active": True, "credential": credential, "started_at_step": self._step}
            elif stream == "unknown_credential":
                credential = "credential.unknown"
                self._household["presented_credential"] = credential
                self._events.append({"type": "unknown_credential_arrived", "credential": credential, "authorized": False, "step": self._step})
            elif stream == "collection_due":
                self._events.append({"type": "bin_collection_due", "step": self._step})
            elif stream == "departure_keys_missing":
                self._household["keys_present"] = False
                self._events.append({"type": "departure_started", "keys_present": False, "step": self._step})
                self._workflow["sessions"]["departure"] = {"active": True, "started_at_step": self._step}
            elif stream == "keys_found":
                self._household["keys_present"] = True
                self._events.append({"type": "keys_present", "step": self._step})
            elif stream == "departure_completed":
                self._events.append({"type": "departure_completed", "step": self._step})
                self._workflow["sessions"]["departure"] = {"active": False, "ended_at_step": self._step}
            elif stream == "departure_cancelled":
                self._events.append({"type": "departure_cancelled", "step": self._step})
                self._workflow["sessions"]["departure"] = {"active": False, "cancelled_at_step": self._step}
            elif stream == "wake_time":
                self._events.append({"type": "intended_wake_time", "step": self._step})
            elif stream == "shower_started":
                self._events.append({"type": "shower_started", "step": self._step})
                self._workflow["sessions"]["shower"] = {"active": True, "started_at_step": self._step}
            elif stream == "shower_ended":
                self._events.append({"type": "shower_ended", "step": self._step})
                self._workflow["sessions"]["shower"] = {"active": False, "ended_at_step": self._step}
            elif stream == "toilet_used":
                self._devices["toilet.main"]["state"] = "unflushed"
                self._events.append({"type": "toilet_use_finished", "step": self._step})
            elif stream == "toilet_paper_empty":
                paper = self._devices["toilet_paper.main"]
                paper.update({"state": "empty", "level": 0})
                self._events.append({"type": "toilet_paper_depleted", "step": self._step})
            elif stream == "emergency":
                self._household["emergency_type"] = "fall"
                payload = {"emergency_type": "fall", "supported": True, "step": self._step}
                self._events.append({"type": "emergency_detected", **payload})
                self._events.append({"type": "supported_emergency_detected", **payload})
            elif stream == "unsupported_emergency":
                self._household["emergency_type"] = "pet_motion"
                self._events.append({"type": "unsupported_alarm", "emergency_type": "pet_motion", "supported": False, "step": self._step})
            elif stream == "authorized_vehicle":
                self._household["vehicle_identity"] = "resident.vehicle"
                payload = {"vehicle_id": "resident.vehicle", "authorized": True, "step": self._step}
                self._events.append({"type": "vehicle_arrived", **payload})
                self._events.append({"type": "authorized_vehicle_arrived", **payload})
                self._workflow["sessions"]["vehicle_entry"] = {"active": True, "vehicle_id": "resident.vehicle", "started_at_step": self._step}
            elif stream == "unauthorized_vehicle":
                self._household["vehicle_identity"] = "vehicle.unknown"
                self._events.append({"type": "unauthorized_vehicle_arrived", "vehicle_id": "vehicle.unknown", "authorized": False, "step": self._step})
            elif stream == "stove_unattended":
                self._household["stove_attendance"] = "unattended"
                self._events.append({"type": "stove_became_unattended", "step": self._step})
            elif stream == "stove_attendance_restored":
                self._household["stove_attendance"] = "attended"
                self._events.append({"type": "stove_attendance_restored", "step": self._step})
            elif stream == "weekly_due":
                occurrence_id = f"week.{self._now().date().isoformat()}"
                self._workflow["occurrences"][occurrence_id] = {"status": "due", "due_at_step": self._step}
                self._events.append({"type": "weekly_cleaning_due", "occurrence_id": occurrence_id, "step": self._step})
            elif stream == "unauthorized_access_attempt":
                self._events.append({"type": "unauthorized_access_attempt", "scope": "front_door", "blocked": True, "step": self._step})
            elif stream == "television_resume_attempt":
                media = self._devices["media_player.main"]
                media.update({"state": "playing", "content": "television"})
                self._events.append({"type": "media_resume_attempt", "content": "television", "step": self._step})

    def _process_dynamics(self) -> None:
        cfg = self._config
        tick = self._tick
        self._workflow["content_date"] = self._now().date().isoformat()
        washer = self._devices["laundry.washer"]
        if washer["state"] == "washing":
            washer["remaining_seconds"] = max(0, washer["remaining_seconds"] - tick)
            if washer["remaining_seconds"] == 0:
                washer["state"] = "done"
                self._complete_task("laundry_cycle", "cycle_finished")
                self._events.append({"type": "laundry_cycle_finished", "device_id": "laundry.washer", "step": self._step})
        dishwasher = self._devices["dishwasher.main"]
        if dishwasher["state"] == "running":
            dishwasher["remaining_seconds"] = max(0, dishwasher["remaining_seconds"] - tick)
            if dishwasher["remaining_seconds"] == 0:
                dishwasher["state"] = "done"
                self._complete_task("dish_cycle", "cycle_finished")
                self._events.append({"type": "dishwasher_cycle_finished", "device_id": "dishwasher.main", "step": self._step})
        vacuum = self._devices["vacuum.robot"]
        if vacuum["state"] == "cleaning":
            vacuum["remaining_seconds"] = max(0, vacuum["remaining_seconds"] - tick)
            vacuum["battery_percent"] = max(0, vacuum["battery_percent"] - cfg["vacuum_drain_percent_per_tick"])
            if vacuum["battery_percent"] <= cfg["vacuum_low_battery"]:
                vacuum["state"] = "returning"
                vacuum["remaining_seconds"] = cfg["vacuum_return_seconds"]
                self._events.append({"type": "vacuum_low_battery", "device_id": "vacuum.robot", "step": self._step})
            elif vacuum["remaining_seconds"] == 0:
                vacuum["state"] = "returning"
                vacuum["remaining_seconds"] = cfg["vacuum_return_seconds"]
                self._events.append({"type": "vacuum_cycle_finished", "device_id": "vacuum.robot", "step": self._step})
        elif vacuum["state"] == "returning":
            vacuum["remaining_seconds"] = max(0, vacuum["remaining_seconds"] - tick)
            if vacuum["remaining_seconds"] == 0:
                vacuum["state"] = "docked"
                self._complete_task("vacuum_clean", "docked")
                self._events.append({"type": "vacuum_docked", "device_id": "vacuum.robot", "step": self._step})
                due = next((key for key, value in self._workflow["occurrences"].items() if value.get("status") == "due"), None)
                if due is not None:
                    self._workflow["occurrences"][due].update({"status": "completed", "completed_at_step": self._step})
        elif vacuum["state"] == "docked":
            vacuum["battery_percent"] = min(100, vacuum["battery_percent"] + cfg["vacuum_charge_percent_per_tick"])
        garage = self._devices["garage_door.main"]
        if garage["state"] == "opening":
            garage["remaining_seconds"] = max(0, garage["remaining_seconds"] - tick)
            if garage["remaining_seconds"] == 0:
                garage["state"] = "open"
                self._events.append({"type": "garage_door_opened", "device_id": "garage_door.main", "step": self._step})
                self._workflow["sessions"].setdefault("garage_access", {}).update({"state": "open", "opened_at_step": self._step})
        elif garage["state"] == "closing":
            garage["remaining_seconds"] = max(0, garage["remaining_seconds"] - tick)
            if garage["remaining_seconds"] == 0:
                garage["state"] = "closed"
                self._events.append({"type": "garage_door_closed", "device_id": "garage_door.main", "step": self._step})
                self._workflow["sessions"].setdefault("garage_access", {}).update({"state": "closed", "closed_at_step": self._step})
        lock = self._devices["front_door_lock.main"]
        if lock["state"] == "unlocked" and self._scenario_type not in {"departure_lockdown", "unoccupied_home_security"}:
            lock["unlocked_seconds"] += tick
            relock_seconds = cfg["entry_relock_seconds"] if self._scenario_type == "keyless_resident_entry" else cfg["lock_autolock_seconds"]
            if lock["unlocked_seconds"] >= relock_seconds:
                lock["state"] = "locked"
                lock["unlocked_seconds"] = 0
                self._events.append({"type": "front_door_auto_lock_engaged", "device_id": "front_door_lock.main", "step": self._step})
                self._events.append({"type": "front_door_locked", "device_id": "front_door_lock.main", "automatic": True, "step": self._step})
                self._workflow["sessions"].setdefault("resident_entry", {}).update({"active": False, "relocked_at_step": self._step})
        intercom = self._devices["visitor_intercom.main"]
        if intercom["state"] == "ringing":
            intercom["ring_seconds"] += tick
            if intercom["ring_seconds"] >= cfg["intercom_ring_timeout_seconds"]:
                intercom["state"] = "idle"
                intercom["ring_seconds"] = 0
                self._complete_task("visitor_response", "missed")
                self._events.append({"type": "visitor_missed", "device_id": "visitor_intercom.main", "step": self._step})
        elif intercom["state"] == "answered":
            intercom["answered_seconds"] += tick
            if intercom["answered_seconds"] >= cfg["intercom_answered_clear_seconds"]:
                intercom["state"] = "idle"
                intercom["answered_seconds"] = 0
                self._events.append({"type": "visitor_interaction_ended", "device_id": "visitor_intercom.main", "step": self._step})
        fridge = self._devices["fridge_door.main"]
        if fridge["state"] == "open":
            fridge["open_seconds"] += tick
            fridge["temperature_c"] = round(fridge["temperature_c"] + 0.1 * tick / 60, 4)
            if fridge["open_seconds"] >= cfg["fridge_alarm_seconds"] and not fridge["alarm_sent"]:
                fridge["alarm_sent"] = True
                self._events.append({"type": "fridge_door_alarm", "device_id": "fridge_door.main", "step": self._step})
        else:
            fridge["temperature_c"] = round(max(4.0, fridge["temperature_c"] - 0.05 * tick / 60), 4)
            fridge["open_seconds"] = 0
            fridge["alarm_sent"] = False
        stove = self._devices["stove.main"]
        if stove["state"] == "on":
            hazard_clock_active = (
                self._scenario_type != "unattended_stove_guard"
                or self._household["stove_attendance"] == "unattended"
            )
            if hazard_clock_active:
                stove["on_seconds"] += tick
            if hazard_clock_active and stove["on_seconds"] >= cfg["stove_hazard_seconds"]:
                stove["state"] = "off"
                stove["burner_level"] = None
                stove["on_seconds"] = 0
                self._complete_task("cooking", "safety_shutoff")
                self._events.append({"type": "stove_hazard_shutoff", "device_id": "stove.main", "step": self._step})
        bathroom = self._devices["bathroom_occupancy.main"]
        if bathroom["state"] == "occupied":
            bathroom["occupied_seconds"] += tick
        coffee = self._devices["coffee_maker.main"]
        if coffee["state"] == "brewing":
            coffee["remaining_seconds"] = max(0, coffee["remaining_seconds"] - tick)
            if coffee["remaining_seconds"] == 0:
                coffee["state"] = "ready"
                coffee["ready_at_step"] = self._step
                coffee["freshness_remaining_seconds"] = cfg["coffee_freshness_seconds"]
                self._complete_task("coffee_brew", "ready")
                self._events.append({"type": "coffee_ready", "device_id": "coffee_maker.main", "step": self._step})
        elif coffee["state"] == "ready":
            coffee["freshness_remaining_seconds"] = max(0, coffee["freshness_remaining_seconds"] - tick)
            if coffee["freshness_remaining_seconds"] == 0:
                coffee.update({"state": "expired", "ready_at_step": None})
                self._events.append({"type": "coffee_expired", "device_id": "coffee_maker.main", "step": self._step})
        alarm = self._devices["alarm_clock.main"]
        if alarm["state"] == "ringing":
            alarm.setdefault("ringing_seconds", 0)
            alarm["ringing_seconds"] += tick
            if alarm["ringing_seconds"] >= cfg["alarm_timeout_seconds"]:
                alarm.update({"state": "idle", "ringing_seconds": 0})
                self._events.append({"type": "alarm_timeout", "device_id": "alarm_clock.main", "step": self._step})
        for item, device_id in (("pellets", "supply.pellets"), ("beer", "supply.beer")):
            supply = self._devices[device_id]
            if self._step % max(1, cfg["inventory_consumption_seconds"] // tick) == 0 and supply["level"] > 0:
                supply["level"] = max(0, supply["level"] - cfg["inventory_consumption_units"])
                if supply["level"] == 0:
                    supply["state"] = "empty" if supply["state"] != "ordered" else "ordered"
                    self._events.append({"type": "inventory_stockout", "item": item, "step": self._step})
                elif supply["state"] == "stocked" and supply["level"] <= int(self._profile[f"{item[:-1] if item == 'pellets' else item}_reorder_point"]):
                    supply["state"] = "low"
            if supply["state"] == "ordered":
                supply["delivery_remaining_seconds"] = max(0, supply["delivery_remaining_seconds"] - tick)
                order = self._workflow["orders"].get(item)
                if order is not None:
                    order["delivery_remaining_seconds"] = supply["delivery_remaining_seconds"]
                if supply["delivery_remaining_seconds"] == 0:
                    quantity = int(order["quantity"]) if order else 1
                    supply.update({"state": "stocked", "level": min(100, supply["level"] + quantity), "order_id": None})
                    if order is not None:
                        order.update({"status": "delivered", "delivered_at_step": self._step})
                    self._events.append({"type": "supply_delivered", "item": item, "quantity": quantity, "step": self._step})

    # ------------------------------------------------------------------
    # Commands, rules, validation
    # ------------------------------------------------------------------

    def _apply_commands(self, commands: list[dict[str, Any]], *, source: str, rule_id: str | None = None) -> list[dict[str, Any]]:
        applied = []
        for index, command in enumerate(commands):
            self._apply_command(command)
            record = {
                "command_id": f"{source}.{self._step}.{len(self._applied)}",
                "command": deepcopy(command),
                "source": source,
                "rule_id": rule_id,
                "applied_at_step": self._step,
                "action_cost": COMMAND_COST,
                "cost_unit": COST_UNIT,
            }
            self._applied.append(record)
            applied.append(record)
        return applied

    def _apply_command(self, command: dict[str, Any]) -> None:
        device_id = command["device_id"]
        operation = command["operation"]
        device = self._devices[device_id]
        cfg = self._config
        if device_id == "laundry.washer":
            if operation == "load":
                device["state"] = "loaded"
            elif operation == "start":
                device["state"] = "washing"
                device["remaining_seconds"] = cfg["washer_cycle_seconds"]
                self._start_task("laundry_cycle", device_id)
            elif operation == "unload":
                device["state"] = "idle"
        elif device_id == "dishwasher.main":
            if operation == "load":
                device["state"] = "loaded"
            elif operation == "start":
                device["state"] = "running"
                device["remaining_seconds"] = cfg["dishwasher_cycle_seconds"]
                self._start_task("dish_cycle", device_id)
            elif operation == "unload":
                device["state"] = "idle"
        elif device_id == "vacuum.robot":
            if operation == "start_cleaning":
                device["state"] = "cleaning"
                device["remaining_seconds"] = cfg["vacuum_clean_seconds"]
                self._start_task("vacuum_clean", device_id)
            elif operation == "dock":
                device["state"] = "returning"
                device["remaining_seconds"] = cfg["vacuum_return_seconds"]
        elif device_id == "garage_door.main":
            device["state"] = "opening" if operation == "open" else "closing"
            device["remaining_seconds"] = cfg["garage_move_seconds"]
            session = self._workflow["sessions"].setdefault("garage_access", {})
            session.update({"state": "opening" if operation == "open" else "reclosing", "commanded_at_step": self._step})
        elif device_id == "front_door_lock.main":
            device["state"] = "locked" if operation == "lock" else "unlocked"
            device["unlocked_seconds"] = 0
            self._emit({"type": "front_door_locked" if operation == "lock" else "front_door_unlocked", "device_id": device_id, "step": self._step})
        elif device_id == "visitor_intercom.main":
            device["state"] = "answered"
            device["answered_seconds"] = 0
            self._complete_task("visitor_response", "answered")
            self._emit({"type": "visitor_answered", "device_id": device_id, "step": self._step})
        elif device_id == "fridge_door.main":
            device["state"] = "open" if operation == "open" else "closed"
            if operation == "open":
                device["open_seconds"] = 0
                device["alarm_sent"] = False
            else:
                self._emit({"type": "fridge_door_closed", "device_id": device_id, "step": self._step})
        elif device_id == "stove.main":
            if operation == "extinguish":
                device["state"] = "off"
                device["burner_level"] = None
                device["on_seconds"] = 0
                self._complete_task("cooking", "extinguished")
                self._emit({"type": "stove_extinguished", "device_id": device_id, "step": self._step})
                return
            if operation == "ignite":
                device["state"] = "on"
                device["burner_level"] = command["parameters"]["level"]
                device["on_seconds"] = 0
                self._start_task("cooking", device_id)
            else:
                device["burner_level"] = command["parameters"]["level"]
        elif device_id == "mailbox.main":
            device["state"] = "empty"
            self._emit({"type": "mail_collected", "device_id": device_id, "step": self._step})
        elif device_id == "notification.service":
            message = {
                "message": command["parameters"]["message"],
                "channel": command["parameters"]["channel"],
                "recipients": deepcopy(command["parameters"]["recipients"]),
                "sent_at_step": self._step,
            }
            device["messages"].append(message)
            self._emit({"type": "notification_sent", "device_id": device_id, **message})
        elif device_id == "media_player.main":
            if operation == "stop":
                device.update({"state": "off", "content": None})
                self._emit({"type": "media_stopped", "device_id": device_id, "step": self._step})
            else:
                device.update({"state": "playing", "content": command["parameters"]["content"]})
                event = {"type": "media_started", "device_id": device_id, "content": device["content"], "step": self._step}
                if device["content"] == "news":
                    event["content_date"] = self._workflow["content_date"]
                self._emit(event)
        elif device_id == "alarm_clock.main":
            device["state"] = "ringing" if operation == "ring" else "idle"
            device["ringing_seconds"] = 0
            self._emit({"type": "alarm_sounded" if operation == "ring" else "alarm_acknowledged", "device_id": device_id, "step": self._step})
        elif device_id == "coffee_maker.main":
            if operation == "start":
                device.update({"state": "brewing", "remaining_seconds": self._config["coffee_brew_seconds"]})
                self._start_task("coffee_brew", device_id)
            else:
                device.update({"state": "idle", "remaining_seconds": 0, "ready_at_step": None, "freshness_remaining_seconds": 0})
                self._emit({"type": "coffee_served", "device_id": device_id, "step": self._step})
        elif device_id == "toilet.main":
            device["state"] = "flushed"
            self._emit({"type": "toilet_flushed", "device_id": device_id, "step": self._step})
        elif device_id == "toilet_paper.main":
            device.update({"state": "stocked", "level": 100})
            self._emit({"type": "supply_restocked", "device_id": device_id, "step": self._step})
        elif device_id in {"supply.pellets", "supply.beer"}:
            item = "pellets" if device_id.endswith("pellets") else "beer"
            params = command["parameters"]
            lead = cfg["pellet_delivery_lead_seconds" if item == "pellets" else "beer_delivery_lead_seconds"]
            order_id = sha256(f"order|{self._episode_id}|{item}|{self._step}".encode()).hexdigest()[:16]
            device.update({"state": "ordered", "order_id": order_id, "delivery_remaining_seconds": lead})
            order = {
                "order_id": order_id,
                "item": item,
                "supplier": params["supplier"],
                "quantity": params["quantity"],
                "max_cost": params["max_cost"],
                "status": "accepted",
                "accepted_at_step": self._step,
                "delivery_remaining_seconds": lead,
            }
            self._workflow["orders"][item] = order
            self._emit({"type": "supply_order_accepted", **deepcopy(order), "step": self._step})
        elif device_id == "trash_bin.main":
            device.update({"state": "empty", "fill_percent": 0})
            self._emit({"type": "trash_bin_emptied", "device_id": device_id, "step": self._step})
        elif device_id == "emergency_call.service":
            call = {"service": command["parameters"]["service"], "called_at_step": self._step}
            device["calls"].append(call)
            self._emit({"type": "emergency_call_placed", "device_id": device_id, **call})
            self._emit({"type": "emergency_call_accepted", "device_id": device_id, **call})

    def _validate_commands(self, commands: list[dict[str, Any]], *, check_state: bool = True) -> str | None:
        if len(commands) > MAX_ACTIONS_PER_TICK:
            return "TOO_MANY_COMMANDS"
        device_ids = [command.get("device_id") for command in commands if isinstance(command, dict)]
        if len(device_ids) != len(set(device_ids)):
            return "DUPLICATE_DEVICE_COMMAND"
        for command in commands:
            error = self._validate_command(command, check_state=check_state)
            if error:
                return error
        return None

    def _validate_command(self, command: dict[str, Any], *, check_state: bool = True) -> str | None:
        device_id = command.get("device_id")
        if device_id not in DEVICE_SPECS:
            return "UNKNOWN_DEVICE"
        device = self._devices[device_id]
        capability = command.get("capability")
        operation = command.get("operation")
        params = command.get("parameters")
        if not isinstance(params, dict):
            return "UNSUPPORTED_COMMAND"
        allowed = ALLOWED_COMMANDS
        if device_id == "stove.main":
            ops = allowed[device_id]["stove.control"]
            if capability != "stove.control" or operation not in ops:
                return "UNSUPPORTED_COMMAND"
            required = ops[operation]
            if operation != "extinguish":
                level = params.get("level")
                if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= 3:
                    return "PARAMETER_OUT_OF_RANGE"
            if check_state and device["state"] != required:
                return "INVALID_STATE"
            return None
        spec = allowed.get(device_id, {})
        if capability not in spec or operation not in spec.get(capability, {}):
            return "UNSUPPORTED_COMMAND"
        if device_id == "vacuum.robot" and operation == "start_cleaning":
            if check_state and device["battery_percent"] < self._config["vacuum_min_battery_to_start"]:
                return "BATTERY_TOO_LOW"
        if device_id == "notification.service":
            if set(params) != {"message", "channel", "recipients"}:
                return "INVALID_PARAMETERS"
            if not isinstance(params["message"], str) or not params["message"].strip() or len(params["message"]) > 240:
                return "INVALID_PARAMETERS"
            if params["channel"] not in {"app", "display", "speaker"}:
                return "INVALID_PARAMETERS"
            recipients = params["recipients"]
            if (
                not isinstance(recipients, list)
                or not recipients
                or len(set(recipients)) != len(recipients)
                or any(not isinstance(item, str) or not item for item in recipients)
            ):
                return "INVALID_PARAMETERS"
        elif device_id == "media_player.main" and operation == "play":
            if set(params) != {"content"} or params["content"] not in {"music", "news", "television"}:
                return "INVALID_PARAMETERS"
        elif device_id == "emergency_call.service":
            if set(params) != {"service"} or params["service"] not in {"emergency", "caregiver"}:
                return "INVALID_PARAMETERS"
            if check_state and self._scenario_type == "supported_emergency_call":
                emergency_type = self._household.get("emergency_type")
                if emergency_type not in set(self._profile["supported_emergency_types"]):
                    return "UNAUTHORIZED_EMERGENCY_TYPE"
        elif device_id in {"supply.pellets", "supply.beer"}:
            if set(params) != {"item", "supplier", "quantity", "max_cost"}:
                return "INVALID_PARAMETERS"
            item = "pellets" if device_id.endswith("pellets") else "beer"
            if params.get("item") != item:
                return "INVALID_PARAMETERS"
            quantity = params.get("quantity")
            max_cost = params.get("max_cost")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 100:
                return "PARAMETER_OUT_OF_RANGE"
            if isinstance(max_cost, bool) or not isinstance(max_cost, (int, float)) or max_cost <= 0:
                return "PARAMETER_OUT_OF_RANGE"
            suppliers = set(self._profile[f"{item[:-1] if item == 'pellets' else item}_suppliers"])
            budget = self._profile[f"{item[:-1] if item == 'pellets' else item}_budget"]
            if params.get("supplier") not in suppliers or max_cost > budget:
                return "PURCHASE_NOT_AUTHORIZED"
        elif params:
            return "INVALID_PARAMETERS"
        if not check_state:
            return None
        if device_id == "garage_door.main" and operation == "open":
            identity_checks = {
                "expected_delivery_gate_and_notice": ("delivery_identity", "expected_delivery_allowlist"),
                "remote_visitor_gate_access": ("visitor_identity", "visitor_allowlist"),
                "authorized_vehicle_gate_entry": ("vehicle_identity", "authorized_vehicle_ids"),
            }
            identity_check = identity_checks.get(self._scenario_type)
            if identity_check is not None:
                field, profile_key = identity_check
                if self._household.get(field) not in set(self._profile[profile_key]):
                    return "IDENTITY_NOT_AUTHORIZED"
        if device_id == "front_door_lock.main" and operation == "unlock" and self._scenario_type == "keyless_resident_entry":
            if self._household.get("presented_credential") not in set(self._profile["resident_credential_allowlist"]):
                return "IDENTITY_NOT_AUTHORIZED"
        required = allowed[device_id][capability][operation]
        states = (required,) if isinstance(required, str) else tuple(required)
        if device["state"] not in states:
            return "INVALID_STATE"
        return None

    def _validate_rule(self, rule: dict[str, Any]) -> str | None:
        rule_id = rule.get("rule_id")
        if not isinstance(rule_id, str) or not rule_id:
            return "INVALID_RULE"
        if rule_id in self._rules:
            return "RULE_ALREADY_INSTALLED"
        fire_at = rule.get("fire_at_step")
        if isinstance(fire_at, bool) or not isinstance(fire_at, int) or fire_at <= self._step or fire_at > MAX_TICKS:
            return "INVALID_RULE"
        release_at = rule.get("release_at_step")
        if release_at is not None:
            if isinstance(release_at, bool) or not isinstance(release_at, int) or release_at <= fire_at or release_at > MAX_TICKS:
                return "INVALID_RULE"
        commands = rule.get("commands")
        if not isinstance(commands, list) or not commands:
            return "INVALID_RULE"
        release_commands = rule.get("release_commands", [])
        if release_at is not None and (not isinstance(release_commands, list) or not release_commands):
            return "INVALID_RULE"
        if release_at is None and release_commands:
            return "INVALID_RULE"
        checked = list(commands) + (list(release_commands) if release_at is not None else [])
        if len(checked) > MAX_ACTIONS_PER_TICK:
            return "INVALID_RULE"
        for command in checked:
            if not isinstance(command, dict):
                return "INVALID_RULE"
            error = self._validate_command(command, check_state=False)
            if error:
                return f"RULE_COMMAND_{error}"
        return None

    # ------------------------------------------------------------------
    # Tasks, events, helpers
    # ------------------------------------------------------------------

    def _start_task(self, name: str, device_id: str) -> None:
        self._tasks[name] = {"task": name, "device_id": device_id, "started_at_step": self._step, "elapsed_seconds": 0}

    def _complete_task(self, name: str, outcome: str) -> None:
        task = self._tasks.pop(name, None)
        if task is not None:
            self._completed_tasks.append({**task, "outcome": outcome, "ended_at_step": self._step})

    def _event_matches(self, event_filter: dict[str, Any]) -> bool:
        return any(all(event.get(key) == value for key, value in event_filter.items()) for event in self._events)

    def _build_exogenous_schedule(self, seed: int) -> dict[int, list[dict[str, str]]]:
        horizon_ticks = self._horizon // self._tick
        schedule: dict[int, list[dict[str, str]]] = {}

        def add(tick: int, stream: str) -> None:
            instance_id = sha256(f"workflow-instance|{seed}|{stream}|{tick}".encode()).hexdigest()[:16]
            schedule.setdefault(tick, []).append({"stream": stream, "instance_id": instance_id})

        def pick(stream: str, lo: int = 2, hi: int | None = None) -> int:
            upper = hi if hi is not None else max(lo, min(horizon_ticks - 4, 30))
            return _seeded_ticks(seed, f"{self._scenario_type}:{stream}", 1, lo, max(lo, upper))[0]

        if horizon_ticks >= 8:
            scenario = self._scenario_type
            away_scenarios = {"away_intercom_notification", "away_visitor_monitoring", "unoccupied_visitor_acknowledgement", "remote_visitor_gate_access"}
            if scenario in away_scenarios:
                add(1, "away_start")
                visitor_tick = pick("visitor", 3, max(3, horizon_ticks - 8))
                add(visitor_tick, "visitor")
                if scenario == "remote_visitor_gate_access" and horizon_ticks >= 12:
                    add(max(visitor_tick + 4, horizon_ticks // 2), "unauthorized_visitor")
                add(max(visitor_tick + 6, horizon_ticks - 2), "occupants_return")
            if scenario == "mail_arrival_notification":
                add(_seeded_ticks(seed, "mail", 1, 1, max(2, horizon_ticks - 2))[0], "mail")
            if scenario == "bathroom_occupancy_indicator":
                enter = _seeded_ticks(seed, "bathroom", 1, 1, max(1, horizon_ticks // 2))[0]
                add(enter, "bathroom_enter")
                leave = enter + max(1, self._config["bathroom_occupancy_seconds"] // self._tick)
                if leave < horizon_ticks:
                    add(leave, "bathroom_leave")
            if scenario in {"departure_lockdown", "unoccupied_home_security"}:
                departure_tick = pick("departure", 2, max(2, min(horizon_ticks // 3, 30)))
                add(departure_tick, "departure")
                if scenario == "unoccupied_home_security" and horizon_ticks >= 12:
                    add(max(departure_tick + 4, horizon_ticks // 2), "unauthorized_access_attempt")
                add(max(departure_tick + 6, horizon_ticks - 2), "occupants_return")
            if scenario == "failed_entry_notification":
                add(_seeded_ticks(seed, "failed_entry", 1, 1, max(1, horizon_ticks // 3))[0], "failed_entry")
            if scenario == "expected_delivery_gate_and_notice":
                delivery_tick = pick("expected_delivery", 2, max(2, min(horizon_ticks // 3, 30)))
                add(delivery_tick, "expected_delivery")
                if horizon_ticks >= 12:
                    add(max(delivery_tick + 5, horizon_ticks // 2), "unexpected_delivery")
            if scenario == "post_parking_garage_closure":
                parked_tick = pick("parked", 2, max(2, min(horizon_ticks // 3, 30)))
                add(parked_tick, "parked")
                add(parked_tick + 1, "garage_obstruction")
            if scenario == "laundry_backlog_management":
                add(pick("laundry_loaded"), "laundry_loaded")
            if scenario == "fridge_door_left_open":
                add(pick("fridge_opened"), "fridge_opened")
            scenario_stream = {
                "full_bin_collection_notice": "collection_due",
                "post_use_toilet_flush": "toilet_used",
                "toilet_paper_depletion_notice": "toilet_paper_empty",
            }.get(scenario)
            if scenario_stream:
                add(_seeded_ticks(seed, scenario_stream, 1, 2, max(2, horizon_ticks // 2))[0], scenario_stream)
            if scenario == "weekly_floor_cleaning":
                due_tick = self._timestamp_tick(str(self._profile["weekly_cleaning_due_at"]), fallback=2)
                if 1 <= due_tick <= horizon_ticks:
                    add(due_tick, "weekly_due")
            if scenario == "unattended_stove_guard":
                unattended_tick = pick("stove_unattended", 2, max(2, min(horizon_ticks // 3, 30)))
                add(unattended_tick, "stove_unattended")
                add(min(horizon_ticks - 1, unattended_tick + 10), "stove_attendance_restored")
            if scenario == "television_curfew":
                start_tick = self._next_local_clock_tick("23:30")
                end_tick = self._next_local_clock_tick(str(self._profile["television_curfew_end"]), after_tick=start_tick)
                if start_tick <= horizon_ticks:
                    add(start_tick, "curfew")
                    resume_tick = pick("television_resume_attempt", start_tick + 2, min(horizon_ticks - 1, start_tick + 30))
                    if resume_tick <= horizon_ticks:
                        add(resume_tick, "television_resume_attempt")
                if end_tick <= horizon_ticks:
                    add(end_tick, "curfew_end")
            if scenario == "keyless_resident_entry":
                arrival_tick = pick("resident_arrival", 2, max(2, min(horizon_ticks // 3, 30)))
                add(arrival_tick, "resident_arrival")
                add(max(arrival_tick + 5, horizon_ticks // 2), "unknown_credential")
            if scenario == "departure_key_reminder":
                departure_tick = pick("departure_keys_missing", 2, max(2, min(horizon_ticks // 3, 30)))
                add(departure_tick, "departure_keys_missing")
                add(min(horizon_ticks - 2, departure_tick + 4), "keys_found")
                add(min(horizon_ticks - 1, departure_tick + 8), "departure_cancelled" if seed % 2 else "departure_completed")
            if scenario in {"intended_wake_alarm", "coffee_ready_at_wake"}:
                wake_tick = self._timestamp_tick(str(self._profile["intended_wake_at"]), fallback=15)
                add(min(horizon_ticks - 1, max(2, wake_tick)), "wake_time")
            if scenario in {"shower_music_availability", "shower_news_delivery"}:
                if scenario == "shower_music_availability":
                    shower_start = pick("shower_started", 2, max(2, min(horizon_ticks // 3, 30)))
                else:
                    period_start = self._next_local_period_tick(self._profile["morning_period"])
                    shower_start = pick("shower_started", period_start, min(horizon_ticks - 2, period_start + 30))
                shower_end = shower_start + max(1, self._config["shower_session_seconds"] // self._tick)
                if shower_start < horizon_ticks:
                    add(shower_start, "shower_started")
                if shower_end < horizon_ticks:
                    add(shower_end, "shower_ended")
            if scenario == "supported_emergency_call":
                emergency_tick = pick("emergency", 2, max(2, min(horizon_ticks // 3, 30)))
                add(emergency_tick, "emergency")
                add(max(emergency_tick + 5, horizon_ticks // 2), "unsupported_emergency")
            if scenario == "authorized_vehicle_gate_entry":
                arrival_tick = pick("authorized_vehicle", 2, max(2, min(horizon_ticks // 3, 30)))
                add(arrival_tick, "authorized_vehicle")
                add(max(arrival_tick + 5, horizon_ticks // 2), "unauthorized_vehicle")
        return schedule

    def _timestamp_tick(self, value: str, *, fallback: int) -> int:
        try:
            target = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(1, ceil((target.astimezone(timezone.utc) - START).total_seconds() / self._tick))
        except (TypeError, ValueError):
            return fallback

    def _validate_public_profile(self) -> None:
        list_fields = {
            "authorized_vehicle_ids", "beer_suppliers", "cleaning_zone_scope",
            "expected_delivery_allowlist", "notification_recipients", "pellet_suppliers",
            "resident_credential_allowlist", "security_scope", "supported_emergency_types",
            "visitor_allowlist",
        }
        for field in list_fields:
            value = self._profile.get(field)
            if not isinstance(value, list) or not value or any(not isinstance(item, str) or not item for item in value):
                raise ValueError(f"public_profile.{field} must be a non-empty string list")
        for field in {"beer_budget", "beer_order_quantity", "pellet_budget", "pellet_order_quantity"}:
            value = self._profile.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise ValueError(f"public_profile.{field} must be positive")
        if not isinstance(self._profile.get("shower_music_opt_in"), bool):
            raise ValueError("public_profile.shower_music_opt_in must be boolean")
        try:
            ZoneInfo(str(self._profile["local_timezone"]))
        except ZoneInfoNotFoundError as exc:
            raise ValueError("public_profile.local_timezone is unknown") from exc

    def _next_local_clock_tick(self, clock: str, *, after_tick: int = 0) -> int:
        try:
            hour, minute = (int(part) for part in clock.split(":"))
            zone = ZoneInfo(str(self._profile["local_timezone"]))
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            raise ValueError("invalid local clock or local_timezone")
        after = START + timedelta(seconds=after_tick * self._tick)
        local_after = after.astimezone(zone)
        target = local_after.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if target <= local_after:
            target += timedelta(days=1)
        return ceil((target.astimezone(timezone.utc) - START).total_seconds() / self._tick)

    def _next_local_period_tick(self, period: Any) -> int:
        if not isinstance(period, dict) or not isinstance(period.get("start"), str):
            raise ValueError("morning_period must contain a local start time")
        return self._next_local_clock_tick(period["start"])

    def _apply_scenario_initial_state(self) -> None:
        scenario = self._scenario_type
        if scenario == "laundry_completion_notification":
            self._devices["laundry.washer"].update({"state": "washing", "remaining_seconds": self._config["washer_cycle_seconds"]})
            self._start_task("laundry_cycle", "laundry.washer")
        elif scenario == "laundry_backlog_management":
            self._devices["laundry.washer"]["state"] = "idle"
        elif scenario == "fridge_door_left_open":
            self._devices["fridge_door.main"]["state"] = "closed"
        elif scenario == "unattended_stove_guard":
            stove = self._devices["stove.main"]
            # The unattended transition must leave a real intervention window;
            # otherwise the same tick can trigger the backend safety shutoff and
            # the Episode has no feasible Agent action.
            stove.update({"state": "on", "burner_level": 2, "on_seconds": 0})
            self._household["stove_attendance"] = "attended"
        elif scenario in {"departure_lockdown", "unoccupied_home_security"}:
            self._devices["front_door_lock.main"]["state"] = "unlocked"
        elif scenario in {"expected_delivery_gate_and_notice", "remote_visitor_gate_access"}:
            self._devices["garage_door.main"]["state"] = "closed"
        elif scenario == "post_parking_garage_closure":
            self._devices["garage_door.main"]["state"] = "open"
        elif scenario == "television_curfew":
            self._devices["media_player.main"].update({"state": "playing", "content": "television"})
        elif scenario == "keyless_resident_entry":
            self._devices["front_door_lock.main"]["state"] = "locked"
        elif scenario == "full_bin_collection_notice":
            self._devices["trash_bin.main"].update({"state": "full", "fill_percent": 100})
        elif scenario == "pellet_supply_guard":
            self._devices["supply.pellets"].update({"state": "low", "level": self._config["initial_supply_level"]})
        elif scenario == "beer_dispenser_stock":
            self._devices["supply.beer"].update({"state": "low", "level": self._config["initial_supply_level"]})

    def _now(self) -> datetime:
        return START + timedelta(seconds=self._step * self._tick)

    def _answer(self, question: str) -> str:
        lowered = question.casefold()
        if "bathroom" in lowered:
            return f"bathroom {self._devices['bathroom_occupancy.main']['state']}"
        if "visitor" in lowered or "door" in lowered:
            return f"intercom {self._devices['visitor_intercom.main']['state']}"
        if "mail" in lowered:
            return f"mailbox {self._devices['mailbox.main']['state']}"
        if "laundry" in lowered or "wash" in lowered:
            return f"washer {self._devices['laundry.washer']['state']}"
        return "unknown"

    def _step_view(self, terminal: bool) -> BackendStep:
        now = self._now().isoformat().replace("+00:00", "Z")
        local_now = self._now().astimezone(ZoneInfo(str(self._profile["local_timezone"]))).isoformat()
        consumption_interval = self._config["inventory_consumption_seconds"]
        consumption_units = self._config["inventory_consumption_units"]
        inventory_projection = {}
        for item, device_id in (("pellets", "supply.pellets"), ("beer", "supply.beer")):
            level = self._devices[device_id]["level"]
            seconds_to_stockout = ceil(level / consumption_units) * consumption_interval if level > 0 else 0
            inventory_projection[item] = {
                "level": level,
                "consumption_units_per_interval": consumption_units,
                "consumption_interval_seconds": consumption_interval,
                "projected_stockout_at": (self._now() + timedelta(seconds=seconds_to_stockout)).isoformat().replace("+00:00", "Z"),
            }
        semantic_observations = {
            "local_time": local_now,
            "household_occupancy": deepcopy(self._household["occupancy"]),
            "stove_attendance": self._household["stove_attendance"],
            "keys_present": self._household["keys_present"],
            "bathroom_occupancy": self._household["bathroom_occupied"],
            "visitor_identity": self._household["visitor_identity"],
            "delivery_identity": self._household["delivery_identity"],
            "presented_credential": self._household["presented_credential"],
            "vehicle_identity": self._household["vehicle_identity"],
            "emergency_type": self._household["emergency_type"],
            "visitor_event_log": deepcopy(self._workflow["visitor_event_log"]),
            "inventory_projection": inventory_projection,
            "order_state": deepcopy(self._workflow["orders"]),
            "coffee_freshness": self._devices["coffee_maker.main"]["freshness_remaining_seconds"],
            "content_date": self._workflow["content_date"],
        }
        devices_public = {}
        for device_id, device in self._devices.items():
            spec = DEVICE_SPECS[device_id]
            attributes = {key: value for key, value in device.items() if key != "state"}
            devices_public[device_id] = {"device_type": spec["device_type"], "capabilities": spec["capabilities"], "state": device["state"], "attributes": deepcopy(attributes)}
        public = {
            "episode_id": self._episode_id,
            "step": self._step,
            "time": now,
            "local_time": local_now,
            "tick_seconds": self._tick,
            "inventory": {
                "complete": True,
                "devices": [
                    {
                        "device_id": device_id,
                        "device_type": spec["device_type"],
                        "capabilities": list(spec["capabilities"]),
                        "interfaces": _public_interfaces(device_id),
                        "availability": "available",
                    }
                    for device_id, spec in DEVICE_SPECS.items()
                ],
            },
            "devices": devices_public,
            "public_context": deepcopy(self._profile),
            "household": deepcopy(self._household),
            "semantic_observations": semantic_observations,
            "workflow": {
                "visitor_event_log": deepcopy(self._workflow["visitor_event_log"]),
                "orders": deepcopy(self._workflow["orders"]),
                "sessions": deepcopy(self._workflow["sessions"]),
                "occurrences": deepcopy(self._workflow["occurrences"]),
                "content_date": self._workflow["content_date"],
            },
            "events": deepcopy(self._events),
            "active_rule_ids": sorted(self._rules),
            "metrics": {
                "task_elapsed_seconds": {name: task["elapsed_seconds"] for name, task in sorted(self._tasks.items())},
                "device_runtime_seconds": dict(sorted(self._device_runtime.items())),
                "action_cost": self._action_cost,
                "cost_unit": COST_UNIT,
            },
            "terminal_reason": "horizon_reached" if terminal else None,
        }
        private = {
            "step": self._step,
            "seed": self._seed,
            "devices": deepcopy(self._devices),
            "rules": deepcopy(self._rules),
            "active_tasks": deepcopy(self._tasks),
            "completed_tasks": deepcopy(self._completed_tasks),
            "household": deepcopy(self._household),
            "workflow": deepcopy(self._workflow),
            "public_profile": deepcopy(self._profile),
            "applied_commands": deepcopy(self._applied),
            "exogenous_pending": {str(tick): deepcopy(events) for tick, events in sorted(self._exog.items())},
            "ask_log": deepcopy(self._ask_log),
            "device_runtime_seconds": dict(sorted(self._device_runtime.items())),
            "action_cost": self._action_cost,
            "cost_unit": COST_UNIT,
        }
        return BackendStep(public, private, terminal)


__all__ = [
    "COMMAND_COST",
    "COST_UNIT",
    "DEFAULT_CONFIG",
    "DEVICE_SPECS",
    "HORIZON_SECONDS",
    "TICK_SECONDS",
    "WorkflowBackend",
]
