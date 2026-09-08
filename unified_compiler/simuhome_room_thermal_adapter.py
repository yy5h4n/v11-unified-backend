"""Shared, fail-closed SimuHome room-thermal replay adapter."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


WORKSPACE = Path(__file__).resolve().parents[6]
SIMUHOME_ROOT = WORKSPACE / "external" / "SimuHome"
if str(SIMUHOME_ROOT) not in sys.path:
    sys.path.insert(0, str(SIMUHOME_ROOT))

ADAPTER_VERSION = "simuhome_room_thermal_adapter_v1"
SAMPLE_MINUTES = 15


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class ReplayError(RuntimeError):
    pass


class SimuHomeRoomThermalAdapter:
    """Replay an exact source configuration with an arbitrary step policy.

    A policy receives ``(step_index, compact_observation)`` and returns either
    ``None`` or ``{"mode": "auto|heat|cool|off", "target_c": 22}``.
    """

    version = ADAPTER_VERSION

    def __init__(self, config: dict[str, Any], room_id: str = "kitchen", device_id: str | None = None):
        self.config = copy.deepcopy(config)
        self.room_id = room_id
        self.source_digest = digest_json(self.config)
        self._requested_device_id = device_id
        self.home = self._new_home()
        self.device_id, self.device_type = self._select_device(device_id)

    def _new_home(self):
        from src.simulator.application.home_initializer import SimulationConfig, initialize_home_from_config
        from src.simulator.domain.home import Home

        home = Home(tick_interval=60.0, fast_forward=True, base_time=self.config["base_time"])
        result = initialize_home_from_config(home, SimulationConfig(**self.config))
        if not result.success:
            raise ReplayError(f"initialization_failed:{result.error_message}:{result.error_detail}")
        return home

    def _select_device(self, requested: str | None) -> tuple[str, str]:
        devices = self.home.devices_by_room.get(self.room_id, {})
        thermal = [(did, dev.device_type) for did, dev in devices.items() if dev.device_type in {"air_conditioner", "heat_pump"}]
        if requested:
            thermal = [item for item in thermal if item[0] == requested]
        if not thermal:
            raise ReplayError(f"no_room_thermal_device:{self.room_id}:{requested}")
        thermal.sort(key=lambda item: (item[1] != "air_conditioner", item[0]))
        return thermal[0]

    def _checked_command(self, endpoint: int, cluster: str, command: str, args: dict | None = None) -> None:
        result = self.home._execute_command(self.device_id, endpoint, cluster, command, args or {})
        if not result.success:
            raise ReplayError(f"command_failed:{self.device_id}:{endpoint}:{cluster}:{command}:{result.error_message}:{result.error_detail}")

    def _checked_write(self, endpoint: int, cluster: str, attribute: str, value: Any) -> None:
        result = self.home._write_attribute(self.device_id, endpoint, cluster, attribute, value)
        if not result.success:
            raise ReplayError(f"write_failed:{self.device_id}:{endpoint}:{cluster}:{attribute}={value}:{result.error_message}:{result.error_detail}")

    def _set_deadband_target(self, endpoint: int, mode: str, target_c: float) -> None:
        target = int(round(target_c * 100.0))
        if not 7.0 <= target_c <= 32.0:
            raise ReplayError(f"target_out_of_range:{target_c}")
        device = self.home.devices_by_id[self.device_id][1]
        current_cool = int(device.get_attribute(endpoint, "Thermostat", "OccupiedCoolingSetpoint"))
        interim_heat = max(700, min(1800, current_cool - 25))
        self._checked_write(endpoint, "Thermostat", "OccupiedHeatingSetpoint", interim_heat)
        if mode == "heat":
            final_cool = max(target + 25, current_cool, 2225)
            if final_cool > 3200:
                raise ReplayError(f"cannot_form_heat_deadband:{target}:{current_cool}")
            self._checked_write(endpoint, "Thermostat", "OccupiedCoolingSetpoint", final_cool)
            self._checked_write(endpoint, "Thermostat", "OccupiedHeatingSetpoint", target)
            self._checked_write(endpoint, "Thermostat", "SystemMode", 4)
        elif mode == "cool":
            if self.device_type != "air_conditioner":
                raise ReplayError("heat_pump_cooling_not_causally_supported")
            self._checked_write(endpoint, "Thermostat", "OccupiedCoolingSetpoint", target)
            self._checked_write(endpoint, "Thermostat", "SystemMode", 3)
        else:
            raise ReplayError(f"unsupported_mode:{mode}")

    def apply_room_thermal_action(self, action: dict[str, Any], observation: dict[str, Any]) -> dict[str, Any]:
        mode = str(action.get("mode", "auto"))
        target_c = float(action.get("target_c", 22.0))
        temperature_c = float(observation["temperature_c"])
        if mode == "auto":
            if temperature_c < target_c - 0.05:
                mode = "heat"
            elif temperature_c > target_c + 0.05 and self.device_type == "air_conditioner":
                mode = "cool"
            else:
                mode = "off"

        endpoint = 1 if self.device_type == "air_conditioner" else 4
        if self.device_type == "air_conditioner":
            self._checked_command(1, "OnOff", "On")
            self._checked_write(1, "FanControl", "PercentSetting", 100)
        if mode == "off":
            self._checked_write(endpoint, "Thermostat", "SystemMode", 0)
        elif mode in {"heat", "cool"}:
            self._set_deadband_target(endpoint, mode, target_c)
        else:
            raise ReplayError(f"unsupported_mode:{mode}")
        return {"mode": mode, "target_c": target_c, "device_id": self.device_id}

    def compact_observation(self) -> dict[str, Any]:
        result = self.home._get_home_state()
        if not result.success:
            raise ReplayError(f"state_failed:{result.error_message}")
        state = result.data
        temperature_raw = state["rooms"][self.room_id]["state"]["temperature"]
        return {
            "virtual_time": state["current_time"],
            "current_tick": int(state["current_tick"]),
            "room_id": self.room_id,
            "temperature_c": float(temperature_raw) / 100.0,
            "device_id": self.device_id,
            "device_type": self.device_type,
        }

    def horizon_steps_to_hour(self, end_hour: int = 23) -> int:
        start = datetime.strptime(self.config["base_time"], "%Y-%m-%d %H:%M:%S")
        end = start.replace(hour=end_hour, minute=0, second=0)
        seconds = (end - start).total_seconds()
        if seconds <= 0:
            return 0
        return int(math.floor(seconds / (SAMPLE_MINUTES * 60))) + 1

    def replay(self, policy: Callable[[int, dict[str, Any]], dict[str, Any] | None] | None, *, end_hour: int = 23) -> dict[str, Any]:
        self.home = self._new_home()
        self.device_id, self.device_type = self._select_device(self._requested_device_id)
        steps = self.horizon_steps_to_hour(end_hour)
        if steps < 2:
            raise ReplayError("insufficient_evening_horizon")
        trace: list[dict[str, Any]] = []
        for step in range(steps):
            observation = self.compact_observation()
            requested = policy(step, copy.deepcopy(observation)) if policy else None
            applied = self.apply_room_thermal_action(requested, observation) if requested else None
            trace.append({"step": step, "observation": observation, "action": applied})
            if step < steps - 1:
                target_tick = self.home.current_tick + SAMPLE_MINUTES
                advanced = self.home._fast_forward_to(target_tick, room_ids=[self.room_id])
                if not advanced.success:
                    raise ReplayError(f"advance_failed:{advanced.error_message}:{advanced.error_detail}")
        return {
            "adapter_version": self.version,
            "source_config_sha256": self.source_digest,
            "room_id": self.room_id,
            "device_id": self.device_id,
            "device_type": self.device_type,
            "sample_minutes": SAMPLE_MINUTES,
            "trace": trace,
        }
