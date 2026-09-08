#!/usr/bin/env python3
"""Verify a runtime ventilation-opening action on an official EnergyPlus model."""
from __future__ import annotations

import argparse, hashlib, json, re, shutil, sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
EPLUS = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64"
SOURCE = EPLUS / "ExampleFiles/PythonPluginAirflowNetworkOpeningControlByHumidity.idf"
WEATHER = EPLUS / "WeatherData/USA_VA_Sterling-Washington.Dulles.Intl.AP.724030_TMY3.epw"
BASE = ROOT / "generated/energyplus_humidity_runtime_v1"
PREPARED = BASE / "api_controlled.idf"
REPORT = BASE / "gate_report.json"
if str(EPLUS) not in sys.path: sys.path.insert(0, str(EPLUS))
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

VARIABLES = {
    "zone_relative_humidity_pct": ("System Node Relative Humidity", "ZONE 1 NODE"),
    "zone_temperature_c": ("System Node Temperature", "ZONE 1 NODE"),
    "opening_factor": ("AFN Surface Venting Window or Door Opening Factor", "ZN001:WALL001:WIN001"),
}

def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def save_trace(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = BASE / f"{name}.jsonl"; path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    return {"path": str(path.relative_to(ROOT)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "row_count": len(rows)}

def prepare_model() -> None:
    text = SOURCE.read_text(encoding="utf-8", errors="replace")
    pattern = re.compile(r"\n\s*PythonPlugin:Instance,.*?;\s*", re.I | re.S)
    changed, count = pattern.subn("\n! PythonPlugin controller removed; API callback owns the same actuator.\n", text)
    if count != 1: raise RuntimeError(f"expected one PythonPlugin instance, removed {count}")
    BASE.mkdir(parents=True, exist_ok=True); PREPARED.write_text(changed, encoding="utf-8")

def run_policy(name: str, opening: float, replicate: int) -> dict[str, Any]:
    api, state = EnergyPlusAPI(), None
    state = api.state_manager.new_state()
    output = BASE / f"{name}_{replicate}"
    if output.exists(): shutil.rmtree(output)
    output.mkdir(parents=True)
    for variable, key in VARIABLES.values(): api.exchange.request_variable(state, variable, key)
    handles, errors, trace = {}, [], []
    def acquire(current_state) -> bool:
        if not api.exchange.api_data_fully_ready(current_state): return False
        if handles: return True
        handles["actuator"] = api.exchange.get_actuator_handle(current_state, "AirFlow Network Window/Door Opening", "Venting Opening Factor", "Zn001:Wall001:Win001")
        for role, (variable, key) in VARIABLES.items(): handles[role] = api.exchange.get_variable_handle(current_state, variable, key)
        invalid = {key: value for key, value in handles.items() if value < 0}
        if invalid: errors.append(f"invalid handles: {invalid}")
        return not invalid
    def apply_action(current_state) -> None:
        if acquire(current_state): api.exchange.set_actuator_value(current_state, handles["actuator"], opening)
    def collect(current_state) -> None:
        if api.exchange.warmup_flag(current_state) or not acquire(current_state): return
        trace.append({"step": len(trace), "environment_num":api.exchange.current_environment_num(current_state),"year":api.exchange.year(current_state),"month":api.exchange.month(current_state),"day":api.exchange.day_of_month(current_state),"hour":api.exchange.hour(current_state),"zone_timestep_number":api.exchange.zone_time_step_number(current_state),"zone_timesteps_per_hour":api.exchange.num_time_steps_in_hour(current_state), "action_opening_factor": opening, **{role: float(api.exchange.get_variable_value(current_state, handles[role])) for role in VARIABLES}})
    api.runtime.callback_begin_system_timestep_before_predictor(state, apply_action)
    api.runtime.callback_end_zone_timestep_after_zone_reporting(state, collect)
    code = api.runtime.run_energyplus(state, ["-w", str(WEATHER), "-d", str(output), str(PREPARED)])
    api.state_manager.delete_state(state)
    if code or errors or not trace: raise RuntimeError(f"humidity runtime failed: code={code}, errors={errors}, rows={len(trace)}")
    return {"policy": name, "opening_factor": opening, "replicate": replicate, "row_count": len(trace), "trace_digest": digest(trace), "trace": trace}

def build() -> dict[str, Any]:
    prepare_model()
    closed_a, closed_b = run_policy("closed", 0.0, 1), run_policy("closed", 0.0, 2)
    open_a, open_b = run_policy("open", 1.0, 1), run_policy("open", 1.0, 2)
    deterministic = closed_a["trace_digest"] == closed_b["trace_digest"] and open_a["trace_digest"] == open_b["trace_digest"]
    if closed_a["row_count"] != open_a["row_count"]: raise RuntimeError("humidity horizons differ")
    time_fields=("environment_num","year","month","day","hour","zone_timestep_number","zone_timesteps_per_hour")
    if any(tuple(a[k] for k in time_fields)!=tuple(b[k] for k in time_fields) for a,b in zip(closed_a["trace"],open_a["trace"])): raise RuntimeError("humidity policy timestep keys differ")
    pairs = list(zip(closed_a["trace"], open_a["trace"]))
    max_rh_delta = max(abs(a["zone_relative_humidity_pct"] - b["zone_relative_humidity_pct"]) for a, b in pairs)
    max_temp_delta = max(abs(a["zone_temperature_c"] - b["zone_temperature_c"]) for a, b in pairs)
    max_opening_delta = max(abs(a["opening_factor"] - b["opening_factor"]) for a, b in pairs)
    action_sensitive = max_opening_delta > 0.99 and (max_rh_delta > 1e-6 or max_temp_delta > 1e-6)
    traces = {"closed": save_trace("closed_trace", closed_a["trace"]), "open": save_trace("open_trace", open_a["trace"])}
    return {
        "schema_version": "energyplus-humidity-runtime-gate-v1", "physical_process_id": "energyplus:humidity_iaq_ventilation_opening",
        "source_model_role": "official EnergyPlus AFN humidity/opening example with bundled plugin removed and equivalent actuator exposed to benchmark runtime",
        "runtime_action_adapter": {"component_type": "AirFlow Network Window/Door Opening", "control_type": "Venting Opening Factor", "actuator_key": "Zn001:Wall001:Win001", "legal_action_range": [0.0, 1.0]},
        "passed": deterministic and action_sensitive, "runtime_action_adapter_gate": True, "action_available_gate": True,
        "action_sensitivity_gate": action_sensitive, "determinism_gate": deterministic, "evaluator_gate": False,
        "episode_eligible": False, "episode_count": 0, "row_count": closed_a["row_count"],
        "maximum_relative_humidity_delta_pct": max_rh_delta, "maximum_zone_temperature_delta_c": max_temp_delta,
        "maximum_observed_opening_factor_delta": max_opening_delta,
        "trajectory_digests": {"closed_1": closed_a["trace_digest"], "closed_2": closed_b["trace_digest"], "open_1": open_a["trace_digest"], "open_2": open_b["trace_digest"]},
        "causal_trace_refs": traces,
        "prepared_model_sha256": hashlib.sha256(PREPARED.read_bytes()).hexdigest(), "gold_actions_released": False,
        "runtime_sha256":hashlib.sha256((EPLUS/"energyplus").read_bytes()).hexdigest(),"source_model_sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),"weather_sha256":hashlib.sha256(WEATHER.read_bytes()).hexdigest(),
    }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--check", action="store_true"); args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != content: raise SystemExit(f"stale humidity gate: {REPORT}")
        return
    REPORT.parent.mkdir(parents=True, exist_ok=True); REPORT.write_text(content, encoding="utf-8")
if __name__ == "__main__": main()
