"""Harness V2 backend for the real SimuHome multi-room thermal simulator."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from math import ceil
from typing import Any

from unified_compiler.simuhome_multiroom_thermal_adapter import (
    ReplayError,
    SAMPLE_MINUTES,
    SimuHomeMultiroomThermalAdapter,
    digest_json,
)

from .core import ActionOutcome, BackendStep, EpisodeSpec


class SimuHomeAdvanceError(ReplayError):
    def __init__(self, phase: str, error_code: str):
        super().__init__(f"{phase}:{error_code}")
        self.phase = phase
        self.error_code = error_code


class SimuHomeHarnessAdapter:
    """Expose one deterministic SimuHome instance through the minimal core API."""

    def __init__(self, config: dict[str, Any], room_ids: list[str], *, sample_minutes: int = SAMPLE_MINUTES):
        self._config = deepcopy(config)
        self._room_ids = sorted(set(room_ids))
        self._sample_minutes = sample_minutes
        self._legacy: SimuHomeMultiroomThermalAdapter | None = None
        self._episode: EpisodeSpec | None = None
        self._step_index = 0
        self._horizon_steps = 0
        self._rules: dict[str, dict[str, Any]] = {}
        self._history: list[dict[str, Any]] = []
        self._events: list[dict[str, Any]] = []
        self._applied: list[dict[str, Any]] = []
        self._energy_proxy_wh: dict[str, float] = {}
        self._last_interval_energy_proxy_wh: dict[str, float | None] = {}

    def reset(self, episode: EpisodeSpec) -> BackendStep:
        self._episode = episode
        self._horizon_steps = int(episode.public_bootstrap["horizon_steps"])
        if self._horizon_steps < 1:
            raise ValueError("horizon_steps must be positive")
        self._reset_runtime()
        return self._step(False)

    def state_digest(self) -> str:
        return digest_json({
            "step_index": self._step_index,
            "home": self._causal_home_state(),
            "rules": self._rules,
            "history": self._history,
            "events": self._events,
            "applied_commands": self._applied,
            "energy_proxy_wh": self._energy_proxy_wh,
            "last_interval_energy_proxy_wh": self._last_interval_energy_proxy_wh,
        })

    def execute_atomic(self, action: dict[str, Any]) -> ActionOutcome:
        transaction_id = self._transaction_id(action)
        error = self._validate_action(action)
        if error:
            return self._rejected(error, transaction_id)
        before_history = deepcopy(self._history)
        try:
            self._applied = []
            self._apply_action(action, transaction_id=transaction_id, record=True)
        except Exception as exc:
            self._rebuild(before_history)
            return self._rejected(f"BACKEND_REJECTED:{type(exc).__name__}", transaction_id)
        private_feedback = {
            "status": "accepted",
            "transaction_id": transaction_id,
            "transaction_status": "committed",
            "applied_commands": deepcopy(self._applied),
            "active_rule_ids": sorted(self._rules),
        }
        public_feedback = {
            "status": "accepted",
            "transaction_id": transaction_id,
            "applied_command_count": len(self._applied),
            "active_rule_ids": sorted(self._rules),
        }
        return ActionOutcome(True, public_feedback, private_feedback)

    def advance(self, triggering_action: dict[str, Any] | None = None) -> BackendStep:
        triggering_action = triggering_action or {"kind": "act", "commands": []}
        if self._step_index >= self._horizon_steps:
            return self._step(True)
        before_history = deepcopy(self._history)
        try:
            steps = self._wait_steps(triggering_action)
            for _ in range(steps):
                self._advance_once(record=True)
                if self._step_index >= self._horizon_steps:
                    break
                if triggering_action["kind"] == "wait" and self._events:
                    break
        except Exception:
            self._rebuild(before_history)
            raise
        return self._step(self._step_index >= self._horizon_steps)

    def _wait_steps(self, action: dict[str, Any]) -> int:
        if action["kind"] != "wait":
            return 1
        tick_seconds = self._sample_minutes * 60
        mode = action["mode"]
        if mode == "for":
            seconds = float(action["duration_seconds"])
        elif mode == "until_event":
            seconds = float(action["timeout_seconds"])
        else:
            current_text = next(iter(self._require_legacy().compact_observations().values()))["virtual_time"]
            current = datetime.fromisoformat(current_text.replace("Z", "+00:00"))
            target = datetime.fromisoformat(action["timestamp"].replace("Z", "+00:00"))
            seconds = (target - current).total_seconds()
        if seconds <= 0:
            raise SimuHomeAdvanceError("wait", "WAIT_TARGET_NOT_IN_FUTURE")
        return max(1, ceil(seconds / tick_seconds))

    def _event_matches(self, event_filter: dict[str, Any]) -> bool:
        return any(all(event.get(key) == value for key, value in event_filter.items()) for event in self._events)

    def _causal_home_state(self) -> dict[str, Any]:
        legacy = self._require_legacy()
        home = legacy.home
        state = home._get_home_state()
        if not state.success:
            raise ReplayError(f"state_failed:{state.error_message}")
        aggregators = {}
        for room in self._room_ids:
            aggregators[room] = {
                name: {
                    "current_value": aggregator.current_value,
                    "baseline_value": aggregator.baseline_value,
                    "continuous_effects": aggregator.continuous_effects,
                    "last_device_states": aggregator._last_device_states,
                    "first_sync_done": aggregator._first_sync_done,
                }
                for name, aggregator in sorted(home.aggregators_by_room.get(room, {}).items())
            }
        return {
            "public_state": state.data,
            "aggregators": aggregators,
            "scheduler_queue": list(home.schedular_queue.queue),
            "task_seq_counter": home.task_seq_counter,
            "workflows": home.workflows_by_id,
        }

    def _reset_runtime(self) -> None:
        self._legacy = SimuHomeMultiroomThermalAdapter(
            self._config,
            room_ids=self._room_ids,
            allow_single_room=True,
        )
        self._step_index = 0
        self._rules = {}
        self._history = []
        self._events = []
        self._applied = []
        self._energy_proxy_wh = {
            device_id: 0.0 for device_id, device_type in self._require_legacy().devices.values()
            if device_type == "heat_pump"
        }
        self._last_interval_energy_proxy_wh = {
            device_id: (0.0 if device_type == "heat_pump" else None)
            for device_id, device_type in self._require_legacy().devices.values()
        }

    def _rebuild(self, history: list[dict[str, Any]]) -> None:
        self._reset_runtime()
        for item in history:
            if item["kind"] == "action":
                self._applied = []
                self._apply_action(item["value"], transaction_id=item["transaction_id"], record=True)
            else:
                self._advance_once(record=True)

    def _transaction_id(self, action: dict[str, Any]) -> str:
        return f"tx.{self._step_index}.{len(self._history)}.{digest_json(action)[:12]}"

    def _validate_action(self, action: dict[str, Any]) -> str | None:
        kind = action["kind"]
        if kind == "act":
            return self._validate_commands(action["commands"])
        if kind == "install_rule":
            rule = action["rule"]
            required = {"rule_id", "fire_at_step", "release_at_step", "commands", "release_commands"}
            if set(rule) != required or rule["rule_id"] in self._rules:
                return "INVALID_RULE"
            if not isinstance(rule["fire_at_step"], int) or not isinstance(rule["release_at_step"], int):
                return "INVALID_RULE_TIME"
            if not self._step_index < rule["fire_at_step"] < rule["release_at_step"] <= self._horizon_steps:
                return "INVALID_RULE_TIME"
            fire_error = self._validate_commands(rule["commands"])
            if fire_error:
                return fire_error
            return self._validate_commands(rule["release_commands"])
        if kind == "cancel_rule":
            return None if action["rule_id"] in self._rules else "UNKNOWN_RULE"
        if kind in {"ask", "wait"}:
            return None
        return "UNSUPPORTED_ACTION"

    def _validate_commands(self, commands: list[dict[str, Any]]) -> str | None:
        legacy = self._require_legacy()
        by_device = {device_id: (room, device_type) for room, (device_id, device_type) in legacy.devices.items()}
        seen: set[str] = set()
        for command in commands:
            if not isinstance(command, dict) or set(command) != {"device_id", "capability", "operation", "parameters"}:
                return "INVALID_COMMAND"
            device_id = command["device_id"]
            if device_id not in by_device:
                return "UNKNOWN_DEVICE"
            if device_id in seen:
                return "DUPLICATE_DEVICE_COMMAND"
            seen.add(device_id)
            if command["capability"] != "thermal.control" or command["operation"] != "set":
                return "UNSUPPORTED_COMMAND"
            parameters = command["parameters"]
            if not isinstance(parameters, dict) or set(parameters) != {"mode", "target_c"}:
                return "INVALID_PARAMETERS"
            mode, target = parameters["mode"], parameters["target_c"]
            if mode not in {"heat", "cool", "off", "auto"} or not isinstance(target, (int, float)) or not 7 <= target <= 32:
                return "INVALID_PARAMETERS"
            if mode == "cool" and by_device[device_id][1] != "air_conditioner":
                return "UNSUPPORTED_COOLING"
        return None

    def _apply_action(self, action: dict[str, Any], *, transaction_id: str, record: bool) -> None:
        kind = action["kind"]
        if kind == "act":
            self._apply_commands(
                action["commands"],
                "agent_immediate",
                origin={"kind": "agent_immediate", "transaction_id": transaction_id},
            )
        elif kind == "install_rule":
            rule = deepcopy(action["rule"])
            rule["_installation_transaction_id"] = transaction_id
            self._rules[rule["rule_id"]] = rule
            self._events.append({"type": "rule_installed", "rule_id": rule["rule_id"]})
        elif kind == "cancel_rule":
            del self._rules[action["rule_id"]]
            self._events.append({"type": "rule_cancelled", "rule_id": action["rule_id"]})
        if record:
            self._history.append({"kind": "action", "transaction_id": transaction_id, "value": deepcopy(action)})

    def _advance_once(self, *, record: bool) -> None:
        legacy = self._require_legacy()
        self._step_index += 1
        self._events = []
        self._applied = []
        for rule_id, rule in list(self._rules.items()):
            if self._step_index == rule["fire_at_step"]:
                try:
                    self._apply_commands(rule["commands"], "rule_firing", rule_id)
                except Exception as exc:
                    raise SimuHomeAdvanceError("rule_firing", "RULE_FIRE_FAILED") from exc
                self._events.append({"type": "rule_fired", "rule_id": rule_id})
            if self._step_index == rule["release_at_step"]:
                try:
                    self._apply_commands(rule["release_commands"], "rule_release", rule_id)
                except Exception as exc:
                    raise SimuHomeAdvanceError("rule_release", "RULE_RELEASE_FAILED") from exc
                self._events.append({"type": "rule_released", "rule_id": rule_id})
                del self._rules[rule_id]
        interval_power = {
            device_id: self._effective_power_proxy_w(room)
            for room, (device_id, _) in legacy.devices.items()
        }
        result = legacy.home._fast_forward_to(legacy.home.current_tick + self._sample_minutes, room_ids=self._room_ids)
        if not result.success:
            raise SimuHomeAdvanceError("fast_forward", "SIMULATOR_ADVANCE_FAILED")
        for device_id, power_w in interval_power.items():
            interval_wh = None if power_w is None else power_w * self._sample_minutes / 60.0
            self._last_interval_energy_proxy_wh[device_id] = interval_wh
            if interval_wh is not None:
                self._energy_proxy_wh[device_id] = self._energy_proxy_wh.get(device_id, 0.0) + interval_wh
        if record:
            self._history.append({"kind": "advance"})

    def _apply_commands(
        self,
        commands: list[dict[str, Any]],
        source: str,
        rule_id: str | None = None,
        origin: dict[str, Any] | None = None,
    ) -> None:
        legacy = self._require_legacy()
        observations = legacy.compact_observations()
        room_by_device = {device_id: room for room, (device_id, _) in legacy.devices.items()}
        for command in commands:
            room = room_by_device[command["device_id"]]
            parameters = command["parameters"]
            requested = {"mode": parameters["mode"], "target_c": parameters["target_c"]}
            resolved_mode = self._resolve_mode(room, requested, observations[room])
            if self._same_control_state(room, resolved_mode, float(parameters["target_c"])):
                applied = {"mode": resolved_mode, "target_c": float(parameters["target_c"]), "device_id": command["device_id"]}
                command_status = "coalesced"
            else:
                applied = legacy._apply_for_room(room, requested, observations[room])
                command_status = "committed"
            if origin is None and rule_id is not None:
                rule = self._rules[rule_id]
                occurrence_kind = "trigger_occurrence_id" if source == "rule_firing" else "retirement_occurrence_id"
                origin = {
                    "kind": source,
                    "installation_transaction_id": rule["_installation_transaction_id"],
                    "rule_id": rule_id,
                    occurrence_kind: f"{source}.{rule_id}.{self._step_index}",
                }
            self._applied.append({
                **applied,
                "source": source,
                "origin": deepcopy(origin),
                "rule_id": rule_id,
                "applied_at_step": self._step_index,
                "status": command_status,
            })

    def _resolve_mode(self, room: str, action: dict[str, Any], observation: dict[str, Any]) -> str:
        mode = str(action["mode"])
        if mode != "auto":
            return mode
        target = float(action["target_c"])
        temperature = float(observation["temperature_c"])
        device_type = self._require_legacy().devices[room][1]
        if temperature < target - 0.05:
            return "heat"
        if temperature > target + 0.05 and device_type == "air_conditioner":
            return "cool"
        return "off"

    def _same_control_state(self, room: str, mode: str, target_c: float) -> bool:
        device_id, device_type = self._require_legacy().devices[room]
        state = self._public_device_state(device_id, device_type)
        if state["mode"] != mode:
            return False
        return mode == "off" or abs(float(state["target_c"]) - target_c) < 1e-9

    def _effective_power_proxy_w(self, room: str) -> float | None:
        legacy = self._require_legacy()
        device_id, device_type = legacy.devices[room]
        if device_type != "heat_pump":
            return None
        device = legacy.home.devices_by_id[device_id][1]
        rated_power_w = float(device.get_grid_power_consumption())
        observations = legacy.compact_observations()
        temperature = float(observations[room]["temperature_c"])
        state = self._public_device_state(device_id, device_type)
        target = float(state["target_c"])
        actuating = state["mode"] == "heat" and temperature < target
        return rated_power_w if actuating else 0.0

    def _step(self, terminal: bool) -> BackendStep:
        legacy = self._require_legacy()
        observations = legacy.compact_observations()
        public_rules = [
            {key: deepcopy(value) for key, value in rule.items() if not key.startswith("_")}
            for _, rule in sorted(self._rules.items())
        ]
        public = {
            "step": self._step_index,
            "virtual_time": next(iter(observations.values()))["virtual_time"],
            "rooms": {room: {"temperature_c": item["temperature_c"]} for room, item in observations.items()},
            "devices": {
                room: {
                    "device_id": item["device_id"],
                    "device_type": item["device_type"],
                    **self._public_device_state(item["device_id"], item["device_type"]),
                }
                for room, item in observations.items()
            },
            "events": deepcopy(self._events),
            "active_rules": public_rules,
        }
        private = {"applied_commands": deepcopy(self._applied), "active_rule_ids": sorted(self._rules), "backend_state_digest": self.state_digest()}
        return BackendStep(public, private, terminal)

    def _public_device_state(self, device_id: str, device_type: str) -> dict[str, Any]:
        device = self._require_legacy().home.devices_by_id[device_id][1]
        endpoint = 1 if device_type == "air_conditioner" else 4
        raw_mode = int(device.get_attribute(endpoint, "Thermostat", "SystemMode"))
        mode = {0: "off", 3: "cool", 4: "heat"}.get(raw_mode, "other")
        heat_target = float(device.get_attribute(endpoint, "Thermostat", "OccupiedHeatingSetpoint")) / 100.0
        cool_target = float(device.get_attribute(endpoint, "Thermostat", "OccupiedCoolingSetpoint")) / 100.0
        return {
            "mode": mode,
            "target_c": cool_target if mode == "cool" else heat_target,
            "heating_setpoint_c": heat_target,
            "cooling_setpoint_c": cool_target,
            "thermal_actuation_active": self._thermal_actuation_active(device_id, device_type, mode, heat_target, cool_target),
            "rated_active_power_w": self._rated_power_w(device_id, device_type),
            "effective_power_proxy_w": self._effective_power_for_public(device_id, device_type, mode, heat_target, cool_target),
            "last_interval_energy_proxy_wh": self._last_interval_energy_proxy_wh.get(device_id),
            "cumulative_energy_proxy_wh": self._energy_proxy_wh.get(device_id),
            "energy_semantics": "simulator_duty_gated_rated_power_proxy" if device_type == "heat_pump" else "unavailable",
        }

    def _thermal_actuation_active(
        self, device_id: str, device_type: str, mode: str, heat_target: float, cool_target: float
    ) -> bool:
        legacy = self._require_legacy()
        room = next(room for room, (candidate, _) in legacy.devices.items() if candidate == device_id)
        temperature = float(legacy.compact_observations()[room]["temperature_c"])
        return (mode == "heat" and temperature < heat_target) or (mode == "cool" and temperature > cool_target)

    def _rated_power_w(self, device_id: str, device_type: str) -> float | None:
        if device_type != "heat_pump":
            return None
        return float(self._require_legacy().home.devices_by_id[device_id][1].get_grid_power_consumption())

    def _effective_power_for_public(
        self, device_id: str, device_type: str, mode: str, heat_target: float, cool_target: float
    ) -> float | None:
        rated = self._rated_power_w(device_id, device_type)
        if rated is None:
            return None
        return rated if self._thermal_actuation_active(device_id, device_type, mode, heat_target, cool_target) else 0.0

    @staticmethod
    def _rejected(error: str, transaction_id: str) -> ActionOutcome:
        return ActionOutcome(
            False,
            {"status": "rejected", "error_code": error, "transaction_id": transaction_id},
            {
                "status": "rejected",
                "transaction_id": transaction_id,
                "transaction_status": "rejected",
                "applied_commands": [],
            },
            error,
        )

    def _require_legacy(self) -> SimuHomeMultiroomThermalAdapter:
        if self._legacy is None:
            raise RuntimeError("adapter is not reset")
        return self._legacy
