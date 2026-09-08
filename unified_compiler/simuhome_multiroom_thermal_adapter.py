"""Deterministic, fail-closed multi-room SimuHome thermal replay adapter."""
from __future__ import annotations
import copy, hashlib, json, math, sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

WORKSPACE = Path(__file__).resolve().parents[6]
SIMUHOME_ROOT = WORKSPACE / "external" / "SimuHome"
if str(SIMUHOME_ROOT) not in sys.path: sys.path.insert(0, str(SIMUHOME_ROOT))
ADAPTER_VERSION = "simuhome_multiroom_thermal_adapter_v1"
SAMPLE_MINUTES = 15

def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
class ReplayError(RuntimeError): pass

class SimuHomeMultiroomThermalAdapter:
    """Replay two or more rooms, with an explicit single-room Harness bridge.

    Policy receives ``(step, {room_id: observation})`` and returns either one
    action (shared) or ``{room_id: action}``. Actions contain mode and target_c.
    """
    version = ADAPTER_VERSION
    def __init__(self, config: dict[str, Any], room_ids: list[str] | None = None, *, allow_single_room: bool = False):
        raw = copy.deepcopy(config)
        self.config = raw.get("initial_home_config", raw)
        available = sorted(self.config.get("rooms", {}).keys())
        if room_ids is None:
            raise ReplayError("at_least_two_explicit_rooms_required")
        self.room_ids = sorted(set(room_ids))
        if len(self.room_ids) < 2 and not (allow_single_room and len(self.room_ids) == 1):
            raise ReplayError("at_least_two_explicit_rooms_required")
        if not set(self.room_ids) <= set(available): raise ReplayError("unknown_room_id")
        self.source_digest = digest_json(self.config)
        self.home = self._new_home()
        self.devices = {room: self._select_device(room) for room in self.room_ids}

    def _new_home(self):
        from src.simulator.application.home_initializer import SimulationConfig, initialize_home_from_config
        from src.simulator.domain.home import Home
        home = Home(tick_interval=60.0, fast_forward=True, base_time=self.config["base_time"])
        result = initialize_home_from_config(home, SimulationConfig(**self.config))
        if not result.success: raise ReplayError(f"initialization_failed:{result.error_message}:{result.error_detail}")
        return home

    def _select_device(self, room: str) -> tuple[str, str]:
        devices = self.home.devices_by_room.get(room, {})
        thermal = [(did, dev.device_type) for did, dev in devices.items() if dev.device_type in {"air_conditioner", "heat_pump"}]
        if not thermal: raise ReplayError(f"no_room_thermal_device:{room}")
        thermal.sort(key=lambda x: (x[1] != "air_conditioner", x[0]))
        return thermal[0]

    def _checked_command(self, device_id: str, endpoint: int, cluster: str, command: str, args: dict | None = None):
        result = self.home._execute_command(device_id, endpoint, cluster, command, args or {})
        if not result.success: raise ReplayError(f"command_failed:{device_id}:{endpoint}:{cluster}:{command}:{result.error_message}:{result.error_detail}")
    def _checked_write(self, device_id: str, endpoint: int, cluster: str, attribute: str, value: Any):
        result = self.home._write_attribute(device_id, endpoint, cluster, attribute, value)
        if not result.success: raise ReplayError(f"write_failed:{device_id}:{endpoint}:{cluster}:{attribute}={value}:{result.error_message}:{result.error_detail}")

    def _set_target(self, device_id: str, device_type: str, endpoint: int, mode: str, target_c: float):
        if not 7.0 <= target_c <= 32.0: raise ReplayError(f"target_out_of_range:{target_c}")
        target = int(round(target_c * 100))
        current = int(self.home.devices_by_id[device_id][1].get_attribute(endpoint, "Thermostat", "OccupiedCoolingSetpoint"))
        # Move the heating side of the deadband first. Otherwise a downward
        # cooling-setpoint write can be rejected against the old heating value.
        interim_heat = max(700, min(1800, target - 25, current - 25))
        self._checked_write(device_id, endpoint, "Thermostat", "OccupiedHeatingSetpoint", interim_heat)
        if mode == "cool":
            if device_type != "air_conditioner": raise ReplayError("heat_pump_cooling_not_causally_supported")
            self._checked_write(device_id, endpoint, "Thermostat", "OccupiedCoolingSetpoint", target)
            self._checked_write(device_id, endpoint, "Thermostat", "SystemMode", 3)
        elif mode == "heat":
            final_cool = max(target + 25, current, 2225)
            if final_cool > 3200: raise ReplayError(f"cannot_form_heat_deadband:{target}:{current}")
            self._checked_write(device_id, endpoint, "Thermostat", "OccupiedCoolingSetpoint", final_cool)
            self._checked_write(device_id, endpoint, "Thermostat", "OccupiedHeatingSetpoint", target)
            self._checked_write(device_id, endpoint, "Thermostat", "SystemMode", 4)
        else: raise ReplayError(f"unsupported_mode:{mode}")

    def _apply_for_room(self, room: str, action: dict[str, Any], observation: dict[str, Any]):
        device_id, device_type = self.devices[room]
        mode, target_c = str(action.get("mode", "auto")), float(action.get("target_c", 22.0))
        temp = float(observation["temperature_c"])
        if mode == "auto": mode = "heat" if temp < target_c - .05 else ("cool" if temp > target_c + .05 and device_type == "air_conditioner" else "off")
        endpoint = 1 if device_type == "air_conditioner" else 4
        if mode == "off":
            if device_type == "air_conditioner":
                device = self.home.devices_by_id[device_id][1]
                if bool(device.get_attribute(1, "OnOff", "OnOff")):
                    self._checked_write(device_id, endpoint, "Thermostat", "SystemMode", 0)
                    self._checked_write(device_id, 1, "FanControl", "PercentSetting", 0)
                    self._checked_command(device_id, 1, "OnOff", "Off")
            else:
                self._checked_write(device_id, endpoint, "Thermostat", "SystemMode", 0)
        elif mode in {"heat", "cool"}:
            if device_type == "air_conditioner":
                self._checked_command(device_id, 1, "OnOff", "On")
                self._checked_write(device_id, 1, "FanControl", "PercentSetting", 100)
            self._set_target(device_id, device_type, endpoint, mode, target_c)
        else: raise ReplayError(f"unsupported_mode:{mode}")
        return {"mode": mode, "target_c": target_c, "device_id": device_id}

    def compact_observations(self):
        result = self.home._get_home_state()
        if not result.success: raise ReplayError(f"state_failed:{result.error_message}")
        state = result.data
        return {r: {"virtual_time": state["current_time"], "current_tick": int(state["current_tick"]), "room_id": r,
                    "temperature_c": float(state["rooms"][r]["state"]["temperature"]) / 100,
                    "device_id": self.devices[r][0], "device_type": self.devices[r][1]} for r in self.room_ids}

    def horizon_steps_to_hour(self, end_hour: int = 23):
        start = datetime.strptime(self.config["base_time"], "%Y-%m-%d %H:%M:%S")
        end = start.replace(hour=end_hour, minute=0, second=0)
        seconds = (end - start).total_seconds()
        # Observations are strictly before the exclusive end boundary.
        return 0 if seconds <= 0 else int(math.ceil(seconds / (SAMPLE_MINUTES * 60)))

    def replay(self, policy: Callable[[int, dict[str, dict[str, Any]]], Any] | None, *, end_hour: int = 23):
        self.home = self._new_home()
        self.devices = {r: self._select_device(r) for r in self.room_ids}
        steps = self.horizon_steps_to_hour(end_hour)
        if steps < 2: raise ReplayError("insufficient_evening_horizon")
        trace = []
        for step in range(steps):
            observation = self.compact_observations()
            requested = policy(step, copy.deepcopy(observation)) if policy else None
            applied = None
            if requested is not None:
                if not isinstance(requested, dict): raise ReplayError("invalid_policy_action")
                mapping = ({r: requested for r in self.room_ids} if "mode" in requested or "target_c" in requested else requested)
                if set(mapping) != set(self.room_ids): raise ReplayError("action_mapping_must_cover_all_rooms")
                # Validate all rooms before issuing any command, so an invalid
                # heat-pump cooling request cannot partially apply.
                for r in self.room_ids:
                    if str(mapping[r].get("mode", "auto")) == "cool" and self.devices[r][1] != "air_conditioner":
                        raise ReplayError("heat_pump_cooling_not_causally_supported")
                applied = {r: self._apply_for_room(r, mapping[r], observation[r]) for r in self.room_ids}
            trace.append({"step": step, "observation": observation, "action": applied})
            if step < steps - 1:
                advanced = self.home._fast_forward_to(self.home.current_tick + SAMPLE_MINUTES, room_ids=self.room_ids)
                if not advanced.success: raise ReplayError(f"advance_failed:{advanced.error_message}:{advanced.error_detail}")
        return {"adapter_version": self.version, "source_config_sha256": self.source_digest, "room_ids": self.room_ids,
                "devices": {r: {"device_id": d, "device_type": t} for r, (d, t) in self.devices.items()},
                "source_tick_interval": self.config.get("tick_interval"),
                "effective_tick_interval_seconds": 60.0,
                "tick_mapping": "one_simulator_tick_equals_one_virtual_minute",
                "sample_minutes": SAMPLE_MINUTES, "trace": trace}
