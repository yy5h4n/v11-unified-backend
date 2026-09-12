"""Fresh native D3 interventions, preserving physical versus budget semantics."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.agent_interface import make_agent_backend, D3_AGENT_ROUTE_IDS
from unified_compiler.mechanism_evidence import compare_fixed_peer


def configuration(route_id):
    if route_id == "d3_modelica_shared_heat":
        return {"channels": [["space_heating_request"], ["dhw_request"]],
                "delivery": [["observation", "allocated_space_heat_w"], ["observation", "allocated_dhw_heat_w"]],
                "zero": {"space_heating_request": 0., "dhw_request": 0.}}
    if route_id == "d3_energyplus_shared_ventilation":
        return {"channels": [["zone_a_airflow_request"], ["zone_b_airflow_request"]],
                "delivery": [["observation", "zone_a_actual_airflow_m3_s"], ["observation", "zone_b_actual_airflow_m3_s"]],
                "zero": {"zone_a_airflow_request": 0., "zone_b_airflow_request": 0.}}
    if route_id == "d3_wntr_water_competition":
        return {"channels": [["shower_valve_open"], ["laundry_valve_open"]], "branch_steps": 2,
                "delivery": [["observation", "served_shower_m3_s"], ["observation", "served_laundry_m3_s"]],
                "zero": {"shower_valve_open": 0., "laundry_valve_open": 0.}}
    if route_id == "d3_ev2gym_electric_competition":
        return {"channels": [["charger_0_rate"], ["charger_1_rate"]], "seed": 3, "prefix_steps": 26,
                "delivery": [["observation", "ports", i, "power_kw"] for i in range(2)],
                "margin": [["observation", "transformer", "remaining_capacity_kw"]] * 2,
                "margin_semantics": "native transformer headroom; overload is observable, not assumed to clip port power",
                "zero": {"charger_0_rate": 0., "charger_1_rate": 0.}}
    if route_id == "d3_citylearn_multi_system":
        return {"channels": [["battery_rate"], ["hvac_rate"]],
                "delivery": [["info", "effect", "storage_electricity_kwh"], ["info", "effect", "hvac_electricity_kwh"]],
                "zero": {"battery_rate": 0., "hvac_rate": 0.}}
    if route_id == "d3_citylearn_multibuilding_competition":
        from d3_citylearn_multibuilding_adapter import DEFAULT_BUILDINGS
        return {"channels": [[b, "battery_rate"] for b in DEFAULT_BUILDINGS],
                "delivery": [["observation", b + ".electrical_storage_soc"] for b in DEFAULT_BUILDINGS],
                "margin": [["observation", b + ".feasible_headroom_kwh"] for b in DEFAULT_BUILDINGS],
                "margin_semantics": "benchmark shared-meter budget computed from native electricity; no native clipping",
                "zero": {b: {"battery_rate": 0., "hvac_rate": 0.} for b in DEFAULT_BUILDINGS}}
    raise KeyError(route_id)


def put(action, path, value):
    parent = action
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value


def probe(route_id):
    spec = configuration(route_id)
    comparisons = []
    for fixed in range(2):
        runs = []
        for value in (0., 1.):
            backend = make_agent_backend(route_id)
            try:
                backend.reset(seed=spec.get("seed", 0))
                for _ in range(spec.get("prefix_steps", 1)):
                    backend.step(deepcopy(spec["zero"]))
                before = backend.observe()
                if route_id == "d3_ev2gym_electric_competition" and not all(p["connected"] for p in before["ports"]):
                    raise ValueError("counterfactual requires both native vehicles connected")
                action = deepcopy(spec["zero"])
                put(action, spec["channels"][fixed], 1.)
                put(action, spec["channels"][1-fixed], value)
                trace = [backend.step(deepcopy(action)) for _ in range(spec.get("branch_steps", 1))]
                receipt = trace[-1]
                runs.append({"prefix_observation": before, "action": action, "receipt": receipt, "intervention_trace": trace})
            finally:
                backend.close()
        result = compare_fixed_peer(*runs, fixed_path=spec["channels"][fixed], varied_path=spec["channels"][1-fixed],
                                    delivery_path=spec["delivery"][fixed], margin_path=spec.get("margin", [None, None])[fixed])
        comparisons.append({"fixed_channel": spec["channels"][fixed], "delivery_path": spec["delivery"][fixed],
                            "margin_path": spec.get("margin", [None, None])[fixed], "runs": runs, **result})
    physical = all(c["fixed_delivery_changed"] for c in comparisons)
    budget = all(c["shared_margin_changed"] for c in comparisons)
    return {"route_id": route_id, "physical_delivery_coupling": physical, "shared_budget_effect": budget,
            "status": "physical_delivery_coupling" if physical else "shared_constraint_only" if budget else "not_demonstrated",
            "margin_semantics": spec.get("margin_semantics"), "comparisons": comparisons,
            "limits": "one declared native prefix per direction; not universal feasibility, task success or controllability"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", required=True, choices=D3_AGENT_ROUTE_IDS)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "generated/fixed_peer_mechanisms_v1")
    args = parser.parse_args()
    try:
        report = probe(args.route)
    except Exception as exc:
        report = {"route_id": args.route, "status": "runtime_error", "error": f"{type(exc).__name__}: {exc}"}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / (args.route + ".json")).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "comparisons"}, ensure_ascii=False))
    raise SystemExit(1 if report["status"] == "runtime_error" else 0)
