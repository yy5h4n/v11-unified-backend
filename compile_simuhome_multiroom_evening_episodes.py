#!/usr/bin/env python3
"""Compile source-grounded, explicit multi-room SimuHome episodes."""
from __future__ import annotations
import hashlib, json, math, subprocess, sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parent; WORKSPACE=ROOT.parents[4]
DATA=WORKSPACE/"external/SimuHome/data/benchmark"
CONTRACT_PATH=ROOT/"contracts/simuhome_multiroom_evening_warmth_v1.json"
CATALOG_PATH=ROOT/"responsibility_ai_coding_v1/NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
RELEASE_DIR=ROOT/"generated/simuhome_multiroom_evening_release_v1"
RID="rd_split_b8457e559b4d"; QUERY_ID="si_split_b8457e559b4d"
PUBLIC_FORBIDDEN_FIELDS={"gold_actions","witness_policy","witness_trace","replay_certificates"}
sys.path.insert(0,str(ROOT))
from unified_compiler.simuhome_multiroom_thermal_adapter import SimuHomeMultiroomThermalAdapter,canonical_json,digest_json

def write_json(p,v): p.write_text(json.dumps(v,ensure_ascii=False,sort_keys=True,indent=2)+"\n")
def write_jsonl(p,rows): p.write_text("".join(canonical_json(x)+"\n" for x in rows))
def sha256_file(path):
    h=hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b""): h.update(chunk)
    return h.hexdigest()
def manifest_digest(paths,base):
    manifest={str(p.relative_to(base)):sha256_file(p) for p in sorted(paths)}
    return digest_json(manifest),len(manifest)

def load_catalog_record():
    c=json.loads(CATALOG_PATH.read_text()); rows=[r for r in c["queries"] if r.get("responsibility_id")==RID]
    if len(rows)!=1: raise RuntimeError(f"catalog_record_count:{len(rows)}")
    return c,rows[0]

def public_payload_safe(value):
    if isinstance(value,dict):
        return not (PUBLIC_FORBIDDEN_FIELDS & set(value)) and all(public_payload_safe(v) for v in value.values())
    if isinstance(value,list): return all(public_payload_safe(v) for v in value)
    return True

def replay_matrix_complete_and_synchronized(replay, room_ids):
    if len(replay.get("trace",[]))<2: return False
    for step in replay["trace"]:
        observations=step.get("observation",{})
        if set(observations)!=set(room_ids): return False
        if len({x["virtual_time"] for x in observations.values()})!=1: return False
        if len({x["current_tick"] for x in observations.values()})!=1: return False
    return True

def scan_candidates():
    candidates=[]; failures=[]; seen=set()
    for path in sorted(DATA.glob("*.json")):
        try:
            config=json.loads(path.read_text())["initial_home_config"]
            hour=datetime.strptime(config["base_time"],"%Y-%m-%d %H:%M:%S").hour
            if not 17<=hour<23: failures.append({"source":path.name,"code":"NOT_EVENING"}); continue
            selected={}
            for room,v in config.get("rooms",{}).items():
                temp=float(v["state"]["temperature"])/100
                devices=[d for d in v.get("devices",[]) if d.get("device_type") in {"air_conditioner","heat_pump"}]
                if 20<=temp<=24 and devices:
                    d=sorted(devices,key=lambda x:(x["device_type"]!="air_conditioner",x["device_id"]))[0]
                    selected[room]={"room_id":room,"initial_temperature_c":temp,"device_id":d["device_id"],"device_type":d["device_type"]}
            if len(selected)<2: failures.append({"source":path.name,"code":"FEWER_THAN_TWO_ELIGIBLE_ROOMS"}); continue
            digest=digest_json(config)
            if digest in seen: failures.append({"source":path.name,"code":"DUPLICATE_SOURCE_CONFIG"}); continue
            seen.add(digest)
            # A heat-pump-only room above target cannot be improved because the
            # checked SimuHome bridge supports heat/off but not cooling.
            blocked=[r for r,v in selected.items() if v["device_type"]=="heat_pump" and v["initial_temperature_c"]>22.0]
            if blocked:
                failures.append({"source":path.name,"code":"HEAT_PUMP_CANNOT_IMPROVE_ABOVE_TARGET","rooms":blocked}); continue
            candidates.append({"source_path":path,"config":config,"config_digest":digest,"rooms":selected,"room_ids":sorted(selected)})
        except Exception as e: failures.append({"source":path.name,"code":f"INVALID_CONFIG:{type(e).__name__}"})
    return candidates,failures

