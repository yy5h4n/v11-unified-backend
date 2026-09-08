#!/usr/bin/env python3
"""Closed-loop evaluation of an OpenAI-compatible policy on the 40 public episodes."""
from __future__ import annotations
import argparse, json, os, time, uuid, threading
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[4]
PUBLIC = ROOT / "generated/expanded_responsibility_release_v1/episodes_public.jsonl"
PRIVATE = ROOT / "generated/expanded_responsibility_release_v1/episodes_private.jsonl"
DEFAULT_BASE = "https://aigc.sankuai.com/v1/openai/native"
DEFAULT_MODEL = "deepseek-v4-flash-meituan"

class APIError(RuntimeError): pass

class ChatClient:
    def __init__(self, base_url=DEFAULT_BASE, model=DEFAULT_MODEL, api_key=None, retries=2, timeout=120):
        self.base_url = base_url.rstrip("/"); self.model = model
        self.api_key = api_key or os.environ.get("AIGC_API_KEY")
        self.retries, self.timeout = retries, timeout
        if not self.api_key: raise RuntimeError("AIGC_API_KEY is required")
    def complete(self, messages):
        payload = json.dumps({"model": self.model, "messages": messages, "temperature": 0, "max_tokens": 256}).encode()
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                req = Request(self.base_url + "/chat/completions", data=payload, method="POST",
                              headers={"Content-Type":"application/json", "Authorization":"Bearer " + self.api_key})
                with urlopen(req, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode())
                choice = body.get("choices", [{}])[0].get("message", {}).get("content", "")
                usage = body.get("usage", {}) or {}
                return {"content": choice, "usage": usage, "latency_ms": (time.perf_counter()-started)*1000}
            except HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt >= self.retries: raise APIError("http_%s" % exc.code)
            except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                if attempt >= self.retries: raise APIError(type(exc).__name__.lower())
            time.sleep(min(8.0, 0.5 * (2 ** attempt)))

def _json_content(content):
    if isinstance(content, dict): return content
    text = str(content).strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    try: return json.loads(text)
    except Exception as exc: raise ValueError("invalid_json") from exc

def validate_action(action, episode):
    schema = episode["legal_action_schema"]
    if not isinstance(action, dict): raise ValueError("action_not_object")
    # EnergyPlus is deliberately scalar and must never receive a discrete index.
    if schema.get("type") == "discrete_thermostat_schedule_value_c":
        value = action.get("value", action.get("target_c", action.get("setpoint_c")))
        if isinstance(value, bool) or not isinstance(value, (int, float)): raise ValueError("energyplus_value_not_number")
        if float(value) not in [float(x) for x in schema["values"]]: raise ValueError("energyplus_value_not_legal")
        return float(value)
    if schema.get("type") == "room_thermal_command":
        mode = str(action.get("mode", "auto")); target = action.get("target_c", 22.0)
        if isinstance(target, bool) or not isinstance(target, (int, float)): raise ValueError("target_not_number")
        target = float(target)
        if mode not in schema.get("modes", ["auto","heat","cool","off"]): raise ValueError("mode_not_legal")
        if not schema["target_c_range"][0] <= target <= schema["target_c_range"][1]: raise ValueError("target_out_of_range")
        dtype = episode["initial_observation"].get("device_type")
        if mode != "auto" and mode not in schema["device_constraints"][dtype]: raise ValueError("mode_not_supported_by_device")
        return {"mode": mode, "target_c": target}
    rooms = episode.get("room_ids") or list((episode.get("initial_observation") or {}).keys())
    mapping = action.get("actions", action)
    if not isinstance(mapping, dict) or set(mapping) != set(rooms): raise ValueError("action_mapping_must_cover_all_rooms")
    for room in rooms:
        a = mapping[room]
        if not isinstance(a, dict): raise ValueError("room_action_not_object")
        mode = str(a.get("mode", "auto")); raw_target = a.get("target_c", 22.0)
        if isinstance(raw_target, bool) or not isinstance(raw_target, (int, float)): raise ValueError("target_not_number")
        target = float(raw_target)
        if mode not in schema.get("modes", ["auto","heat","cool","off"]): raise ValueError("mode_not_legal")
        if not schema["target_c_range"][0] <= target <= schema["target_c_range"][1]: raise ValueError("target_out_of_range")
        dtype = episode["initial_observation"][room].get("device_type")
        if mode != "auto" and mode not in schema["device_constraints"][dtype]: raise ValueError("mode_not_supported_by_device")
    return {r: {"mode": str(mapping[r].get("mode", "auto")), "target_c": float(mapping[r].get("target_c", 22.0))} for r in rooms}

