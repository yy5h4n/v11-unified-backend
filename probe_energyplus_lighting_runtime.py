#!/usr/bin/env python3
"""Stepwise EnergyPlus lighting-only runtime adapter probe."""
from __future__ import annotations
import argparse, hashlib, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EPLUS = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64"
SOURCE = EPLUS / "ExampleFiles/PurchAirWithDaylightingAndShadeControl.idf"
WEATHER = EPLUS / "WeatherData/USA_VA_Sterling-Washington.Dulles.Intl.AP.724030_TMY3.epw"
BASE = ROOT / "generated/energyplus_lighting_runtime_v1"
REPORT = BASE / "gate_report.json"
if str(EPLUS) not in sys.path: sys.path.insert(0, str(EPLUS))
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

def digest(v): return hashlib.sha256(json.dumps(v, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def save_trace(name, rows):
    path=BASE/f"{name}.jsonl"; path.write_text("".join(json.dumps(row,sort_keys=True,separators=(",",":"))+"\n" for row in rows))
    return {"path":str(path.relative_to(ROOT)),"sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"row_count":len(rows)}

def run_policy(name: str, value: float, replicate: int):
    api, state = EnergyPlusAPI(), None
    state = api.state_manager.new_state(); out = BASE / f"{name}_{replicate}"
    if out.exists(): shutil.rmtree(out)
    out.mkdir(parents=True)
    for var, key in [("Zone Lights Electricity Rate", "WEST ZONE"), ("Daylighting Reference Point 1 Illuminance", "WEST ZONE_DAYLREFPT1"),
                     ("Surface Shading Device Is On Time Fraction", "Zn001:Wall001:Win001"), ("Surface Outside Face Incident Solar Radiation Rate per Area", "Zn001:Wall001:Win001")]:
        api.exchange.request_variable(state, var, key)
    handles, trace, errors = {}, [], []
    def acquire(s):
        if not api.exchange.api_data_fully_ready(s): return False
        if handles: return True
        handles["lighting_actuator"] = api.exchange.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "Office Lighting")
        handles["lighting_power_w"] = api.exchange.get_variable_handle(s, "Zone Lights Electricity Rate", "WEST ZONE")
        handles["illuminance_lux"] = api.exchange.get_variable_handle(s, "Daylighting Reference Point 1 Illuminance", "WEST ZONE_DAYLREFPT1")
        handles["shade_fraction"] = api.exchange.get_variable_handle(s, "Surface Shading Device Is On Time Fraction", "Zn001:Wall001:Win001")
        handles["solar_w_m2"] = api.exchange.get_variable_handle(s, "Surface Outside Face Incident Solar Radiation Rate per Area", "Zn001:Wall001:Win001")
        # Some EnergyPlus builds do not expose the daylight reference-point
        # output through the runtime API; retain it in the trace as fail-closed
        # unavailable while requiring the actuator and causal power signal.
        bad = {k:v for k,v in handles.items() if v < 0 and k != "illuminance_lux"}
        if bad: errors.append(f"invalid handles: {bad}")
        return not bad
    def apply(s):
        if acquire(s): api.exchange.set_actuator_value(s, handles["lighting_actuator"], value)
    def collect(s):
        if api.exchange.warmup_flag(s) or not acquire(s): return
        trace.append({"step":len(trace),"environment_num":api.exchange.current_environment_num(s),"year":api.exchange.year(s),"month":api.exchange.month(s),"day":api.exchange.day_of_month(s),"hour":api.exchange.hour(s),"zone_timestep_number":api.exchange.zone_time_step_number(s),"zone_timesteps_per_hour":api.exchange.num_time_steps_in_hour(s), "lighting_action":value, **{k:(float(api.exchange.get_variable_value(s,h)) if h >= 0 else 0.0) for k,h in handles.items() if k != "lighting_actuator"}})
    api.runtime.callback_begin_zone_timestep_before_init_heat_balance(state, apply)
    api.runtime.callback_end_zone_timestep_after_zone_reporting(state, collect)
    code = api.runtime.run_energyplus(state, ["-w",str(WEATHER),"-d",str(out),str(SOURCE)])
    api.state_manager.delete_state(state)
    if code or errors or not trace: raise RuntimeError(f"runtime replay failed: code={code}, errors={errors}, rows={len(trace)}")
    return {"policy":name,"replicate":replicate,"row_count":len(trace),"trace_digest":digest(trace),"trace":trace,"handles":handles}

def build():
    low1, low2 = run_policy("lighting_0p2", .2, 1), run_policy("lighting_0p2", .2, 2)
    high1, high2 = run_policy("lighting_1p0", 1., 1), run_policy("lighting_1p0", 1., 2)
    time_fields=("environment_num","year","month","day","hour","zone_timestep_number","zone_timesteps_per_hour")
    if any(tuple(a[k] for k in time_fields)!=tuple(b[k] for k in time_fields) for a,b in zip(low1["trace"],high1["trace"])): raise RuntimeError("lighting policy timestep keys differ")
    deltas=[abs(a["lighting_power_w"]-b["lighting_power_w"]) for a,b in zip(low1["trace"],high1["trace"])]
    deterministic=low1["trace_digest"]==low2["trace_digest"] and high1["trace_digest"]==high2["trace_digest"]
    causal=max(deltas)>1e-6
    traces={"lighting_0p2":save_trace("lighting_0p2_trace",low1["trace"]),"lighting_1p0":save_trace("lighting_1p0_trace",high1["trace"])}
    availability={"lighting_power":low1["handles"]["lighting_power_w"]>=0,"illuminance":low1["handles"]["illuminance_lux"]>=0,"shade_state":low1["handles"]["shade_fraction"]>=0,"solar_condition":low1["handles"]["solar_w_m2"]>=0}
    return {"schema_version":"energyplus-lighting-runtime-gate-v1","physical_process_id":"energyplus:lighting_control_only","runtime_action_adapter":{"component_type":"Schedule:Compact","control_type":"Schedule Value","actuator_key":"Office Lighting","tested_actions":[.2,1.0],"supports_lighting_action":True,"supports_blind_action":False},"observation_availability":availability,"observability_gate_for_lighting_power":availability["lighting_power"],"observability_gate_for_illuminance_contract":availability["illuminance"],"passed":bool(deterministic and causal),"runtime_action_adapter_gate":True,"action_available_gate":True,"action_sensitivity_gate":causal,"determinism_gate":deterministic,"evaluator_gate":False,"episode_eligible":False,"episode_count":0,"row_count":low1["row_count"],"maximum_lighting_power_delta_w":max(deltas),"trajectory_digests":{"low_1":low1["trace_digest"],"low_2":low2["trace_digest"],"high_1":high1["trace_digest"],"high_2":high2["trace_digest"]},"causal_trace_refs":traces,"runtime_sha256":hashlib.sha256((EPLUS/"energyplus").read_bytes()).hexdigest(),"source_model_sha256":hashlib.sha256(SOURCE.read_bytes()).hexdigest(),"weather_sha256":hashlib.sha256(WEATHER.read_bytes()).hexdigest(),"gold_actions_released":False}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--check",action="store_true"); a=p.parse_args(); content=json.dumps(build(),indent=2,sort_keys=True)+"\n"
    if a.check:
        if not REPORT.is_file() or REPORT.read_text()!=content: raise SystemExit("stale runtime gate")
    else: REPORT.parent.mkdir(parents=True,exist_ok=True); REPORT.write_text(content)
if __name__ == "__main__": main()
