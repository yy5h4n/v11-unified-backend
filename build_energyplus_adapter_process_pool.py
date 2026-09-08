#!/usr/bin/env python3
"""Consolidate verified EnergyPlus runtime adapters and their causal traces."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
READINESS=ROOT/"generated/energyplus_adapter_readiness_v1.json"
GATES=[ROOT/"generated/energyplus_thermal_runtime_v1/gate_report.json",ROOT/"generated/energyplus_humidity_runtime_v1/gate_report.json",ROOT/"generated/energyplus_lighting_runtime_v1/gate_report.json"]
OUTPUT=ROOT/"generated/energyplus_adapter_process_pool_v1.json"

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def build():
    ledger=json.loads(READINESS.read_text())
    bindings={}
    for row in ledger["responsibilities"]:
        if row["action_capability_process_id"]:
            bindings.setdefault(row["action_capability_process_id"],[]).append({"responsibility_id":row["responsibility_id"],"query":row["query"],"match_scope":"family_only_not_contract_binding"})
    processes=[]
    for path in GATES:
        gate=json.loads(path.read_text())
        if not gate["passed"]: raise RuntimeError(f"runtime gate did not pass: {path}")
        process_id=gate["physical_process_id"]
        processes.append({
            "physical_process_id":process_id,"backend":"EnergyPlus","runtime_version":"26.1.0",
            "runtime_action_adapter":gate["runtime_action_adapter"],
            "gates":{"runtime_action_adapter":gate["runtime_action_adapter_gate"],"action_available":gate["action_available_gate"],"action_sensitivity":gate["action_sensitivity_gate"],"cross_run_determinism":gate["determinism_gate"],"responsibility_evaluator":gate["evaluator_gate"]},
            "causal_effects":{key:value for key,value in gate.items() if key.startswith("maximum_")},
            "causal_trace_refs":gate["causal_trace_refs"],"trace_row_count":gate["row_count"],
            "tentative_family_matches":sorted(bindings.get(process_id,[]),key=lambda row:row["responsibility_id"]),
            "gate_report_ref":str(path.relative_to(ROOT)),"gate_report_sha256":sha(path),
            "canonical_observation_action_process_joined":False,"responsibility_contract_bound":False,
            "episode_eligible":False,"episode_count":0,"gold_actions_released":False,
        })
    count=sum(len(row["tentative_family_matches"]) for row in processes)
    return {"schema_version":"energyplus-action-capability-pool-v1","status":"ACTION_CAPABILITY_PROBES_NOT_CANONICAL_RESPONSIBILITY_PROCESSES","action_capability_probe_count":len(processes),"tentative_family_match_count":count,"contract_bound_responsibility_count":0,"episode_eligible_responsibility_count":0,"episode_count":0,"source_readiness_ledger":str(READINESS.relative_to(ROOT)),"source_readiness_sha256":sha(READINESS),"processes":processes}

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--check",action="store_true");args=parser.parse_args();content=json.dumps(build(),ensure_ascii=False,indent=2,sort_keys=True)+"\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text()!=content: raise SystemExit(f"stale adapter process pool: {OUTPUT}")
        return
    OUTPUT.parent.mkdir(parents=True,exist_ok=True);OUTPUT.write_text(content)
if __name__=="__main__":main()
