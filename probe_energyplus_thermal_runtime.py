#!/usr/bin/env python3
"""Probe a stepwise EnergyPlus thermostat action interface through its Python API."""
from __future__ import annotations

import argparse, hashlib, json, shutil, sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
EPLUS = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64"
SOURCE = EPLUS / "ExampleFiles/5ZoneAirCooledWithSpacesDaylightingIntMass.idf"
WEATHER = EPLUS / "WeatherData/USA_VA_Sterling-Washington.Dulles.Intl.AP.724030_TMY3.epw"
BASE = ROOT / "generated/energyplus_thermal_runtime_v1"
REPORT = BASE / "gate_report.json"
if str(EPLUS) not in sys.path:
    sys.path.insert(0, str(EPLUS))
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

VARIABLES = {
    "zone_temperature_c": ("Zone Mean Air Temperature", "ZONE 1"),
    "heating_setpoint_c": ("Zone Thermostat Heating Setpoint Temperature", "ZONE 1"),
    "heating_rate_w": ("Zone Air System Sensible Heating Rate", "ZONE 1"),
    "occupant_count": ("Zone People Occupant Count", "ZONE 1"),
    "outdoor_temperature_c": ("Site Outdoor Air Drybulb Temperature", "ENVIRONMENT"),
    "facility_electric_demand_w": ("Facility Total Building Electricity Demand Rate", "WHOLE BUILDING"),
}

def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def save_trace(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = BASE / f"{name}.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "row_count": len(rows)}

def run_policy(name: str, setpoint_c: float, replicate: int) -> dict[str, Any]:
    api = EnergyPlusAPI()
    state = api.state_manager.new_state()
    output = BASE / f"{name}_{replicate}"
    if output.exists(): shutil.rmtree(output)
    output.mkdir(parents=True)
    for variable, key in VARIABLES.values():
        api.exchange.request_variable(state, variable, key)
    handles: dict[str, int] = {}
    trace: list[dict[str, float | int]] = []
    errors: list[str] = []

    def acquire(current_state) -> bool:
        if not api.exchange.api_data_fully_ready(current_state): return False
        if handles: return True
        handles["schedule"] = api.exchange.get_actuator_handle(current_state, "Schedule:Compact", "Schedule Value", "Htg-SetP-Sch")
        for role, (variable, key) in VARIABLES.items():
            handles[role] = api.exchange.get_variable_handle(current_state, variable, key)
        invalid = {key: value for key, value in handles.items() if value < 0}
        if invalid: errors.append(f"invalid handles: {invalid}")
        return not invalid

    def apply_action(current_state) -> None:
        if acquire(current_state):
            api.exchange.set_actuator_value(current_state, handles["schedule"], setpoint_c)

    def collect(current_state) -> None:
        if api.exchange.warmup_flag(current_state) or not acquire(current_state): return
        trace.append({
            "step": len(trace), "environment_num": api.exchange.current_environment_num(current_state),
            "year": api.exchange.year(current_state), "month": api.exchange.month(current_state),
            "day": api.exchange.day_of_month(current_state), "hour": api.exchange.hour(current_state),
            "zone_timestep_number":api.exchange.zone_time_step_number(current_state),"zone_timesteps_per_hour":api.exchange.num_time_steps_in_hour(current_state), "action_setpoint_c": setpoint_c,
            **{role: float(api.exchange.get_variable_value(current_state, handles[role])) for role in VARIABLES},
        })

    api.runtime.callback_begin_zone_timestep_before_init_heat_balance(state, apply_action)
    api.runtime.callback_end_zone_timestep_after_zone_reporting(state, collect)
    code = api.runtime.run_energyplus(state, ["-w", str(WEATHER), "-d", str(output), str(SOURCE)])
    api.state_manager.delete_state(state)
    if code or errors or not trace:
        raise RuntimeError(f"runtime replay failed: code={code}, errors={errors}, rows={len(trace)}")
    return {"policy": name, "setpoint_c": setpoint_c, "replicate": replicate, "row_count": len(trace), "trace_digest": digest(trace), "trace": trace}

def build() -> dict[str, Any]:
    low_a, low_b = run_policy("setpoint_21c", 21.0, 1), run_policy("setpoint_21c", 21.0, 2)
    high_a, high_b = run_policy("setpoint_22c", 22.0, 1), run_policy("setpoint_22c", 22.0, 2)
    deterministic = low_a["trace_digest"] == low_b["trace_digest"] and high_a["trace_digest"] == high_b["trace_digest"]
    if low_a["row_count"] != high_a["row_count"]: raise RuntimeError("runtime horizons differ")
    time_fields=("environment_num","year","month","day","hour","zone_timestep_number","zone_timesteps_per_hour")
    if any(tuple(a[k] for k in time_fields)!=tuple(b[k] for k in time_fields) for a,b in zip(low_a["trace"],high_a["trace"])): raise RuntimeError("runtime policy timestep keys differ")
    pairs = zip(low_a["trace"], high_a["trace"])
    deltas = [(abs(a["zone_temperature_c"] - b["zone_temperature_c"]), abs(a["heating_rate_w"] - b["heating_rate_w"])) for a, b in pairs]
    max_temperature_delta = max(value[0] for value in deltas)
    max_heating_delta = max(value[1] for value in deltas)
    action_sensitive = max_temperature_delta > 1e-6 and max_heating_delta > 1e-6
    passed = deterministic and action_sensitive
    traces = {"setpoint_21c": save_trace("setpoint_21c_trace", low_a["trace"]), "setpoint_22c": save_trace("setpoint_22c_trace", high_a["trace"])}
    return {
        "schema_version": "energyplus-thermal-runtime-gate-v1", "physical_process_id": "energyplus:thermal_occupancy_energy",
        "runtime_action_adapter": {"component_type": "Schedule:Compact", "control_type": "Schedule Value", "actuator_key": "Htg-SetP-Sch", "declared_safe_range_c_not_exhaustively_tested": [16.7, 23.2], "tested_actions_c": [21.0, 22.0]},
        "passed": passed, "runtime_action_adapter_gate": True, "action_available_gate": True,
        "action_sensitivity_gate": action_sensitive, "determinism_gate": deterministic, "evaluator_gate": False,
        "episode_eligible": False, "episode_count": 0, "row_count": low_a["row_count"],
        "maximum_zone_temperature_delta_c": max_temperature_delta, "maximum_heating_rate_delta_w": max_heating_delta,
        "trajectory_digests": {"low_1": low_a["trace_digest"], "low_2": low_b["trace_digest"], "high_1": high_a["trace_digest"], "high_2": high_b["trace_digest"]},
        "causal_trace_refs": traces,
        "runtime_sha256":hashlib.sha256((EPLUS/"energyplus").read_bytes()).hexdigest(),"source_model_sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),"weather_sha256":hashlib.sha256(WEATHER.read_bytes()).hexdigest(),
        "gold_actions_released": False,
    }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--check", action="store_true"); args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != content: raise SystemExit(f"stale runtime gate: {REPORT}")
        return
    REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(content, encoding="utf-8")

if __name__ == "__main__": main()
