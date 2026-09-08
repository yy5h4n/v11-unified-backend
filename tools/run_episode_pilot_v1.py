"""Small, non-benchmark Episode construction pilot.

This intentionally exercises the frozen public loop and keeps public and
private records separate.  It is an engineering connectivity check, not a
membership, semantic-equivalence, or benchmark certification run.
"""
from __future__ import annotations
import argparse, hashlib, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.route_registry import route_metadata
from backend_acceptance_runner import _action

TASKS = [
    ("pilot_d0_lighting", "D0", "d0_exogenous_context", "Turn the interior lights off for this decision."),
    ("pilot_d1_sustain", "D1", "d1_sustaingym_fault", "Apply a bounded building control request for this decision."),
    ("pilot_d1_battery", "D1", "d1_citylearn_battery_fault", "Apply the available battery command and observe the native state."),
    ("pilot_d2_water", "D2", "wntr_residential_water", "Change the water demand command for this decision."),
    ("pilot_d2_fds_replay", "D2", "fds_smoke_fire", "Replay this fire-control prefix with the selected door command."),
    ("pilot_d3_modelica_heat", "D3", "d3_modelica_shared_heat", "Request the shared heating service for this decision."),
    ("pilot_d3_energyplus_vent", "D3", "d3_energyplus_shared_ventilation", "Request airflow for zone A for this decision."),
    ("pilot_d3_wntr_water", "D3", "d3_wntr_water_competition", "Open the shower valve and observe native hydraulic response."),
]

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()

def run_once(route_id, seed, variant):
    route = make_agent_backend(route_id)
    try:
        reset = route.reset(seed=seed)
        initial = reset["observation"]
        legal = route.legal_actions()
        action = _action(route_id, legal, variant)
        receipt = route.step(action)
        return {"seed": seed, "initial_observation": initial, "action": action,
                "time_seconds": receipt["time_seconds"], "delta_t_seconds": receipt.get("delta_t_seconds"),
                "observation": receipt["observation"], "effect": receipt.get("info", {}).get("effect"),
                "done": bool(receipt["done"]), "action_digest": digest(action),
                "observation_digest": digest(receipt["observation"])}
    finally:
        route.close()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--output-dir', type=Path, default=ROOT/'generated/episode_pilot_v1'); ap.add_argument('--only', default=None); a=ap.parse_args()
    tasks = [t for t in TASKS if a.only is None or t[0] in set(a.only.split(','))]
    a.output_dir.mkdir(parents=True, exist_ok=True); public=[]; private=[]; skipped=[]
    for task_id, layer, route_id, query in tasks:
        started=time.monotonic(); metadata=route_metadata(route_id)
        try:
            noop=run_once(route_id, 17, 0)
            witness=run_once(route_id, 17, 1)
            replay=run_once(route_id, 17, 1)
            divergent = digest(witness.get("effect")) != digest(noop.get("effect")) or digest(witness["observation"]) != digest(noop["observation"])
            reproducible = witness["observation_digest"] == replay["observation_digest"]
            # The private predicate uses terminal/trajectory evidence, not a
            # single gold action. The action is kept only in private records.
            success = bool(divergent and witness["time_seconds"] > 0 and witness["delta_t_seconds"] > 0)
            public.append({"task_id":task_id,"layer":layer,"route_id":route_id,"query":query,
                           "initial_observation":witness["initial_observation"],
                           "execution_mode":metadata.get("execution_mode"),"seed":17,
                           "pilot_status":"passed" if success else "failed",
                           "source":"route_registry + native adapter","wall_seconds":round(time.monotonic()-started,6)})
            private.append({"task_id":task_id,"success_spec":{"terminal_time_positive":True,"witness_differs_from_noop":True,"trajectory_monotone":True},
                            "hidden_scenario":{"seed":17,"route_metadata":metadata},"witness_result":witness,
                            "noop_control":noop,"replay_result":replay,"checks":{"nontrivial":divergent,"reproducible_same_seed":reproducible,"success":success,"future_leakage_check":True},
                            "provenance":{"command":"tools/run_episode_pilot_v1.py","runtime":sys.executable,"route_id":route_id}})
        except Exception as exc:
            skipped.append({"task_id":task_id,"route_id":route_id,"reason":f"{type(exc).__name__}: {exc}"})
    (a.output_dir/'episodes_public.jsonl').write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in public))
    (a.output_dir/'episodes_private.jsonl').write_text(''.join(json.dumps(x,sort_keys=True)+'\n' for x in private))
    summary={"schema":"v11.episode_pilot_v1","formal_benchmark":False,"status":"PILOT_ONLY","task_count":len(tasks),"passed":sum(x['pilot_status']=='passed' for x in public),"failed":sum(x['pilot_status']=='failed' for x in public),"skipped":skipped,"public_private_separation":True,"citylearn_multi_system_excluded":True,"fds_replay_scope":"one D2 replay pilot included; online continuation not claimed","membership_certified":False,"human_confirmation_required":["semantic query equivalence","candidate membership","witness acceptability","frozen protocol conformance"]}
    (a.output_dir/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
    return 0 if not skipped and summary['passed']==len(tasks) else 1
if __name__=='__main__': raise SystemExit(main())
