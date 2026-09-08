#!/usr/bin/env python3
"""D3 strong-coupling route for two EnergyPlus zones sharing one fan.

The two Agent channels are native Schedule:Compact actuator writes.  An EMS
program in the generated IDF reads those schedules and allocates the native
Zone Ventilation ``Air Exchange Flow Rate`` actuators under one 0.8 m3/s
capacity.  This is intentionally a live callback adapter: one EnergyPlusAPI
state runs in a dedicated thread, and every Agent step releases exactly one
900-second (four-timestep-per-hour) zone timestep.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent
EPLUS = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64"
SOURCE_MODEL = EPLUS / "ExampleFiles/VentilationSimpleTest.idf"
WEATHER = None  # The official example uses design-day environments, not EPW.
RUNTIME = EPLUS / "energyplus"
ASSET_ROOT = ROOT / "shared_assets/energyplus_d3_shared_ventilation_v1"
WORK_ROOT = ROOT / "generated/d3_energyplus_shared_ventilation_v1"
WORKING_MODEL = WORK_ROOT / "D3SharedVentilation.idf"
RUN_ROOT = WORK_ROOT / "runtime_runs"
TICK_SECONDS = 900.0
HORIZON_STEPS = 24
SHARED_CAPACITY_M3_S = 0.8
ACTION_NAMES = ("zone_a_airflow_request", "zone_b_airflow_request")
OBSERVATION_NAMES = (
    "zone_a_co2_ppm", "zone_b_co2_ppm",
    "zone_a_relative_humidity_pct", "zone_b_relative_humidity_pct",
    "zone_a_temperature_c", "zone_b_temperature_c",
    "zone_a_actual_airflow_m3_s", "zone_b_actual_airflow_m3_s",
)

if str(EPLUS) not in sys.path:
    sys.path.insert(0, str(EPLUS))
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402


class D3EnergyPlusError(RuntimeError):
    """Pinned EnergyPlus runtime/model/callback failure."""


class D3EnergyPlusActionError(ValueError):
    """Malformed public action or physical timestep."""


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _model_suffix() -> str:
    """The D3 additions are ordinary native IDF/EMS objects."""
    return r'''

  Schedule:Compact,
    D3_REQUEST_A,
    Fraction,
    Through: 12/31,
    For: AllDays,
    Until: 24:00, 0.0;

  Schedule:Compact,
    D3_REQUEST_B,
    Fraction,
    Through: 12/31,
    For: AllDays,
    Until: 24:00, 0.0;

  EnergyManagementSystem:Sensor,
    D3_Request_A_Sensor,
    D3_REQUEST_A,
    Schedule Value;

  EnergyManagementSystem:Sensor,
    D3_Request_B_Sensor,
    D3_REQUEST_B,
    Schedule Value;

  EnergyManagementSystem:Actuator,
    D3_Zone_A_Flow,
    ZONE 1 VENTL 1,
    Zone Ventilation,
    Air Exchange Flow Rate;

  EnergyManagementSystem:Actuator,
    D3_Zone_B_Flow,
    ZONE 2 VENTL 1,
    Zone Ventilation,
    Air Exchange Flow Rate;

  EnergyManagementSystem:GlobalVariable,
    D3_Total_Request;

  EnergyManagementSystem:Program,
    D3_Shared_Fan_Allocation,
    SET D3_Total_Request = D3_Request_A_Sensor + D3_Request_B_Sensor,
    IF D3_Total_Request > 1.0,
      SET D3_Zone_A_Flow = 0.8 * D3_Request_A_Sensor / D3_Total_Request,
      SET D3_Zone_B_Flow = 0.8 * D3_Request_B_Sensor / D3_Total_Request,
    ELSE,
      SET D3_Zone_A_Flow = 0.8 * D3_Request_A_Sensor,
      SET D3_Zone_B_Flow = 0.8 * D3_Request_B_Sensor,
    ENDIF;

  EnergyManagementSystem:ProgramCallingManager,
    D3_Shared_Fan_Manager,
    BeginTimestepBeforePredictor,
    D3_Shared_Fan_Allocation;

  Output:Variable,
    ZONE 1,
    Zone Air CO2 Concentration,
    timestep;

  Output:Variable,
    ZONE 2,
    Zone Air CO2 Concentration,
    timestep;

  Output:Variable,
    ZONE 1,
    Zone Air Relative Humidity,
    timestep;

  Output:Variable,
    ZONE 2,
    Zone Air Relative Humidity,
    timestep;
'''


def prepare_working_model(target: Path | None = None) -> tuple[str, str]:
    """Construct a deterministic, legal two-agent model from the official asset."""
    if not SOURCE_MODEL.is_file():
        raise D3EnergyPlusError(f"pinned official EnergyPlus model missing: {SOURCE_MODEL}")
    source = SOURCE_MODEL.read_text(encoding="utf-8")
    # Enable native CO2 balance (the example already contains People CO2 rates).
    marker = "  ScheduleTypeLimits,\n    Any Number;"
    contaminant = """  ZoneAirContaminantBalance,
    Yes,
    D3_OUTDOOR_CO2,
    No,
    ;

  Schedule:Compact,
    D3_OUTDOOR_CO2,
    Any Number,
    Through: 12/31,
    For: AllDays,
    Until: 24:00, 415.0;

