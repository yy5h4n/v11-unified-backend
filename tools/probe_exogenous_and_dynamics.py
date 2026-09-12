"""Targeted D0/D1/D2 checks over native states, not task verdicts.

Healthy controls alter only the experimenter's schedule before the first
action. They are diagnostic counterfactuals, not newly admitted tasks/routes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS

D2_FIELDS = {"energyplus_iaq": "co2_ppm", "wntr_residential_water": "tank_level_m",
             "fds_smoke_fire": "room_b_temperature_c", "modelica_buildings_aixlib": "room_a_temperature_c"}


def healthy_schedule(route_id):
    if route_id == "d0_exogenous_context":
        from unified_compiler.adapters.d0_exogenous_context import ExogenousContextSchedule
        return ExogenousContextSchedule()
    if route_id == "d1_sustaingym_fault":
        from unified_compiler.adapters.d1_fault_mechanism import ActuatorFaultSchedule
        return ActuatorFaultSchedule()
    if route_id == "d1_citylearn_battery_fault":
        from unified_compiler.adapters.citylearn_battery_fault import BatteryFaultSchedule
        return BatteryFaultSchedule()
    if route_id == "d1_ev2gym_fault":
        from unified_compiler.adapters.ev2gym_fault import ChargerFaultSchedule
        return ChargerFaultSchedule()
    if route_id == "d1_discrete_device_fault":
        from unified_compiler.adapters.d1_discrete_device_fault import DiscreteFaultSchedule
        return DiscreteFaultSchedule()
    raise KeyError(route_id)


def physical_outcome(route_id, receipt):
    obs = receipt["observation"]
    if route_id in D2_FIELDS: return obs[D2_FIELDS[route_id]]
    if route_id == "d0_exogenous_context": return {"context": obs["context"], "door": obs["devices"]["front_door"]}
    if route_id == "d1_sustaingym_fault": return obs["zone_temperatures_c"]
    if route_id == "d1_citylearn_battery_fault": return receipt["info"]["effect"]["battery_soc"]
    if route_id == "d1_ev2gym_fault": return receipt["info"]["effect"]["delivered_charging_kwh"]
    if route_id == "d1_discrete_device_fault": return obs["devices"]["garage_door.main"]["state"]
    raise KeyError(route_id)


def action_for(route_id, schema, index, branch):
    if route_id in D2_FIELDS: return float(branch)
    if route_id == "d0_exogenous_context":
        return {"kind": "act", "command": {"target": "interior_lights", "operation": "off"}}
    if route_id == "d1_sustaingym_fault":
        zeros = schema.get("zero_required_zones", [])
        return [0. if i in zeros else -0.05 for i in range(schema["shape"][0])]
    if route_id == "d1_citylearn_battery_fault": return .25
    if route_id == "d1_ev2gym_fault": return {"type": "SET_CHARGE_POWER", "kw": 1.}
    if route_id == "d1_discrete_device_fault":
        return {"kind": "act", "commands": [{"device_id": "garage_door.main", "capability": "garage.door",
                  "operation": "open" if index < 2 else "close", "parameters": {}}]}
    raise KeyError(route_id)


def probe(route_id):
    runs = []
    for branch in (0, 1):
        kwargs = {}
        if route_id == "d1_discrete_device_fault":
            from unified_compiler.adapters.d1_discrete_device_fault import DiscreteFaultSchedule, DiscreteFaultWindow
            kwargs["schedule"] = DiscreteFaultSchedule((DiscreteFaultWindow("garage_door.main", 2, 6, "jammed"),))
        backend = make_agent_backend(route_id, **kwargs)
        try:
            # Fault/context controls are configured by the experimenter,
            # never exposed as actions that the model could select.
            if branch == 0 and route_id not in D2_FIELDS:
                if route_id == "d1_sustaingym_fault":
                    # Open the verified native episode first; only its test
                    # schedule is changed before initialization/action.
                    backend.route._episode = backend.route.open_episode()
                    backend.route._episode.schedule = healthy_schedule(route_id)
                else:
                    backend.route.schedule = healthy_schedule(route_id)
            initial = backend.reset(seed=0)
            trace = []
            for index in range(3 if route_id in D2_FIELDS else 8):
                action = action_for(route_id, backend.legal_actions(), index, branch)
                receipt = backend.step(deepcopy(action))
                trace.append(receipt)
            runs.append({"initial": initial, "trace": trace,
                         "outcomes": [physical_outcome(route_id, r) for r in trace]})
        finally:
            backend.close()
    before_equal = runs[0]["initial"]["observation"] == runs[1]["initial"]["observation"]
    same_actions = [r["action"] for r in runs[0]["trace"]] == [r["action"] for r in runs[1]["trace"]]
    differing_steps = [i for i, (a,b) in enumerate(zip(runs[0]["outcomes"], runs[1]["outcomes"])) if a != b]
    dynamic = any(run["outcomes"][i] != run["outcomes"][i-1] for run in runs for i in range(1,len(run["outcomes"])))
    verified = before_equal and bool(differing_steps) and (dynamic if route_id in D2_FIELDS else same_actions)
    return {"route_id": route_id, "status": "observed" if verified else "not_demonstrated",
            "same_initial_public_state": before_equal, "same_requested_actions": same_actions,
            "native_outcome_differing_steps": differing_steps, "state_evolves_under_held_action": dynamic if route_id in D2_FIELDS else None,
            "tested_mechanism": "continuous_state_and_action_effect" if route_id in D2_FIELDS else "external_context_effect" if route_id.startswith("d0") else "fault_changes_native_outcome",
            "runs": runs, "task_feasibility_verified": False,
            "limits": "one seed and one action sequence; no task thresholds, safety or controllability guarantee"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", required=True, choices=[r for r in PUBLIC_ROUTE_IDS if not r.startswith("d3_")])
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated/exogenous_dynamics_mechanisms_v1")
    args = parser.parse_args()
    try:
        result = probe(args.route)
    except Exception as exc:
        result = {"route_id": args.route, "status": "runtime_error", "error": f"{type(exc).__name__}: {exc}"}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / (args.route + ".json")).write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k:v for k,v in result.items() if k != "runs"}, ensure_ascii=False))
    raise SystemExit(1 if result["status"] == "runtime_error" else 0)
