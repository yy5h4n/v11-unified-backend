#!/usr/bin/env python3
"""Probe the real SingleFamilyHouse EnergyPlus actuator with prefix-controlled replays."""
from __future__ import annotations
import argparse, hashlib, json, shutil, sys, subprocess
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
EPLUS = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64"
SOURCE = EPLUS / "ExampleFiles/SingleFamilyHouse_TwoSpeed_MultiStageElectricSuppCoil.idf"
WEATHER = EPLUS / "WeatherData/USA_VA_Sterling-Washington.Dulles.Intl.AP.724030_TMY3.epw"
OUT = ROOT / "generated/energyplus_residential_runtime_v1"
WORKING = OUT / "SingleFamilyHouse_TwoSpeed_MultiStageElectricSuppCoil_January_Timestep4.idf"
REPORT = OUT / "gate_report.json"
CONTRACT = ROOT / "contracts/residential_generic_comfort_v1.json"
if str(EPLUS) not in sys.path: sys.path.insert(0, str(EPLUS))
from pyenergyplus.api import EnergyPlusAPI  # noqa: E402

VARS = {"zone_temperature_c": ("Zone Mean Air Temperature", "LIVING ZONE"), "heating_setpoint_c": ("Zone Thermostat Heating Setpoint Temperature", "LIVING ZONE"), "heating_rate_w": ("Zone Air System Sensible Heating Rate", "LIVING ZONE"), "occupant_count": ("Zone People Occupant Count", "LIVING ZONE"), "outdoor_temperature_c": ("Site Outdoor Air Drybulb Temperature", "ENVIRONMENT"), "facility_demand_w": ("Facility Total Building Electricity Demand Rate", "WHOLE BUILDING")}

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def digest(x: Any) -> str: return hashlib.sha256(json.dumps(x, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def pin_model() -> tuple[str, str]:
    text = SOURCE.read_text(encoding="utf-8")
    first_period = """  RunPeriod,
    Run Period 1,            !- Name
    1,                       !- Begin Month
    14,                      !- Begin Day of Month
    ,                        !- Begin Year
    1,                       !- End Month
    14,                      !- End Day of Month"""
    pinned_period = """  RunPeriod,
    Run Period 1,            !- Name
    1,                       !- Begin Month
    1,                       !- Begin Day of Month
    ,                        !- Begin Year
    1,                       !- End Month
    31,                      !- End Day of Month"""
    second_period = """  RunPeriod,
    Run Period 2,"""
    if text.count("Timestep,6;") != 1 or text.count(first_period) != 1 or text.count(second_period) != 1:
        raise RuntimeError("source model replacement anchors are not unique")
    working = text.replace("Timestep,6;", "Timestep,4;").replace(first_period, pinned_period)
    # Keep the source's July one-day environment; all Episode material is
    # explicitly restricted to the January environment.
    if working.count("Timestep,6;") or working.count("Timestep,4;") != 1 or working.count(pinned_period) != 1:
        raise RuntimeError("pinned model replacement failed closed")
    OUT.mkdir(parents=True, exist_ok=True); WORKING.write_text(working, encoding="utf-8")
    return sha(SOURCE), sha(WORKING)

def validate_action(value: float) -> float:
    action = float(value)
    if action not in (21.0, 22.0):
        raise ValueError(f"illegal thermostat command {action}; expected 21.0 or 22.0")
    return action


def run_replay(day: int, strategy: str, replicate: int = 1, policy: Callable[[dict[str, float], int], float] | None = None) -> dict[str, Any]:
    api, state = EnergyPlusAPI(), None
    state = api.state_manager.new_state(); output = OUT / f"run_d{day:02d}_{strategy}_{replicate}"
    if output.exists(): shutil.rmtree(output)
    output.mkdir(parents=True)
    for var, key in VARS.values(): api.exchange.request_variable(state, var, key)
    handles: dict[str, int] = {}; errors: list[str] = []; steps: list[dict[str, Any]] = []; pending: dict[int, tuple[dict[str, float], float | None]] = {}; pre = None; first_action_seen = False
    def acquire(s):
        if not api.exchange.api_data_fully_ready(s): return False
        if handles: return True
        handles["actuator"] = api.exchange.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "Dual Heating Setpoints")
        for role, (var, key) in VARS.items(): handles[role] = api.exchange.get_variable_handle(s, var, key)
        bad = {k: v for k, v in handles.items() if v < 0}
        if bad: errors.append(f"invalid handles {bad}"); return False
        return True
    def obs(s):
        return {k: float(api.exchange.get_variable_value(s, handles[k])) for k in VARS}
    def before(s):
        nonlocal pre, first_action_seen
        if not acquire(s) or api.exchange.warmup_flag(s): return
        month, d = api.exchange.month(s), api.exchange.day_of_month(s)
        # The responsibility profile is fixed at 22 C.  This HVAC model uses a
        # 2 C thermostat cutout, so witness search (performed after contract
        # freeze) certifies the 21 C control input; 22 C is the causal contrast.
        current = obs(s)
        action = 22.0 if d != day else ({"contrast": 22.0, "witness": 21.0}.get(strategy))
        if d == day and policy is not None:
            idx = api.exchange.hour(s) * 4 + api.exchange.zone_time_step_number(s) - 1
            try:
                action = validate_action(policy(dict(current), idx))
            except Exception as exc:
                errors.append(f"policy rejected at step {idx}: {exc}")
                action = 22.0
        if month == 1 and d == day and not first_action_seen:
            pre = current; first_action_seen = True
        if month == 1 and d == day:
            idx = api.exchange.hour(s) * 4 + api.exchange.zone_time_step_number(s) - 1
            if 0 <= idx < 96:
                pending[idx] = (current, None if strategy == "noop" else action)
        if d == day and strategy == "noop": api.exchange.reset_actuator(s, handles["actuator"])
        elif action is not None: api.exchange.set_actuator_value(s, handles["actuator"], action)
    def after(s):
        if not acquire(s) or api.exchange.warmup_flag(s): return
        if api.exchange.month(s) != 1 or api.exchange.day_of_month(s) != day: return
        if len(steps) >= 96: return
        idx = len(steps)
        if idx not in pending:
            errors.append(f"missing pre-action observation for step {idx}")
            return
        pre_step, action = pending.pop(idx)
        steps.append({"step": idx, "time_key": {k: getattr(api.exchange, k)(s) for k in ("current_environment_num", "year", "month", "day_of_month", "hour", "zone_time_step_number", "num_time_steps_in_hour")}, "action": action, "observation": {"occupant_count": pre_step["occupant_count"], "outdoor_temperature_c": pre_step["outdoor_temperature_c"], "zone_temperature_c": pre_step["zone_temperature_c"], "effective_heating_setpoint_c": pre_step["heating_setpoint_c"]}, "effect": {"zone_temperature_c": float(api.exchange.get_variable_value(s, handles["zone_temperature_c"])), "effective_heating_setpoint_c": float(api.exchange.get_variable_value(s, handles["heating_setpoint_c"])), "heating_rate_w": float(api.exchange.get_variable_value(s, handles["heating_rate_w"])), "facility_demand_w": float(api.exchange.get_variable_value(s, handles["facility_demand_w"]))}})
    api.runtime.callback_begin_zone_timestep_before_init_heat_balance(state, before); api.runtime.callback_end_zone_timestep_after_zone_reporting(state, after)
    code = api.runtime.run_energyplus(state, ["-w", str(WEATHER), "-d", str(output), str(WORKING)])
    api.state_manager.delete_state(state)
    if code or errors or len(steps) != 96 or pre is None: raise RuntimeError(f"replay failed day={day} strategy={strategy} code={code} errors={errors} rows={len(steps)} pre={pre is not None}")
    return {"day": day, "strategy": strategy, "replicate": replicate, "pre_action_initial_observation": pre, "pre_action_observation_provenance": "begin-zone-timestep values captured before the current actuator write", "prefix_policy": "22C before target day, target strategy on target day, 22C after target day; identical across strategies", "steps": steps, "trace_digest": digest({"pre_action_initial_observation": pre, "steps": steps})}


def run_observation_policy(day: int, policy: Callable[[dict[str, float], int], float], name: str = "external_policy", replicate: int = 1) -> dict[str, Any]:
    """Replay any legal observation-conditioned policy against one Episode window."""
    return run_replay(day, name, replicate, policy=policy)

def build(smoke: bool = False) -> dict[str, Any]:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    if contract["responsibility_id"] != "rd_37104b57370a" or contract["profile"]["target_c"] != 22.0 or contract["profile"]["tolerance_c"] != 2.0:
        raise RuntimeError("frozen residential contract is not the expected v1 contract")
    original_hash, working_hash = pin_model()
    if smoke: return {"smoke": run_replay(1, "witness")}
    records = [run_replay(day, strategy) for day in range(1, 32) for strategy in ("contrast", "witness", "noop")]
    selected = {(day, strategy): run_replay(day, strategy, 2) for day in range(1, 32) for strategy in ("contrast", "witness", "noop")}
    thermostat_policy = lambda observation, step: 21.0 if observation["zone_temperature_c"] >= 22.0 else 22.0
    dynamic_a = run_observation_policy(15, thermostat_policy, "dynamic", 1)
    dynamic_b = run_observation_policy(15, thermostat_policy, "dynamic", 2)
    for r in records:
        (OUT / "replays.jsonl").open("a", encoding="utf-8").write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
    gates = []
    for day in range(1, 32):
        group = [r for r in records if r["day"] == day]; c, w, n = [next(r for r in group if r["strategy"] == s) for s in ("contrast", "witness", "noop")]
        keys = [s["steps"][i]["time_key"] for i in range(96) for s in [c, w, n]]
        finite = all(v == v and abs(v) != float("inf") for r in group for s in r["steps"] for v in (*s["observation"].values(), *s["effect"].values())) and all(v == v and abs(v) != float("inf") for r in group for v in r["pre_action_initial_observation"].values())
        continuous = all(r["steps"][i]["time_key"]["hour"] == i // 4 and r["steps"][i]["time_key"]["zone_time_step_number"] == i % 4 + 1 and r["steps"][i]["time_key"]["num_time_steps_in_hour"] == 4 for r in group for i in range(96))
        temp_delta = max(abs(c["steps"][i]["effect"]["zone_temperature_c"] - w["steps"][i]["effect"]["zone_temperature_c"]) for i in range(96))
        noop_delta = max(abs(n["steps"][i]["effect"]["zone_temperature_c"] - w["steps"][i]["effect"]["zone_temperature_c"]) for i in range(96))
        def metrics(record):
            errors = [abs(s["effect"]["zone_temperature_c"] - 22.0) for s in record["steps"]]
            return {"hard_violation_fraction": sum(e > 2.0 for e in errors) / len(errors), "mean_abs_error_c": sum(errors) / len(errors)}
        scores = {s: metrics(r) for s, r in (("contrast", c), ("witness", w), ("noop", n))}
        evaluator_delta = scores["contrast"]["mean_abs_error_c"] - scores["witness"]["mean_abs_error_c"]
        gates.append({"day": day, "family_model_evidence": "SingleFamilyHouse_TwoSpeed_MultiStageElectricSuppCoil; LIVING ZONE; LIVING ZONE People; HOUSE OCCUPANCY; Dual Heating Setpoints", "actuator_handles": True, "time_alignment": all(c["steps"][i]["time_key"] == w["steps"][i]["time_key"] == n["steps"][i]["time_key"] for i in range(96)), "time_continuity": continuous, "initial_observation_alignment": c["pre_action_initial_observation"] == w["pre_action_initial_observation"] == n["pre_action_initial_observation"], "complete_96": all(len(r["steps"]) == 96 for r in group), "pre_action_initial_observation": all(r["pre_action_initial_observation"] is not None for r in group), "no_nan": finite, "action_sensitivity": temp_delta >= 0.5, "actuator_effect_observed": temp_delta >= 0.5, "evaluator_scores": scores, "evaluator_sensitivity": evaluator_delta > 0.1, "fixed_witness_feasible": scores["witness"]["hard_violation_fraction"] == 0.0, "no_op_comparison": noop_delta > 0.0, "selected_determinism": all(selected[(day, s)]["trace_digest"] == next(r for r in group if r["strategy"] == s)["trace_digest"] for s in ("contrast", "witness", "noop"))})
    boolean_gate_names = ("time_alignment", "time_continuity", "initial_observation_alignment", "complete_96", "pre_action_initial_observation", "no_nan", "action_sensitivity", "actuator_effect_observed", "evaluator_sensitivity", "fixed_witness_feasible", "no_op_comparison", "selected_determinism")
    for g in gates:
        g["passed"] = all(g[name] for name in boolean_gate_names)
        g["source_gate"] = True
    passed_count = sum(g["passed"] for g in gates)
    dynamic_actions = {step["action"] for step in dynamic_a["steps"]}
    dynamic_gate = {"day": 15, "policy": "if pre_action zone_temperature_c >= 22 then 21C else 22C", "observation_conditioned": True, "nonconstant_actions": dynamic_actions == {21.0, 22.0}, "exact_determinism": dynamic_a["trace_digest"] == dynamic_b["trace_digest"], "trace_digest": dynamic_a["trace_digest"]}
    report = {"schema_version": "energyplus-residential-runtime-v1", "source_model": SOURCE.name, "zone": "LIVING ZONE", "people": "LIVING ZONE People", "occupancy_schedule": "HOUSE OCCUPANCY", "heating_schedule": "Dual Heating Setpoints", "runtime_driver_sha256": sha(Path(__file__)), "policy_bridge": "probe_energyplus_residential_runtime.run_observation_policy(day, policy)", "energyplus_binary_sha256": sha(EPLUS / "energyplus"), "weather_path": str(WEATHER.relative_to(ROOT)), "weather_sha256": sha(WEATHER), "source_model_sha256": original_hash, "working_model_sha256": working_hash, "working_model_path": str(WORKING.relative_to(ROOT)), "replay_count": len(records), "certification_replay_count": len(selected), "candidate_window_count": len(gates), "passed_window_count": passed_count, "gate_report": gates, "dynamic_policy_gate": dynamic_gate, "passed": passed_count > 0 and all(dynamic_gate[k] for k in ("observation_conditioned", "nonconstant_actions", "exact_determinism")) and all(g[name] for g in gates for name in ("time_alignment", "time_continuity", "initial_observation_alignment", "complete_96", "pre_action_initial_observation", "no_nan", "selected_determinism")), "contract_artifact": str(CONTRACT.relative_to(ROOT)), "contract_sha256": sha(CONTRACT), "contract_frozen_at": contract["frozen_at"], "contract_profile": {"target_c": 22.0, "tolerance_c": 2.0, "provenance": "benchmark_design_choice inherited from compile_supported_hvac_episodes.py; not fitted to this residential trace and not query semantics", "soft_loss": "mean_abs_error_c"}, "witness_search": {"action_c": 21.0, "status": "certified after contract freeze", "contrast_action_c": 22.0, "noop": "source schedule (22 C)"}, "command_effective_setpoint_note": "schedule command is not the same as the reported effective heating setpoint; the model's 2 C cutout may report 24 C for a 22 C command", "facility_demand_interpretation": "reported as facility demand only; no HVAC-energy attribution is asserted"}
    (OUT / "replays.jsonl").write_text("".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records), encoding="utf-8")
    return report

def main():
    p = argparse.ArgumentParser(); p.add_argument("--smoke", action="store_true"); p.add_argument("--check", action="store_true"); a = p.parse_args()
    if a.smoke: print(json.dumps(build(True), indent=2)); return
    report = build()
    if a.check:
        if not REPORT.is_file() or json.loads(REPORT.read_text()) != report: raise SystemExit("stale residential runtime report")
    else: REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
if __name__ == "__main__": main()