def _fallback(ep):
    if ep["legal_action_schema"].get("type") == "discrete_thermostat_schedule_value_c": return 22.0
    if ep["legal_action_schema"].get("type") == "room_thermal_command": return {"mode":"off", "target_c":22.0}
    rooms = ep.get("room_ids") or list(ep["initial_observation"])
    return {r: {"mode":"off", "target_c":22.0} for r in rooms}

def _messages(ep, observation):
    typ = ep["legal_action_schema"].get("type")
    if typ == "discrete_thermostat_schedule_value_c":
        output = 'Return exactly {"value": 21.0} or {"value": 22.0}. The value is a physical temperature, never action index 0 or 1.'
    elif typ == "room_thermal_command":
        output = 'Return exactly {"mode":"auto|heat|cool|off","target_c":NUMBER}. Do not add an action wrapper.'
    else:
        rooms = ep.get("room_ids", [])
        output = 'Return exactly {"actions":{ROOM:{"mode":"auto|heat|cool|off","target_c":NUMBER}}} covering every room once. Required rooms: ' + ', '.join(rooms) + '.'
    return [{"role":"system","content":"Return one JSON object only, without markdown or explanation. Follow the legal action schema exactly."},
            {"role":"user","content":output+"\nINPUT:\n"+json.dumps({"natural_query":ep["natural_query"],"legal_action_schema":ep["legal_action_schema"],"profile":ep["profile"],"current_observation":observation},ensure_ascii=False)}]

def load_episodes(limit=None):
    public = [json.loads(x) for x in PUBLIC.read_text().splitlines() if x.strip()]
    private = {json.loads(x)["episode_id"]: json.loads(x) for x in PRIVATE.read_text().splitlines() if x.strip()}
    rows = [(p, private[p["episode_id"]]) for p in public if p["episode_id"] in private]
    return rows[:limit] if limit else rows

def evaluate_episode(public, private, client, backend_runner=None):
    ep = public; backend = private["backend_binding"].get("backend", "")
    calls = valid = api_ok = tokens = 0; errors = Counter(); latencies = []; invalid = False
    observations = ep["initial_observation"]
    # The replay callback is where calls occur, preserving backend semantics.
    def policy(*args):
        nonlocal calls, valid, api_ok, tokens, invalid, observations
        observation = args[0] if len(args)==1 else args[1]
        calls += 1
        try:
            result = client.complete(_messages(ep, observation)); api_ok += 1
            latencies.append(float(result.get("latency_ms", 0)))
            tokens += int((result.get("usage") or {}).get("total_tokens", 0))
            action = validate_action(_json_content(result["content"]), ep); valid += 1; return action
        except APIError as exc:
            errors["api_" + str(exc)] += 1
        except ValueError as exc:
            errors[str(exc)] += 1
        invalid = True; return _fallback(ep)
    try:
        if backend_runner: score = backend_runner(ep, private, policy)
        elif backend.startswith("EnergyPlus"):
            import replay_energyplus_responsibility_episode as mod
            score = mod.evaluate_episode(ep["episode_id"], lambda obs, step: policy(obs), run_name="llm_"+uuid.uuid4().hex)
        elif "multiroom" in ep.get("schema_version", ""):
            from unified_compiler.simuhome_multiroom_thermal_adapter import SimuHomeMultiroomThermalAdapter
            import compile_simuhome_multiroom_evening_episodes as comp
            source = WORKSPACE / "external/SimuHome/data/benchmark" / private["source_window"]["source_file"]
            config = json.loads(source.read_text())["initial_home_config"]
            replay = SimuHomeMultiroomThermalAdapter(config, ep["room_ids"]).replay(lambda i,o: policy(i,o))
            score = comp.evaluate(replay, float(ep["profile"]["target_c"]), *map(float, [ep["profile"]["temperature_band_c"]["lower"], ep["profile"]["temperature_band_c"]["upper"]]))
        else:
            from unified_compiler.simuhome_room_thermal_adapter import SimuHomeRoomThermalAdapter
            import compile_simuhome_kitchen_evening_episodes as comp
            source = WORKSPACE / "external/SimuHome/data/benchmark" / private["source_window"]["source_file"]
            config = json.loads(source.read_text())["initial_home_config"]
            replay = SimuHomeRoomThermalAdapter(config).replay(lambda i,o: policy(i,o))
            score = comp.evaluate(replay, float(ep["profile"]["target_c"]), *map(float, [ep["profile"]["temperature_band_c"]["lower"], ep["profile"]["temperature_band_c"]["upper"]]))
        expected_calls = int(ep["horizon_steps"])
        calls_match_horizon = calls == expected_calls
        if not calls_match_horizon: errors["policy_call_count_mismatch"] += 1
        success = not invalid and calls_match_horizon and score.get("trajectory_complete", False) and score.get("hard_contract_satisfied", score.get("hard_violation_count", 1) == 0)
    except Exception as exc:
        errors["backend_" + type(exc).__name__] += 1; score = {}; success = False
    return {"episode_id": ep["episode_id"], "responsibility_id":ep["responsibility_id"], "backend":backend, "success":success, "expected_calls":int(ep["horizon_steps"]), "calls_match_horizon":calls==int(ep["horizon_steps"]), "calls":calls, "valid_actions":valid, "api_successes":api_ok, "tokens":tokens, "latency_ms":sum(latencies), "errors":dict(errors), "mean_soft_metric":score.get("mean_abs_error_c"), "score":score}

