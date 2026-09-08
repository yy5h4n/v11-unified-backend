#!/usr/bin/env python3
"""D2-only EnergyPlus adapter for humidity and indoor-air-quality stepping.

This module intentionally lives outside the shared adapter registry.  It is a
small evidence probe: each ``run`` creates a fresh EnergyPlus state, writes a
legal ventilation schedule action from the API callback, and records the
resulting CO2, generic-contaminant, relative-humidity, and temperature values.
No toy state transition is implemented here.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
EPLUS = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64"
SOURCE_MODEL = EPLUS / "ExampleFiles/HeatPumpIAQP_GenericContamControl.idf"
WEATHER = EPLUS / "WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw"
RUNTIME = EPLUS / "energyplus"
RUN_ROOT = ROOT / "generated/d2_humidity_air_quality_v1/runtime_runs"

if str(EPLUS) not in sys.path:
    sys.path.insert(0, str(EPLUS))
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402


OBSERVABLES: dict[str, tuple[str, str]] = {
    "co2_ppm": ("Zone Air CO2 Concentration", "NORTH ZONE"),
    "generic_contaminant_ppm": ("Zone Air Generic Air Contaminant Concentration", "NORTH ZONE"),
    "relative_humidity_pct": ("Zone Air Relative Humidity", "NORTH ZONE"),
    "zone_temperature_c": ("Zone Mean Air Temperature", "NORTH ZONE"),
}
ACTUATOR = {
    "component_type": "Schedule:Compact",
    "control_type": "Schedule Value",
    "key": "VENTSCHEDULE",
    "semantics": "Controller:MechanicalVentilation availability schedule; 0 disables and 1 enables IAQ ventilation control",
    "legal_action_range": [0.0, 1.0],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_action(action: float) -> float:
    value = float(action)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"illegal D2 ventilation action {action!r}; expected finite value in [0, 1]")
    return value


class EnergyPlusHumidityAirQualityAdapter:
    """EnergyPlus D2 adapter with both replay and a live callback session.

    ``run`` is retained as the immutable whole-trajectory evidence path.  The
    Agent-facing path is ``reset``/``observe``/``step``: one EnergyPlus state
    runs in a dedicated thread, and the runtime callbacks rendezvous with the
    caller at every zone timestep.  Thus a step never reruns a prefix and an
    action supplied by the caller is applied before the next physical solve.
    """

    def __init__(self, run_root: Path = RUN_ROOT) -> None:
        self.run_root = Path(run_root)
        self._last_trace_digest: str | None = None
        self._session_cv = threading.Condition()
        self._session_thread: threading.Thread | None = None
        self._session_stop = False
        self._session_error: str | None = None
        self._session_terminated = False
        self._session_action = 0.0
        self._session_active_action = 0.0
        self._session_observation: dict[str, float] | None = None
        self._session_time_seconds = 0
        self._session_sequence = 0
        self._session_release_sequence = 0
        self._session_step_seconds = 600
        # The pinned official example contains two 24-hour design-day
        # environments at six zone timesteps/hour.  Keeping this bound in the
        # session makes the final callback report termination synchronously.
        self._session_max_steps = 288
        self._session_api: Any = None
        self._session_state: Any = None
        self._session_handles: dict[str, int] = {}
        self._session_errors: list[str] = []

    def reset(self, seed: int = 0) -> dict[str, Any]:
        """Reset backend state before a run.

        EnergyPlus receives no random seed for this official deterministic
        model.  ``seed`` is accepted and recorded so callers cannot silently
        conflate reset with continuation of a prior state.
        """
        if int(seed) != seed:
            raise ValueError("D2 reset seed must be an integer")
        self._close_session()
        self._last_trace_digest = None
        self._start_session(seed=int(seed))
        with self._session_cv:
            if self._session_observation is None:
                raise RuntimeError("EnergyPlus live session produced no initial observation")
            return dict(self._session_observation)

    def legal_actions(self) -> dict[str, Any]:
        """Return the action contract exposed to an Agent."""
        return {"action_ventilation_schedule": {"type": "continuous", "range": [0.0, 1.0]}}

    def close(self) -> None:
        """Stop a live session at its current callback barrier."""
        self._close_session()

    def observe(self) -> dict[str, float]:
        """Return the latest observation without advancing EnergyPlus."""
        with self._session_cv:
            if self._session_observation is None:
                raise RuntimeError("call reset(seed) before observe()")
            return dict(self._session_observation)

    def step(self, action: float, delta_t: float = 600.0, *, steps: int | None = None) -> dict[str, Any]:
        """Advance the persistent EnergyPlus state and return one transition.

        The pinned IAQ model uses six zone timesteps per hour (600 seconds).
        ``delta_t`` must be a positive integer multiple of that physical
        timestep; ``steps`` is an equivalent explicit spelling.  The same
        action is applied at each requested callback boundary.
        """
        action_value = validate_action(action)
        if steps is not None:
            if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
                raise ValueError("steps must be a positive integer")
            count = steps
            delta_seconds = float(steps * self._session_step_seconds)
        else:
            delta_seconds = float(delta_t)
            if not math.isfinite(delta_seconds) or delta_seconds <= 0:
                raise ValueError("delta_t must be a positive finite number of seconds")
            count = int(round(delta_seconds / self._session_step_seconds))
            if count < 1 or abs(delta_seconds - count * self._session_step_seconds) > 1e-6:
                raise ValueError("delta_t must be an integer multiple of 600 seconds")
        result: dict[str, Any] | None = None
        executed = 0
        for _ in range(count):
            result = self._step_once(action_value)
            executed += 1
            if result["terminated"] or result["truncated"]:
                break
        assert result is not None
        result["delta_t_seconds"] = self._session_step_seconds if executed == 1 else executed * self._session_step_seconds
        result["steps"] = executed
        return result

    def _step_once(self, action: float) -> dict[str, Any]:
        with self._session_cv:
            if self._session_observation is None:
                raise RuntimeError("call reset(seed) before step()")
            if self._session_terminated:
                raise RuntimeError("EnergyPlus episode is terminated; call reset()")
            if self._session_error:
                raise RuntimeError(self._session_error)
            target = self._session_sequence + 1
            self._session_action = action
            # Release the callback that produced the current observation;
            # that callback is the barrier preventing the next solve.
            self._session_release_sequence = self._session_sequence
            self._session_cv.notify_all()
            deadline = time.monotonic() + 180.0
            while self._session_sequence < target and not self._session_terminated and not self._session_error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("timed out waiting for EnergyPlus zone timestep")
                self._session_cv.wait(timeout=remaining)
            if self._session_error:
                raise RuntimeError(self._session_error)
            observation = dict(self._session_observation)
            terminated = self._session_terminated and self._session_sequence < target
            # The worker can finish immediately after the final callback.  In
            # that case the requested final observation is still valid.
            if self._session_terminated and self._session_sequence >= target:
                terminated = True
            return {
                "observation": observation,
                "time_seconds": self._session_time_seconds,
                "action": action,
                "terminated": bool(terminated),
                "truncated": False,
                "done": bool(terminated),
                "delta_t_seconds": self._session_step_seconds,
                "steps": 1,
                "info": {"backend": "EnergyPlus", "online": True},
            }

    def _start_session(self, seed: int) -> None:
        if not all(path.is_file() for path in (RUNTIME, SOURCE_MODEL, WEATHER)):
            raise FileNotFoundError("pinned EnergyPlus runtime, official IAQ model, or weather asset is missing")
        api = EnergyPlusAPI()
        state = api.state_manager.new_state()
        for variable, key in OBSERVABLES.values():
            api.exchange.request_variable(state, variable, key)
        with self._session_cv:
            self._session_api = api
            self._session_state = state
            self._session_handles = {}
            self._session_stop = False
            self._session_error = None
            self._session_terminated = False
            self._session_action = 0.0
            self._session_active_action = 0.0
            self._session_observation = None
            self._session_time_seconds = 0
            self._session_sequence = 0
            self._session_release_sequence = 0
            self._session_errors = []

        def acquire(current_state: Any) -> bool:
            if not api.exchange.api_data_fully_ready(current_state):
                return False
            if self._session_handles:
                return not self._session_errors
            handles = {"action": api.exchange.get_actuator_handle(current_state, ACTUATOR["component_type"], ACTUATOR["control_type"], ACTUATOR["key"])}
            handles.update({role: api.exchange.get_variable_handle(current_state, variable, key) for role, (variable, key) in OBSERVABLES.items()})
            invalid = {name: value for name, value in handles.items() if value < 0}
            with self._session_cv:
                if invalid:
                    self._session_errors.append(f"invalid EnergyPlus handles: {invalid}")
                    self._session_error = self._session_errors[-1]
                    self._session_cv.notify_all()
                else:
                    self._session_handles = handles
            return not invalid

        def before_predictor(current_state: Any) -> None:
            if not acquire(current_state):
                return
            with self._session_cv:
                self._session_active_action = self._session_action
                action_value = self._session_active_action
            api.exchange.set_actuator_value(current_state, self._session_handles["action"], action_value)

        def after_reporting(current_state: Any) -> None:
            if api.exchange.warmup_flag(current_state) or not acquire(current_state):
                return
            observation = {role: float(api.exchange.get_variable_value(current_state, self._session_handles[role])) for role in OBSERVABLES}
            if not all(math.isfinite(value) for value in observation.values()):
                with self._session_cv:
                    self._session_errors.append("non-finite EnergyPlus live observation")
                    self._session_cv.notify_all()
                return
            with self._session_cv:
                self._session_observation = observation
                self._session_sequence += 1
                sequence = self._session_sequence
                self._session_time_seconds = self._session_sequence * self._session_step_seconds
                self._session_cv.notify_all()
                if self._session_sequence >= self._session_max_steps:
                    self._session_terminated = True
                    api.runtime.stop_simulation(current_state)
                    return
                while self._session_release_sequence < sequence and not self._session_stop and not self._session_error and not self._session_errors:
                    # Holding the callback at the boundary is the online
                    # synchronization point; no future timestep can run until
                    # the Agent calls step().
                    self._session_cv.wait(timeout=0.25)
                if self._session_stop:
                    api.runtime.stop_simulation(current_state)

        api.runtime.callback_begin_system_timestep_before_predictor(state, before_predictor)
        api.runtime.callback_end_zone_timestep_after_zone_reporting(state, after_reporting)
        api.runtime.set_console_output_status(state, False)

        def runner() -> None:
            try:
                output = self.run_root / f"live_session_{id(self)}"
                output.mkdir(parents=True, exist_ok=True)
                code = api.runtime.run_energyplus(state, ["-w", str(WEATHER), "-d", str(output), str(SOURCE_MODEL)])
                with self._session_cv:
                    if code:
                        self._session_error = f"EnergyPlus live session failed with code {code}"
                    self._session_terminated = True
                    self._session_cv.notify_all()
            except Exception as exc:
                with self._session_cv:
                    self._session_error = f"EnergyPlus live session failed: {exc}"
                    self._session_terminated = True
                    self._session_cv.notify_all()
            finally:
                api.state_manager.delete_state(state)

        thread = threading.Thread(target=runner, name="d2-energyplus-live", daemon=True)
        with self._session_cv:
            self._session_thread = thread
        thread.start()
        deadline = time.monotonic() + 180.0
        with self._session_cv:
            while self._session_observation is None and not self._session_error:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._session_stop = True
                    self._session_cv.notify_all()
                    raise TimeoutError("timed out waiting for initial EnergyPlus observation")
                self._session_cv.wait(timeout=remaining)
            if self._session_error:
                raise RuntimeError(self._session_error)

    def _close_session(self) -> None:
        with self._session_cv:
            thread = self._session_thread
            if thread is None:
                return
            self._session_stop = True
            self._session_cv.notify_all()
        thread.join(timeout=30.0)
        if thread.is_alive():
            raise RuntimeError("EnergyPlus live session did not stop at a callback boundary")
        with self._session_cv:
            self._session_thread = None
            self._session_observation = None
            self._session_terminated = False

    def run(self, action: float, label: str, replicate: int = 1) -> dict[str, Any]:
        action = validate_action(action)
        if not label or any(char in label for char in "/\\"):
            raise ValueError("label must be a non-empty path-safe token")
        if int(replicate) != replicate or int(replicate) < 1:
            raise ValueError("replicate must be a positive integer")
        if not all(path.is_file() for path in (RUNTIME, SOURCE_MODEL, WEATHER)):
            raise FileNotFoundError("pinned EnergyPlus runtime, official IAQ model, or weather asset is missing")

        output = self.run_root / f"{label}_{int(replicate)}"
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)

        api = EnergyPlusAPI()
        state = api.state_manager.new_state()
        for variable, key in OBSERVABLES.values():
            api.exchange.request_variable(state, variable, key)

        handles: dict[str, int] = {}
        errors: list[str] = []
        trace: list[dict[str, Any]] = []

        def acquire(current_state: Any) -> bool:
            if not api.exchange.api_data_fully_ready(current_state):
                return False
            if handles:
                return not errors
            handles["action"] = api.exchange.get_actuator_handle(
                current_state, ACTUATOR["component_type"], ACTUATOR["control_type"], ACTUATOR["key"]
            )
            for role, (variable, key) in OBSERVABLES.items():
                handles[role] = api.exchange.get_variable_handle(current_state, variable, key)
            invalid = {name: value for name, value in handles.items() if value < 0}
            if invalid:
                errors.append(f"invalid EnergyPlus handles: {invalid}")
            return not invalid

        def apply_action(current_state: Any) -> None:
            if acquire(current_state):
                api.exchange.set_actuator_value(current_state, handles["action"], action)

        def collect(current_state: Any) -> None:
            if api.exchange.warmup_flag(current_state) or not acquire(current_state):
                return
            time_key = {
                field: int(getattr(api.exchange, field)(current_state))
                for field in (
                    "current_environment_num",
                    "year",
                    "month",
                    "day_of_month",
                    "hour",
                    "zone_time_step_number",
                    "num_time_steps_in_hour",
                )
            }
            observation = {role: float(api.exchange.get_variable_value(current_state, handles[role])) for role in OBSERVABLES}
            if not all(math.isfinite(value) for value in observation.values()):
                errors.append(f"non-finite observation at step {len(trace)}")
                return
            trace.append({"step": len(trace), "time_key": time_key, "action_ventilation_schedule": action, "observation": observation})

        api.runtime.callback_begin_system_timestep_before_predictor(state, apply_action)
        api.runtime.callback_end_zone_timestep_after_zone_reporting(state, collect)
        try:
            code = api.runtime.run_energyplus(state, ["-w", str(WEATHER), "-d", str(output), str(SOURCE_MODEL)])
        finally:
            api.state_manager.delete_state(state)
        if code or errors or not trace:
            raise RuntimeError(f"D2 EnergyPlus replay failed: code={code}, errors={errors}, rows={len(trace)}")

        trace_digest = canonical_digest(trace)
        self._last_trace_digest = trace_digest
        return {
            "label": label,
            "action": action,
            "replicate": int(replicate),
            "trace": trace,
            "trace_digest": trace_digest,
            "provenance": {
                "backend": "EnergyPlus",
                "backend_version": "26.1.0",
                "runtime_sha256": sha256(RUNTIME),
                "source_model_path": str(SOURCE_MODEL.relative_to(ROOT)),
                "source_model_sha256": sha256(SOURCE_MODEL),
                "weather_path": str(WEATHER.relative_to(ROOT)),
                "weather_sha256": sha256(WEATHER),
                "simulation_period_mode": "official source design-day environments only; source SimulationControl has Run Simulation for Weather File Run Periods = No",
                "weather_application": "pinned and supplied to EnergyPlus, but not used for a weather-file run period by the immutable source model",
                "official_example_modeled_data": True,
                "household_measured_data": False,
                "stepping_callbacks": [
                    "callback_begin_system_timestep_before_predictor",
                    "callback_end_zone_timestep_after_zone_reporting",
                ],
                "reset_semantics": "new EnergyPlusAPI state for every run; source model and weather are immutable",
                "action_actuator": ACTUATOR,
            },
        }