"""
    if source.count(marker) != 1:
        raise D3EnergyPlusError("official model anchor for contaminant balance is not unique")
    working = source.replace(marker, contaminant + marker, 1)
    # Replace only the first two named ventilation blocks.  Each has a fixed
    # 0.8 m3/s native design flow; EMS is the authoritative shared cap.
    for zone, request in (("ZONE 1", "D3_REQUEST_A"), ("ZONE 2", "D3_REQUEST_B")):
        zone_anchor = f"  Zone,\n    {zone},"
        start = working.find("  ZoneVentilation:DesignFlowRate,", working.find(zone_anchor))
        if start < 0:
            raise D3EnergyPlusError(f"native ventilation object missing for {zone}")
        end = working.find(";", start)
        if end < 0:
            raise D3EnergyPlusError(f"unterminated native ventilation object for {zone}")
        block = working[start : end + 1]
        if block.count("    Constant,") != 1 or block.count("    6.131944,") != 1:
            raise D3EnergyPlusError(f"unexpected native ventilation block for {zone}")
        block = block.replace("    Constant,", f"    {request},", 1)
        block = block.replace("    6.131944,", "    0.800000,", 1)
        block = block.replace("    NATURAL,", "    INTAKE,", 1)
        working = working[:start] + block + working[end + 1 :]
    working += _model_suffix()
    target = target or WORKING_MODEL
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(working, encoding="utf-8")
    # Run-time IDF validity is checked by EnergyPlus itself; do not claim a
    # surrogate or silently fall back to a different model.
    return sha256(SOURCE_MODEL) or "", sha256(target) or ""


def validate_action(action: Any) -> dict[str, float]:
    if not isinstance(action, Mapping) or set(action) != set(ACTION_NAMES):
        raise D3EnergyPlusActionError(f"action must contain exactly {ACTION_NAMES}")
    out = {}
    for name in ACTION_NAMES:
        value = action[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise D3EnergyPlusActionError(f"{name} must be a finite number in [0, 1]")
        value = float(value)
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            raise D3EnergyPlusActionError(f"{name} must be a finite number in [0, 1]")
        out[name] = value
    return out


class D3EnergyPlusSharedVentilationRoute:
    """Persistent two-zone EnergyPlus callback route."""

    backend_name = "EnergyPlus"
    backend_engine = "EnergyPlusAPI"
    public_action_names = ACTION_NAMES

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._thread: threading.Thread | None = None
        self._api: Any = None
        self._state: Any = None
        self._handles: dict[str, int] = {}
        self._action = {name: 0.0 for name in ACTION_NAMES}
        self._observation: dict[str, float] | None = None
        self._time_seconds = 0.0
        self._sequence = 0
        self._release_sequence = 0
        self._terminated = False
        self._stop = False
        self._error: str | None = None
        self._session_id = uuid.uuid4().hex
        self._session_model: Path | None = None
        self._native_first_sequence: int | None = None

    def reset(self, seed: int = 0) -> dict[str, float]:
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise D3EnergyPlusActionError("seed must be an integer")
        self.close()
        session_root = RUN_ROOT / f"session_{self._session_id}_{os.getpid()}"
        session_root.mkdir(parents=True, exist_ok=True)
        self._session_model = session_root / "D3SharedVentilation.idf"
        prepare_working_model(self._session_model)
        api = EnergyPlusAPI()
        state = api.state_manager.new_state()
        for name, key in (
            ("Zone Air CO2 Concentration", "ZONE 1"),
            ("Zone Air CO2 Concentration", "ZONE 2"),
            ("Zone Air Relative Humidity", "ZONE 1"),
            ("Zone Air Relative Humidity", "ZONE 2"),
            ("Zone Mean Air Temperature", "ZONE 1"),
            ("Zone Mean Air Temperature", "ZONE 2"),
            ("Zone Ventilation Current Density Volume Flow Rate", "ZONE 1"),
            ("Zone Ventilation Current Density Volume Flow Rate", "ZONE 2"),
        ):
            api.exchange.request_variable(state, name, key)
        with self._cv:
            self._api, self._state = api, state
            self._handles = {}
            self._action = {name: 0.0 for name in ACTION_NAMES}
            self._observation = None
            self._sequence = self._release_sequence = 0
            self._native_first_sequence = None
            self._time_seconds = 0.0
            self._terminated = self._stop = False
            self._error = None

        def acquire(s: Any) -> bool:
            if not api.exchange.api_data_fully_ready(s):
                return False
            with self._cv:
                if self._handles:
                    return True
            handles = {
                "request_a": api.exchange.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "D3_REQUEST_A"),
                "request_b": api.exchange.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "D3_REQUEST_B"),
                "zone_a_airflow": api.exchange.get_variable_handle(s, "Zone Ventilation Current Density Volume Flow Rate", "ZONE 1"),
                "zone_b_airflow": api.exchange.get_variable_handle(s, "Zone Ventilation Current Density Volume Flow Rate", "ZONE 2"),
                "zone_a_co2": api.exchange.get_variable_handle(s, "Zone Air CO2 Concentration", "ZONE 1"),
                "zone_b_co2": api.exchange.get_variable_handle(s, "Zone Air CO2 Concentration", "ZONE 2"),
                "zone_a_rh": api.exchange.get_variable_handle(s, "Zone Air Relative Humidity", "ZONE 1"),
                "zone_b_rh": api.exchange.get_variable_handle(s, "Zone Air Relative Humidity", "ZONE 2"),
                "zone_a_temp": api.exchange.get_variable_handle(s, "Zone Mean Air Temperature", "ZONE 1"),
                "zone_b_temp": api.exchange.get_variable_handle(s, "Zone Mean Air Temperature", "ZONE 2"),
            }
            invalid = {k: v for k, v in handles.items() if v < 0}
            with self._cv:
                if invalid:
                    self._error = f"invalid EnergyPlus handles: {invalid}"
                    self._cv.notify_all()
                    return False
                self._handles = handles
            return True

        def before(s: Any) -> None:
            if not acquire(s):
                return
            with self._cv:
                action = dict(self._action)
            # Agent action enters through native schedule actuators.  The EMS
            # program in the model performs the shared-capacity allocation.
            api.exchange.set_actuator_value(s, self._handles["request_a"], action[ACTION_NAMES[0]])
            api.exchange.set_actuator_value(s, self._handles["request_b"], action[ACTION_NAMES[1]])

        def after(s: Any) -> None:
            if not acquire(s) or api.exchange.warmup_flag(s):
                return
            with self._cv:
                if self._terminated:
                    return
                observation = {
                    "zone_a_co2_ppm": float(api.exchange.get_variable_value(s, self._handles["zone_a_co2"])),
                    "zone_b_co2_ppm": float(api.exchange.get_variable_value(s, self._handles["zone_b_co2"])),
                    "zone_a_relative_humidity_pct": float(api.exchange.get_variable_value(s, self._handles["zone_a_rh"])),
                    "zone_b_relative_humidity_pct": float(api.exchange.get_variable_value(s, self._handles["zone_b_rh"])),
                    "zone_a_temperature_c": float(api.exchange.get_variable_value(s, self._handles["zone_a_temp"])),
                    "zone_b_temperature_c": float(api.exchange.get_variable_value(s, self._handles["zone_b_temp"])),
                    "zone_a_actual_airflow_m3_s": float(api.exchange.get_variable_value(s, self._handles["zone_a_airflow"])),
                    "zone_b_actual_airflow_m3_s": float(api.exchange.get_variable_value(s, self._handles["zone_b_airflow"])),
                }
                if not all(math.isfinite(v) for v in observation.values()):
                    self._error = "non-finite EnergyPlus observation"
                    self._cv.notify_all()
                    return
                self._observation = observation
                self._sequence += 1
                if self._native_first_sequence is None:
                    self._native_first_sequence = self._sequence
                self._time_seconds = (self._sequence - self._native_first_sequence + 1) * TICK_SECONDS
                sequence = self._sequence
                self._cv.notify_all()
                if sequence >= HORIZON_STEPS:
                    self._terminated = True
                    api.runtime.stop_simulation(s)
                    return
                while self._release_sequence < sequence and not self._stop and not self._error:
                    self._cv.wait(timeout=0.25)
                if self._stop:
                    api.runtime.stop_simulation(s)

        # Schedule actuators must be written before EnergyPlus evaluates the
        # timestep schedule/EMS sensors; the zone-init callback is the native
        # barrier immediately before that physical solve.
        api.runtime.callback_begin_zone_timestep_before_init_heat_balance(state, before)
        api.runtime.callback_end_zone_timestep_after_zone_reporting(state, after)
        api.runtime.set_console_output_status(state, False)

        def runner() -> None:
            try:
                output = RUN_ROOT / f"live_{self._session_id}_{os.getpid()}"
                output.mkdir(parents=True, exist_ok=True)
                code = api.runtime.run_energyplus(state, ["-d", str(output), str(self._session_model or WORKING_MODEL)])
                with self._cv:
                    if code:
                        self._error = f"EnergyPlus returned code {code}"
                    self._terminated = True
                    self._cv.notify_all()
            except Exception as exc:  # pragma: no cover - native boundary
                with self._cv:
                    self._error = f"EnergyPlus live session failed: {exc}"
                    self._terminated = True
                    self._cv.notify_all()
            finally:
                api.state_manager.delete_state(state)

        thread = threading.Thread(target=runner, name="d3-energyplus-shared-ventilation", daemon=True)
        with self._cv:
            self._thread = thread
        thread.start()
        deadline = time.monotonic() + 180.0
        with self._cv:
            while self._observation is None and not self._error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._stop = True
                    self._cv.notify_all()
                    raise TimeoutError("timed out waiting for EnergyPlus initial observation")
                self._cv.wait(timeout=remaining)
            if self._error:
                raise D3EnergyPlusError(self._error)
            return dict(self._observation)

    def observe(self) -> dict[str, float]:
        with self._cv:
            if self._observation is None:
                raise D3EnergyPlusError("call reset(seed) before observe()")
            return dict(self._observation)

    def legal_actions(self) -> dict[str, Any]:
        return {
            "native": True,
            "type": "energyplus_ems_shared_ventilation",
            "channels": {name: {"index": i, "type": "continuous", "range": [0.0, 1.0], "unit": "fraction_of_request_max"} for i, name in enumerate(ACTION_NAMES)},
            "shared_capacity_m3_s": SHARED_CAPACITY_M3_S,
            "native_enforcement": "EnergyManagementSystem allocation over Zone Ventilation Air Exchange Flow Rate actuators",
        }

    def step(self, action: Any, dt_seconds: float = TICK_SECONDS) -> dict[str, Any]:
        validated = validate_action(action)
        if isinstance(dt_seconds, bool) or not isinstance(dt_seconds, (int, float)) or not math.isfinite(float(dt_seconds)) or float(dt_seconds) != TICK_SECONDS:
            raise D3EnergyPlusActionError(f"dt_seconds must equal {TICK_SECONDS:g}")
        with self._cv:
            if self._observation is None:
                raise D3EnergyPlusError("call reset(seed) before step()")
            if self._terminated:
                raise D3EnergyPlusActionError("EnergyPlus episode is terminated; call reset()")
            if self._error:
                raise D3EnergyPlusError(self._error)
            self._action = validated
            target = self._sequence + 1
            self._release_sequence = self._sequence
            self._cv.notify_all()
            deadline = time.monotonic() + 180.0
            while self._sequence < target and not self._terminated and not self._error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for EnergyPlus zone timestep")
                self._cv.wait(timeout=remaining)
            if self._error:
                raise D3EnergyPlusError(self._error)
            obs = dict(self._observation)
            done = bool(self._terminated and self._sequence >= target)
            return {"observation": obs, "time_seconds": self._time_seconds, "action": validated, "terminated": done, "truncated": False, "done": done, "delta_t_seconds": TICK_SECONDS, "info": {"backend": self.backend_name, "engine": self.backend_engine, "online": True, "persistent_native_state": True, "shared_capacity_m3_s": SHARED_CAPACITY_M3_S, "constraint_enforced_by": "EnergyPlus EMS"}}

    def close(self) -> None:
        with self._cv:
            self._stop = True
            self._cv.notify_all()
            thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=10.0)
        with self._cv:
            self._thread = None
            self._api = self._state = None
            self._handles = {}
            self._observation = None
            self._terminated = False
            self._error = None
            self._sequence = self._release_sequence = 0
            self._time_seconds = 0.0


def runtime_provenance() -> dict[str, Any]:
    return {
        "adapter_id": "d3_energyplus_shared_ventilation.v1",
        "schema_version": "d3-energyplus-shared-ventilation-v1",
        "native_backend": "EnergyPlus",
        "energyplus_version": "26.1.0",
        "energyplus_binary": str(RUNTIME.relative_to(ROOT)),
        "energyplus_binary_sha256": sha256(RUNTIME),
        "official_source_model": str(SOURCE_MODEL.relative_to(ROOT)),
        "official_source_model_sha256": sha256(SOURCE_MODEL),
        "working_model": str(WORKING_MODEL.relative_to(ROOT)),
        "working_model_sha256": sha256(WORKING_MODEL),
        "source_policy": "pinned official example; deterministic EMS-only construction",
        "surrogate_model_used": False,
        "status_policy": "EVIDENCE_PENDING unless native callback probe passes",
    }


if __name__ == "__main__":
    prepare_working_model()
    print(json.dumps(runtime_provenance(), indent=2, sort_keys=True))