def evaluate_pair_in_fresh_process(pair, base_url, model):
    """Top-level pickleable worker; API key is inherited only via environment."""
    return evaluate_episode(*pair, ChatClient(base_url, model))

def aggregate(rows):
    n=len(rows); groups=defaultdict(list)
    for r in rows: groups[(r["responsibility_id"],r["backend"])].append(r)
    def one(xs):
        return {"episodes":len(xs),"episode_successes":sum(r["success"] for r in xs),"episode_success_rate":sum(r["success"] for r in xs)/len(xs) if xs else 0,"action_calls":sum(r["calls"] for r in xs),"valid_actions":sum(r["valid_actions"] for r in xs),"action_valid_rate":sum(r["valid_actions"] for r in xs)/max(1,sum(r["calls"] for r in xs)),"api_success_rate":sum(r["api_successes"] for r in xs)/max(1,sum(r["calls"] for r in xs)),"mean_soft_metric":sum(r["mean_soft_metric"] for r in xs if r["mean_soft_metric"] is not None)/max(1,sum(r["mean_soft_metric"] is not None for r in xs)),"tokens":sum(r["tokens"] for r in xs),"latency_ms":sum(r["latency_ms"] for r in xs),"errors":dict(sum((Counter(r["errors"]) for r in xs),Counter()))}
    total=one(rows)
    total["mean_soft_metric"]=None
    total["mean_soft_metric_note"]="disabled: do not pool soft metrics across backend fidelity strata"
    return {"total":total,"by_responsibility_backend":{f"{k[0]}::{k[1]}":one(v) for k,v in groups.items()}}

def main(args=None):
    p=argparse.ArgumentParser(); p.add_argument("--base-url",default=DEFAULT_BASE); p.add_argument("--model",default=DEFAULT_MODEL); p.add_argument("--output",default="generated/llm_eval_deepseek_v4_flash_meituan_v1/report.json"); p.add_argument("--limit",type=int); p.add_argument("--workers",type=int,default=1); p.add_argument("--executor",choices=("process","thread"),default="process"); ns=p.parse_args(args)
    client=ChatClient(ns.base_url,ns.model); pairs=load_episodes(ns.limit)
    out=Path(ns.output); out.parent.mkdir(parents=True,exist_ok=True)
    rows_by_index={}
    def checkpoint(complete=False):
        rows=[rows_by_index[i] for i in sorted(rows_by_index)]
        report={"config":{"base_url":ns.base_url,"model":ns.model,"workers":ns.workers,"executor":ns.executor,"process_isolation":ns.executor=="process","limit":ns.limit,"episode_count":len(pairs),"completed_episode_count":len(rows),"complete":complete},"episodes":rows,"aggregate":aggregate(rows)}
        tmp=out.with_suffix(out.suffix+".tmp"); tmp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n"); tmp.replace(out)
        return report
    executor_class=ProcessPoolExecutor if ns.executor=="process" else ThreadPoolExecutor
    with executor_class(max_workers=max(1,ns.workers)) as pool:
        if ns.executor=="process": futures={pool.submit(evaluate_pair_in_fresh_process,pair,ns.base_url,ns.model):i for i,pair in enumerate(pairs)}
        else: futures={pool.submit(evaluate_episode,*pair,client):i for i,pair in enumerate(pairs)}
        for future in as_completed(futures):
            idx=futures[future]; row=future.result(); rows_by_index[idx]=row; checkpoint(False)
            print(json.dumps({"completed":len(rows_by_index),"total":len(pairs),"episode_id":row["episode_id"],"success":row["success"],"calls":row["calls"],"valid_actions":row["valid_actions"],"errors":row["errors"]},ensure_ascii=False),flush=True)
    return checkpoint(True)
if __name__ == "__main__": main()