def matrix(replay): return {r:[float(s["observation"][r]["temperature_c"]) for s in replay["trace"]] for r in replay["room_ids"]}
def evaluate(replay,target,lo,hi):
    m=matrix(replay); vals=[x for xs in m.values() for x in xs]; maes={r:sum(abs(x-target) for x in xs)/len(xs) for r,xs in m.items()}
    outside=sum((lo-x if x<lo else x-hi if x>hi else 0) for x in vals)*replay["sample_minutes"]/60
    return {"trajectory_complete":len(replay["trace"])>=2,"no_nan":all(math.isfinite(x) for x in vals),"hard_violation_count":sum(not lo<=x<=hi for x in vals),"hard_violation_fraction":sum(not lo<=x<=hi for x in vals)/len(vals),"mean_abs_error_c":sum(maes.values())/len(maes),"worst_room_mae_c":max(maes.values()),"room_mae_c":maes,"aggregate_degree_hours":outside,"min_temperature_c":min(vals),"max_temperature_c":max(vals)}
def process_signature(c,w,n,x): return digest_json({"room_ids":sorted(c["room_ids"]),"device_families":sorted(c["rooms"][r]["device_type"] for r in c["room_ids"]),"rounding_c":.001,"witness":{r:[round(v,3) for v in z] for r,z in sorted(matrix(w).items())},"noop":{r:[round(v,3) for v in z] for r,z in sorted(matrix(n).items())},"contrast":{r:[round(v,3) for v in z] for r,z in sorted(matrix(x).items())}})

def main(out=RELEASE_DIR):
    out=Path(out); out.mkdir(parents=True,exist_ok=True); contract=json.loads(CONTRACT_PATH.read_text()); catalog,record=load_catalog_record()
    catalog_contract_checks={"responsibility_id":record.get("responsibility_id")==contract.get("responsibility_id")==RID,"query_id":record.get("standing_intent_id")==contract.get("query_id")==QUERY_ID,"natural_query":record.get("natural_query")==contract.get("natural_query"),"lifecycle":record.get("lifecycle")==contract.get("lifecycle")}
    if not all(catalog_contract_checks.values()): raise RuntimeError(f"catalog_contract_mismatch:{catalog_contract_checks}")
    target=float(contract["profile"]["target_c"]); band=contract["profile"]["temperature_band_c"]; lo,hi=float(band["lower"]),float(band["upper"]); candidates,failures=scan_candidates(); public=[]; private=[]; gate_rows=[]; signatures=set()
    simuhome_root=WORKSPACE/"external/SimuHome"
    backend_sha,backend_count=manifest_digest(list((simuhome_root/"src/simulator").rglob("*.py")),simuhome_root)
    corpus_sha,corpus_count=manifest_digest(list(DATA.glob("*.json")),simuhome_root)
    commit=subprocess.run(["git","-C",str(simuhome_root),"rev-parse","HEAD"],capture_output=True,text=True,check=False).stdout.strip() or "unavailable"
    git_status=subprocess.run(["git","-C",str(simuhome_root),"status","--porcelain"],capture_output=True,text=True,check=False).stdout
    try:
        import pydantic
        pydantic_version=pydantic.__version__
    except Exception: pydantic_version="unavailable"
    adapter_path=ROOT/"unified_compiler/simuhome_multiroom_thermal_adapter.py"
    fixed_hashes={"adapter_sha256":sha256_file(adapter_path),"compiler_sha256":sha256_file(__file__),"contract_sha256":sha256_file(CONTRACT_PATH),"catalog_sha256":sha256_file(CATALOG_PATH),"simuhome_git_commit":commit,"simuhome_git_dirty":bool(git_status.strip()),"simuhome_git_status_sha256":hashlib.sha256(git_status.encode()).hexdigest(),"backend_python_manifest_sha256":backend_sha,"backend_python_manifest_file_count":backend_count,"corpus_manifest_sha256":corpus_sha,"corpus_manifest_file_count":corpus_count,"pyproject_sha256":sha256_file(simuhome_root/"pyproject.toml"),"runtime":{"python":sys.version,"pydantic":pydantic_version}}
    for cnd in candidates:
        try:
            ids=cnd["room_ids"]
            def witness(_i,_o): return {r:{"mode":"auto","target_c":target} for r in ids}
            def contrast(_i,o): return {r:{"mode":"auto","target_c":hi if o[r]["temperature_c"]<=target else lo} for r in ids}
            def run(p): return SimuHomeMultiroomThermalAdapter(cnd["config"],room_ids=ids).replay(p)
            w,n,x=run(witness),run(None),run(contrast); wr,nr,xr=run(witness),run(None),run(contrast); we,ne,xe=(evaluate(a,target,lo,hi) for a in (w,n,x))
            gates={"trajectory_complete_all_arms":all(replay_matrix_complete_and_synchronized(a,ids) for a in (w,n,x)),"all_selected_rooms_explicit":set(ids)==set(cnd["rooms"]),"all_rooms_same_home_and_clock":len({w["source_config_sha256"],n["source_config_sha256"],x["source_config_sha256"]})==1,"preaction_alignment":w["trace"][0]["observation"]==n["trace"][0]["observation"]==x["trace"][0]["observation"],"action_before_effect_alignment":w["trace"][0]["action"] is not None and all(w["trace"][0]["observation"][r]["temperature_c"]==cnd["rooms"][r]["initial_temperature_c"] for r in ids),"virtual_evening_scope_match_all_arms":all(all(17<=int(next(iter(s["observation"].values()))["virtual_time"][11:13])<23 for s in a["trace"]) for a in (w,n,x)),"fixed_witness_feasible_all_rooms":we["hard_violation_count"]==0,"worst_room_mae_improvement":ne["worst_room_mae_c"]-we["worst_room_mae_c"]>=float(contract["opportunity_predicate"]["minimum_worst_room_mae_delta_c"]),"aggregate_degree_hours":we["aggregate_degree_hours"]<=ne["aggregate_degree_hours"],"action_sensitive":matrix(w)!=matrix(n),"deterministic":digest_json(w)==digest_json(wr) and digest_json(n)==digest_json(nr) and digest_json(x)==digest_json(xr),"noop_compared":bool(n["trace"]),"contrast_compared":bool(x["trace"]),"no_nan":we["no_nan"] and ne["no_nan"] and xe["no_nan"],"one_primary_responsibility":contract["responsibility_id"]==RID==record["responsibility_id"]}
            gates["passed"]=all(gates.values())
            if not all(gates.values()): failures.append({"source":cnd["source_path"].name,"code":"CERTIFICATION_FAILED"}); continue
            sig=process_signature(cnd,w,n,x)
            if sig in signatures: failures.append({"source":cnd["source_path"].name,"code":"DUPLICATE_RESPONSIBILITY_PROCESS"}); continue
            signatures.add(sig); eid="simuhome_multiroom_v1__"+digest_json({"responsibility_id":RID,"source_config_sha256":cnd["config_digest"]})[:20]
            physical_id="simuhome_multiroom_source__"+cnd["config_digest"][:20]
            statuses={"physical_status":"positive_opportunity","semantic_status":contract["semantic_status"],"authorization_status":contract["authorization_status"],"release_status":contract["release_status"],"preview_only":True}
            p={"schema_version":"responsibility-episode-simuhome-multiroom-v1","episode_id":eid,"responsibility_id":RID,"query_id":QUERY_ID,"natural_query":record["natural_query"],"contract_id":contract["contract_id"],"room_ids":ids,"horizon_steps":len(w["trace"]),"observation_interval_minutes":w["sample_minutes"],"initial_observation":w["trace"][0]["observation"],"observation_schema":{"virtual_time":"SimuHome virtual local time","rooms":"mapping of every selected explicit room to temperature and causal thermal device"},"legal_action_schema":{**contract["legal_action_schema"],"modes":["auto","heat","cool","off"],"target_c_range":[7.0,32.0],"device_constraints":{"air_conditioner":["heat","cool","off"],"heat_pump":["heat","off"]}},"profile":{**contract["profile"],"parameter_provenance":contract["profile"]["provenance"]},"evaluator":{"active_scope":"source virtual-clock start until 23:00","hard_clause":"every selected room temperature remains within [20,24] degC","soft_metric":contract["soft_metric"],"trajectory_completeness_required":True},"split":"none","statuses":statuses,"execution_scope":contract["execution_scope"],"termination":{"type":"finite_evaluation_window","terminal_verdict":contract["terminal_semantics"],"reason":"MAINTAIN responsibility continues after the observed evening boundary"}}
            gates["no_public_gold_actions"]=public_payload_safe(p)
            gates["passed"]=all(v for k,v in gates.items() if k!="passed")
            if not gates["passed"]: failures.append({"source":cnd["source_path"].name,"code":"PUBLIC_LEAKAGE_GATE_FAILED"}); continue
            p["content_hash"]=digest_json(p)
            q={"schema_version":"responsibility-episode-simuhome-multiroom-v1-private","episode_id":eid,"responsibility_id":RID,"query_id":QUERY_ID,"contract_id":contract["contract_id"],"binding_role":"PRIMARY","physical_process_id":physical_id,"contract":contract,"backend_binding":{"backend":"SimuHome 0.1.0","adapter_version":w["adapter_version"],"room_ids":ids,"devices":w["devices"],"policy_bridge":"SimuHomeMultiroomThermalAdapter.replay(policy)","source_tick_interval":w["source_tick_interval"],"effective_tick_interval_seconds":w["effective_tick_interval_seconds"],"tick_mapping":w["tick_mapping"],"sample_minutes":w["sample_minutes"]},"source_window":{"source_file":cnd["source_path"].name,"source_file_sha256":sha256_file(cnd["source_path"]),"source_config_sha256":cnd["config_digest"],"responsibility_process_signature":sig,"base_time":cnd["config"]["base_time"],"end_boundary_local_hour":23,"end_inclusive":False,"pre_action_observation_provenance":"source room states observed before current-step actions"},"responsibility_lineage":{"catalog_version":catalog["catalog_version"],"catalog_sha256":fixed_hashes["catalog_sha256"],"contract_artifact":str(CONTRACT_PATH.relative_to(ROOT)),"contract_sha256":fixed_hashes["contract_sha256"],"source_evidence_ids":record["source_evidence_ids"],"query_provenance":record["provenance"],"catalog_contract_checks":catalog_contract_checks},"source_hashes":fixed_hashes,"room_selection":cnd["rooms"],"replay_certificates":{"witness_policy":{"type":"per-room observation-conditioned target","target_c":target},"witness_metrics":we,"noop_metrics":ne,"contrast_metrics":xe,"worst_room_mae_improvement_vs_noop_c":ne["worst_room_mae_c"]-we["worst_room_mae_c"],"witness_digest":digest_json(w),"noop_digest":digest_json(n),"contrast_digest":digest_json(x),"witness_replicate_digest":digest_json(wr),"noop_replicate_digest":digest_json(nr),"contrast_replicate_digest":digest_json(xr)},"selection_lineage":{"selection_rule":"distinct evening source config; all eligible explicit thermal rooms; initial construction band; at least two rooms","responsibility_process_deduplication":"behavioral-trajectory deduplication over sorted room roles/device families plus 0.001C-normalized witness/noop/contrast temperature matrices; timestamps excluded; source physical_process_id remains distinct","primary_window_is_unique":True,"split":"none","split_reason":"all records share one SimuHome implementation lineage"},"gold_actions":w["trace"],"gold_actions_released_publicly":False,"qa_verdicts":gates,"statuses":statuses}; public.append(p); private.append(q); gate_rows.append({"episode_id":eid,"responsibility_id":RID,"physical_process_id":physical_id,"source_config_sha256":cnd["config_digest"],"gates":gates,"passed":True})
        except Exception as e: failures.append({"source":cnd["source_path"].name,"code":f"REPLAY_ERROR:{type(e).__name__}","detail":str(e)})
    public.sort(key=lambda x:x["episode_id"]); private.sort(key=lambda x:x["episode_id"]); write_jsonl(out/"episodes_public.jsonl",public); write_jsonl(out/"episodes_private.jsonl",private)
    report={"schema_version":"simuhome-multiroom-evening-release-v1","status":"PASS" if public else "EMPTY_FAIL_CLOSED","passed":bool(public),"source_file_count":len(list(DATA.glob("*.json"))),"candidate_source_count":len(candidates),"episode_count":len(public),"responsibility_count":1 if public else 0,"responsibility_episode_counts":{RID:len(public)} if public else {},"all_primary_processes_unique":len({x["physical_process_id"] for x in private})==len(private),"gold_actions_released_publicly":False,"split_counts":{"none":len(public)} if public else {},"failure_counts":dict(sorted(Counter(x["code"] for x in failures).items())),"failure_examples":failures[:20],"selection_universe":{"corpus_manifest_sha256":corpus_sha,"corpus_file_count":corpus_count,"backend_python_manifest_sha256":backend_sha,"backend_python_file_count":backend_count,"simuhome_git_commit":commit,"simuhome_git_dirty":bool(git_status.strip()),"runtime":fixed_hashes["runtime"],"license_status":"not_found_in_checked_repository; authorization_unknown"},"selection_rule":{"source_hour":"17<=hour<23","eligible_rooms":"all rooms with AC/HP and initial temperature 20-24C","minimum_rooms":2,"device_preference":"AC else HP","hp_above_target_rejected":True},"public_digest":sha256_file(out/"episodes_public.jsonl"),"private_digest":sha256_file(out/"episodes_private.jsonl"),"limitations":["SimuHome is a deterministic synthetic room-state aggregator, not a calibrated building model","all source configs share one simulator lineage","22C and 20-24C are benchmark construction parameters, not literal query semantics","no occupancy or unrelated lifecycle semantics are inferred","preview-only; public redistribution is not authorized"]}
    write_json(out/"build_report.json",report); write_json(out/"replay_gate.json",{"schema_version":"simuhome-multiroom-evening-replay-gate-v1","all_released_episodes_passed":bool(gate_rows) and all(x["passed"] for x in gate_rows),"candidate_source_count":len(candidates),"released_episode_count":len(gate_rows),"episodes":gate_rows}); (out/"DATASET_CARD.md").write_text("# SimuHome Multi-room Evening Responsibility Release v1\n\nProvisional sandbox-only Episodes mined from explicit multi-room evening thermal processes. Every released window has one primary responsibility and passed witness, no-op, contrast, determinism, completeness, evaluator, lineage, and leakage gates.\n\nSimuHome is synthetic rather than a calibrated building model; redistribution authorization remains unknown.\n"); return report
if __name__=="__main__": print(json.dumps(main(),indent=2))
