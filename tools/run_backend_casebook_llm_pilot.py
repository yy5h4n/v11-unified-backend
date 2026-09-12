#!/usr/bin/env python3
"""Run every Backend Casebook query as a real closed-loop LLM pilot.

This is pilot evidence, not benchmark admission.  It records three separate
resource families: provider-reported model usage, backend compute usage, and
route-native household resource consumption.  Heterogeneous physical units
are never summed into one raw total.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import sys
import time
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.llm_conversation import OBSERVATION_DELTA_INSTRUCTIONS, observation_delta
from unified_compiler.route_registry import route_metadata

DEFAULT_BASE_URL = "https://aigc.sankuai.com/v1/openai/native"
DEFAULT_SPECS = ROOT / "backend_casebook_v0" / "pilot_specs.json"
DEFAULT_OUTPUT = ROOT / "generated" / "backend_casebook_llm_pilot_v0"


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    item = getattr(value, "item", None)
    return jsonable(item()) if callable(item) else str(value)


class ChatClient:
    def __init__(self, base_url: str, model: str, api_key: str, timeout: int = 120, retries: int = 2, max_tokens: int = 320) -> None:
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
            raise ValueError('max_tokens must be a positive integer')
        self.max_tokens = max_tokens
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.retries = retries

    def complete(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        body = canonical({
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }).encode()
        started = time.perf_counter()
        for attempt in range(self.retries + 1):
            try:
                request = Request(self.url, data=body, method="POST", headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer " + self.api_key,
                })
                with urlopen(request, timeout=self.timeout) as response:
                    response_body = response.read()
                    payload = json.loads(response_body)
                message = payload["choices"][0]["message"]
                return {
                    "content": message.get("content", ""),
                    "usage": jsonable(payload.get("usage") or {}),
                    "provider_model": payload.get("model"),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 3),
                    "request_bytes": len(body),
                    "response_bytes": len(response_body),
                    "finish_reason": payload['choices'][0].get('finish_reason'),
                }
            except (HTTPError, URLError, TimeoutError, KeyError, IndexError, ValueError) as exc:
                if attempt >= self.retries:
                    raise RuntimeError(f"LLM transport failed after {attempt + 1} attempt(s): {type(exc).__name__}") from exc
                time.sleep(min(4.0, 0.5 * (2 ** attempt)))
        raise AssertionError("unreachable")


def parse_action(content: Any) -> Any:
    if not isinstance(content, str):
        raise ValueError("model response is not text")
    text = content.strip()
    if text.startswith("<answer>") and text.endswith("</answer>"):
        text = text[len("<answer>"):-len("</answer>")].strip()
    elif text.startswith("```json\n") and text.endswith("```"):
        text = text[len("```json\n"):-3].strip()
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    def invalid_constant(value):
        raise ValueError(f"non-finite JSON number: {value}")
    value = json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)
    if not isinstance(value, dict) or set(value) != {"action"}:
        raise ValueError("response must contain exactly one action field")
    canonical(value)  # Also rejects overflow such as 1e999.
    return value["action"]


def route_factory(route_id: str):
    if route_id == "d0_exogenous_context":
        # The accepted D0 replay gate is bound to its checked-in default
        # schedule.  A case-specific schedule must earn a new gate rather than
        # silently bypassing provenance, so this pilot uses the accepted route.
        return make_agent_backend(route_id)
    if route_id == "d1_discrete_device_fault":
        from harness_v2.core import EpisodeSpec
        from unified_compiler.adapters.d1_discrete_device_fault import DiscreteFaultSchedule, DiscreteFaultWindow
        schedule = DiscreteFaultSchedule((DiscreteFaultWindow("garage_door.main", 0, 3, "jammed"),))
        episode = EpisodeSpec("casebook-garage", {"scenario_type": "post_parking_garage_closure", "horizon_seconds": 600}, 17)
        return make_agent_backend(route_id, schedule=schedule, episode_spec=episode)
    return make_agent_backend(route_id)


def idle_action(route_id: str, legal: Mapping[str, Any]) -> Any:
    if route_id == "d0_exogenous_context":
        return {"kind": "act", "command": {"target": "interior_lights", "operation": "off"}}
    if route_id == "d1_sustaingym_fault":
        return [0.0] * int(legal["shape"][0])
    if route_id == "d1_citylearn_battery_fault": return 0.0
    if route_id == "d1_ev2gym_fault": return {"type": "SET_CHARGE_POWER", "kw": 0.0}
    if route_id == "d1_discrete_device_fault": return {"kind": "act", "commands": []}
    if route_id in {"energyplus_iaq", "wntr_residential_water", "fds_smoke_fire", "modelica_buildings_aixlib"}: return 0.0
    if route_id == "d3_citylearn_multi_system": return {"battery_rate": 0.0, "hvac_rate": 0.0}
    if route_id == "d3_citylearn_multibuilding_competition":
        ids = sorted({name.rsplit(".", 1)[0] for name in legal["channels"]})
        return {i: {"battery_rate": 0.0, "hvac_rate": 0.0} for i in ids}
    if route_id == "d3_wntr_water_competition": return {"shower_valve_open": 0.0, "laundry_valve_open": 0.0}
    if route_id == "d3_modelica_shared_heat": return {"space_heating_request": 0.0, "dhw_request": 0.0}
    if route_id == "d3_ev2gym_electric_competition": return {"charger_0_rate": 0.0, "charger_1_rate": 0.0}
    if route_id == "d3_energyplus_shared_ventilation": return {"zone_a_airflow_request": 0.0, "zone_b_airflow_request": 0.0}
    raise KeyError(route_id)


def _rusage() -> dict[str, float]:
    own = resource.getrusage(resource.RUSAGE_SELF)
    child = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {
        "user_cpu_seconds": own.ru_utime + child.ru_utime,
        "system_cpu_seconds": own.ru_stime + child.ru_stime,
        "max_rss_bytes": float(max(own.ru_maxrss, child.ru_maxrss) * (1024 if sys.platform != "darwin" else 1)),
    }


def _delta_usage(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {
        "user_cpu_seconds": round(after["user_cpu_seconds"] - before["user_cpu_seconds"], 6),
        "system_cpu_seconds": round(after["system_cpu_seconds"] - before["system_cpu_seconds"], 6),
        "max_rss_bytes": int(after["max_rss_bytes"]),
    }


def run_idle(spec: Mapping[str, Any], steps: int, cadence: float) -> dict[str, Any]:
    route_id = spec["route_id"]
    route = route_factory(route_id)
    started = time.monotonic(); usage0 = _rusage(); frames = []
    try:
        initial_receipt = route.reset(seed=int(spec.get("seed", 17)))
        initial = initial_receipt["observation"]
        legal = route.legal_actions()
        action = idle_action(route_id, legal)
        termination = "horizon_reached"
        for i in range(steps):
            receipt = route.step(deepcopy(action), dt_seconds=cadence)
            frames.append({"index": i, "action": deepcopy(action), "receipt": jsonable(receipt)})
            if receipt.get("done"):
                termination = "native_done"
                break
        return {"ok": len(frames) == steps or termination == "native_done", "initial_observation": initial, "frames": frames, "termination": termination,
                "compute": {**_delta_usage(usage0, _rusage()), "wall_seconds": round(time.monotonic() - started, 6), "backend_steps": len(frames)}}
    except Exception as exc:
        return {"ok": False, "initial_observation": locals().get("initial"), "frames": frames, "termination": "error", "error": f"{type(exc).__name__}: {exc}",
                "compute": {**_delta_usage(usage0, _rusage()), "wall_seconds": round(time.monotonic() - started, 6), "backend_steps": len(frames)}}
    finally:
        route.close()


def decision_points(steps: int, maximum: int, route_id: str) -> list[int]:
    if route_id in {"d0_exogenous_context", "d1_discrete_device_fault", "fds_smoke_fire"}:
        return list(range(steps))
    count = min(steps, maximum)
    if count == 1: return [0]
    return sorted({round(i * (steps - 1) / (count - 1)) for i in range(count)})


def _system_message(route_id: str, legal: Mapping[str, Any], cadence: float) -> str:
    wire_rules = {
        "d0_exogenous_context": "The value under action MUST be the full object {kind:'act', command:{target:..., operation:...}}. Do not return only the inner command.",
        "d1_discrete_device_fault": "The value under action MUST be the full object {kind:'act', commands:[...]}; an empty commands list is a legal wait-by-one-tick action.",
        "d1_ev2gym_fault": "The value under action MUST be the full object {type:'SET_CHARGE_POWER', kw:number}.",
        "d3_ev2gym_electric_competition": "The value under action MUST be an object with exactly charger_0_rate and charger_1_rate. Positive values charge connected cars. Do not return an array. IMPORTANT: the public field currently named native_action_mask is EV2Gym's observation_mask and does NOT prohibit charging actions; legal_actions is authoritative.",
        "d3_energyplus_shared_ventilation": "The value under action MUST be an object with exactly zone_a_airflow_request and zone_b_airflow_request. Do not return prose or an array.",
    }
    wire_example_routes = {"d0_exogenous_context", "d1_discrete_device_fault", "d1_ev2gym_fault"}
    return canonical({
        "role": "closed_loop_household_controller",
        "route_id": route_id,
        "native_cadence_seconds": cadence,
        "legal_actions": legal,
        "legal_wire_format_example_not_a_policy": idle_action(route_id, legal) if route_id in wire_example_routes else None,
        "critical_wire_rule": wire_rules.get(route_id, "The value under action is the complete native action, not a description or a partial field."),
        "response_format": "Return exactly <answer>{\"action\": NATIVE_ACTION}</answer>, with no prose.",
        "rules": [
            "Use only public observations and this legal action schema.",
            "The same action may be held for several native backend steps until the next LLM decision.",
            "Do not claim success; the private trajectory evaluator decides it.",
            "Later user messages contain lossless observation deltas relative to the previous decision observation."
        ],
        "observation_delta_instructions": list(OBSERVATION_DELTA_INSTRUCTIONS),
    })


def run_llm(spec: Mapping[str, Any], steps: int, cadence: float, client: ChatClient, max_decisions: int) -> dict[str, Any]:
    route_id = spec["route_id"]
    route = route_factory(route_id)
    started = time.monotonic(); usage0 = _rusage(); frames=[]; calls=[]; messages=[]
    try:
        reset = route.reset(seed=int(spec.get("seed", 17))); initial = reset["observation"]; previous_decision_obs = deepcopy(initial)
        legal = route.legal_actions(); points = decision_points(steps, max_decisions, route_id)
        messages = [
            {"role": "system", "content": _system_message(route_id, legal, cadence)},
            {"role": "user", "content": canonical({"message_type": "initial_request", "query": spec["query"], "benchmark_contract": spec["benchmark_contract"], "initial_observation": initial, "trajectory_steps": steps, "llm_decision_steps": points})}
        ]
        termination="horizon_reached"; current_step=0
        for point_index, point in enumerate(points):
            if current_step != point:
                raise RuntimeError(f"internal decision schedule mismatch: {current_step} != {point}")
            response=client.complete(messages); raw=response["content"]
            call={k:response.get(k) for k in ("usage","provider_model","latency_ms","request_bytes")}
            call.update({"decision_index":point_index,"native_step":point,"response_sha256":hashlib.sha256(str(raw).encode()).hexdigest(),"response_bytes":len(str(raw).encode())})
            calls.append(call)
            try:
                action=parse_action(raw)
            except Exception:
                call["parse_failure_preview"] = str(raw)[:500]
                raise
            call["parsed_action"] = jsonable(action)
            messages.append({"role":"assistant","content":"<answer>"+canonical({"action":action})+"</answer>"})
            next_point = points[point_index+1] if point_index+1 < len(points) else steps
            receipts=[]
            while current_step < next_point:
                receipt=route.step(deepcopy(action),dt_seconds=cadence)
                frame={"index":current_step,"action":deepcopy(action),"receipt":jsonable(receipt)}; frames.append(frame); receipts.append(frame)
                current_step += 1
                if receipt.get("done"):
                    termination="native_done"; break
            final_receipt=receipts[-1]["receipt"]
            final_obs=final_receipt["observation"]
            messages.append({"role":"user","content":canonical({"message_type":"environment_observation","held_action_native_steps":len(receipts),"action_result":{k:final_receipt.get(k) for k in ("time_seconds","delta_t_seconds","done","terminated","truncated","info")},"observation_delta":observation_delta(previous_decision_obs,final_obs),"remaining_native_steps":max(0,steps-current_step)})})
            previous_decision_obs=deepcopy(final_obs)
            if termination=="native_done": break
        complete=(len(frames)==steps or termination=="native_done")
        return {"ok":complete,"initial_observation":initial,"frames":frames,"termination":termination,"decision_points":points,"calls":calls,
                "model_usage":summarize_model_usage(calls),"conversation_sha256":digest(messages),
                "compute":{**_delta_usage(usage0,_rusage()),"wall_seconds":round(time.monotonic()-started,6),"backend_steps":len(frames)}}
    except Exception as exc:
        return {"ok":False,"initial_observation":locals().get("initial"),"frames":frames,"termination":"error","error":f"{type(exc).__name__}: {exc}","calls":calls,
                "model_usage":summarize_model_usage(calls),"conversation_sha256":digest(messages) if messages else None,
                "compute":{**_delta_usage(usage0,_rusage()),"wall_seconds":round(time.monotonic()-started,6),"backend_steps":len(frames)}}
    finally:
        route.close()


def summarize_model_usage(calls: list[Mapping[str, Any]]) -> dict[str, Any]:
    totals: dict[str, float] = {}
    for call in calls:
        for key,value in (call.get("usage") or {}).items():
            if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(float(value)):
                totals[key]=totals.get(key,0.0)+float(value)
    cleaned={k:int(v) if float(v).is_integer() else round(v,6) for k,v in totals.items()}
    cleaned.update({
        "calls":len(calls),
    })
    for field in ('latency_ms', 'request_bytes', 'response_bytes'):
        known = [c[field] for c in calls if isinstance(c.get(field), (int, float))
                 and not isinstance(c[field], bool) and math.isfinite(c[field]) and c[field] >= 0]
        subtotal = round(sum(known), 3) if field == 'latency_ms' else sum(known)
        cleaned[field+'_known_subtotal'] = subtotal
        cleaned[field+'_available_calls'] = len(known)
        cleaned[field+'_total'] = subtotal if len(known) == len(calls) else None
    return cleaned


def observations(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result=[]
    if isinstance(run.get("initial_observation"),Mapping): result.append(run["initial_observation"])
    for frame in run.get("frames",[]):
        obs=frame.get("receipt",{}).get("observation")
        if isinstance(obs,Mapping): result.append(obs)
    return result


def effects(run: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result=[]
    for frame in run.get("frames",[]):
        effect=frame.get("receipt",{}).get("info",{}).get("effect")
        if isinstance(effect,Mapping): result.append(effect)
    return result


def physical_consumption(route_id: str, run: Mapping[str, Any], cadence: float) -> dict[str, Any]:
    obs=observations(run)[1:]; frames=run.get("frames",[]); eff=effects(run); hours=cadence/3600.0
    values: dict[str, dict[str, Any]]={}
    def add(name: str, value: float, unit: str, kind: str="resource"):
        values[name]={"value":round(float(value),8),"unit":unit,"kind":kind}
    if route_id in {"d0_exogenous_context","d1_discrete_device_fault"}:
        add("action_count",len(frames),"commands_or_decisions","actuation")
        if route_id=="d1_discrete_device_fault" and obs:
            add("backend_action_cost",obs[-1].get("metrics",{}).get("action_cost",0),"action_unit","actuation")
    elif route_id=="d1_sustaingym_fault":
        acts=[f.get("action",[]) for f in frames]
        add("cooling_command_effort",sum(sum(max(0.0,-float(v)) for v in a) for a in acts)*hours,"zone_fraction_hours","proxy")
    elif route_id=="d1_citylearn_battery_fault":
        add("grid_import",sum(max(0.0,float(e.get("net_electricity_kwh",0))) for e in eff),"kWh")
        add("battery_throughput",sum(abs(float(e.get("storage_electricity_kwh",0))) for e in eff),"kWh","wear_proxy")
    elif route_id=="d1_ev2gym_fault":
        add("charged_energy",sum(float(e.get("charged_energy_kwh",0)) for e in eff),"kWh")
        add("charging_cost",sum(float(e.get("charge_cost_eur",0)) for e in eff),"EUR")
    elif route_id=="energyplus_iaq":
        add("ventilation_command_effort",sum(float(f.get("action",0)) for f in frames)*hours,"fraction_hours","proxy")
    elif route_id=="wntr_residential_water":
        add("source_water",sum(max(0.0,float(o.get("flow_source_fill_m3_s",0)))*cadence for o in obs),"m3")
        add("leaked_water",sum(max(0.0,float(o.get("leak_total_m3_s",0)))*cadence for o in obs),"m3")
    elif route_id=="fds_smoke_fire":
        add("door_open_time",sum(float(f.get("action",0))*cadence for f in frames),"seconds","risk_proxy")
    elif route_id=="modelica_buildings_aixlib":
        add("delivered_heat",sum(max(0.0,float(o.get("heater_heat_flow_w",0)))*cadence/3.6e6 for o in obs),"kWh_thermal")
    elif route_id=="d3_citylearn_multi_system":
        add("grid_import",sum(max(0.0,float(o.get("net_electricity_consumption",0))) for o in obs),"kWh")
        add("battery_throughput",sum(abs(float(o.get("electrical_storage_electricity_consumption",0))) for o in obs),"kWh","wear_proxy")
    elif route_id=="d3_citylearn_multibuilding_competition":
        add("district_grid_import",sum(max(0.0,float(o.get("district_net_kwh",0))) for o in obs),"kWh")
        add("district_peak",max((float(o.get("district_net_kwh",0)) for o in obs),default=0),"kWh_per_step","peak")
    elif route_id=="d3_wntr_water_competition":
        add("source_water",sum(max(0.0,float(o.get("flow_source_fill_m3_s",0)))*cadence for o in obs),"m3")
        add("served_shower",sum(max(0.0,float(o.get("served_shower_m3_s",0)))*cadence for o in obs),"m3","service")
        add("served_laundry",sum(max(0.0,float(o.get("served_laundry_m3_s",0)))*cadence for o in obs),"m3","service")
    elif route_id=="d3_modelica_shared_heat":
        add("delivered_heat",sum(max(0.0,float(o.get("shared_heat_pump_capacity_used_w",0)))*cadence/3.6e6 for o in obs),"kWh_thermal")
        add("service_shortfall",sum(max(0.0,float(o.get("service_shortfall_w",0)))*cadence/3.6e6 for o in obs),"kWh_thermal_equivalent","deficit")
    elif route_id=="d3_ev2gym_electric_competition":
        add("transformer_energy",sum(max(0.0,float(o.get("transformer",{}).get("power_kw",0)))*hours for o in obs),"kWh")
        add("peak_transformer_power",max((float(o.get("transformer",{}).get("power_kw",0)) for o in obs),default=0),"kW","peak")
    elif route_id=="d3_energyplus_shared_ventilation":
        add("delivered_air",sum((max(0.0,float(o.get("zone_a_actual_airflow_m3_s",0)))+max(0.0,float(o.get("zone_b_actual_airflow_m3_s",0))))*cadence for o in obs),"m3")
    return {"metrics":values,"heterogeneous_units_not_summed":True}


def evaluate(route_id: str, run: Mapping[str, Any], baseline: Mapping[str, Any] | None=None) -> dict[str, Any]:
    obs=observations(run); frames=run.get("frames",[]); completed=bool(run.get("ok")); details={}; score=0.0
    if not completed or not obs:
        return {"pass":False,"score":0.0,"reason":run.get("error","incomplete_trajectory"),"details":details}
    if route_id=="d0_exogenous_context":
        eligible=[]; violations=[]
        for i,o in enumerate(obs[1:]):
            if o.get("context",{}).get("occupancy_count")!=0: continue
            external_now=any(e.get("source")=="external" and e.get("event_type") in {"occupancy_change","door_state_change"} for e in o.get("events",[]))
            if external_now: continue
            eligible.append(i)
            if o.get("devices",{}).get("front_door")!="closed" or o.get("devices",{}).get("interior_lights")!="off": violations.append(i)
        score=1.0-len(violations)/max(1,len(eligible)); passed=bool(eligible) and not violations; details={"eligible_away_frames":eligible,"violation_frames":violations}
    elif route_id=="d1_sustaingym_fault":
        vals=[float(v) for o in obs[1:] for v in o.get("zone_temperatures_c",[])]; bad=[v for v in vals if not 18<=v<=26]; score=1-len(bad)/max(1,len(vals)); passed=bool(vals) and not bad; details={"min_c":min(vals,default=None),"max_c":max(vals,default=None),"violating_values":len(bad)}
    elif route_id=="d1_citylearn_battery_fault":
        socs=[float(o.get("battery_soc",-1)) for o in obs]; final=socs[-1]; safe=all(0<=v<=1 for v in socs); score=min(1,max(0,final/0.35)) if safe else 0; passed=safe and final>=0.35; details={"final_soc":final,"target_soc":0.35,"safe":safe}
    elif route_id=="d1_ev2gym_fault":
        es=effects(run); dep=next((e.get("departure_soc") for e in reversed(es) if e.get("departure_soc") is not None),None); target=float(obs[0].get("required_departure_soc",1)); overloaded=any(e.get("overloaded") for e in es); score=min(1,float(dep or 0)/target) if not overloaded else 0; passed=dep is not None and float(dep)>=target and not overloaded; details={"departure_soc":dep,"required_departure_soc":target,"overloaded":overloaded}
    elif route_id=="d1_discrete_device_fault":
        final=obs[-1].get("devices",{}).get("garage_door.main",{}).get("state"); passed=final=="closed"; score=1.0 if passed else 0.0; details={"final_garage_state":final}
    elif route_id=="energyplus_iaq":
        co2=[float(o["co2_ppm"]) for o in obs[1:]]; rh=[float(o["relative_humidity_pct"]) for o in obs[1:]]; temp=[float(o["zone_temperature_c"]) for o in obs[1:]]; bad=sum(v>1200 for v in co2)+sum(not 0<=v<=100 for v in rh)+sum(not 5<=v<=40 for v in temp); score=1-bad/max(1,len(co2)+len(rh)+len(temp)); passed=bool(co2) and bad==0; details={"max_co2_ppm":max(co2,default=None),"rh_range":[min(rh,default=None),max(rh,default=None)],"temperature_range_c":[min(temp,default=None),max(temp,default=None)]}
    elif route_id=="wntr_residential_water":
        xs=obs[1:]; leak=sum(max(0,float(o.get("leak_total_m3_s",0)))*3600 for o in xs); pressure=max((max(float(o.get("pressure_bathroom_m",0)),float(o.get("pressure_kitchen_m",0))) for o in xs),default=0); tank=min((float(o.get("tank_level_m",-1)) for o in xs),default=-1); passed=leak<=20 and pressure>10 and tank>=0; score=sum((leak<=20,pressure>10,tank>=0))/3; details={"leak_m3":leak,"max_pressure_m":pressure,"min_tank_level_m":tank}
    elif route_id=="fds_smoke_fire":
        vis=[float(o["room_b_visibility_m"]) for o in obs[1:]]; temp=[float(o["room_b_temperature_c"]) for o in obs[1:]]; passed=bool(vis) and min(vis)>=25 and max(temp)<=80; score=sum((bool(vis) and min(vis)>=25,bool(temp) and max(temp)<=80))/2; details={"min_visibility_m":min(vis,default=None),"max_temperature_c":max(temp,default=None),"continuation":"prefix_replay"}
    elif route_id=="modelica_buildings_aixlib":
        vals=[float(o[k]) for o in obs[1:] for k in ("room_a_temperature_c","room_b_temperature_c")]; bad=[v for v in vals if not 18<=v<=26]; score=1-len(bad)/max(1,len(vals)); passed=bool(vals) and not bad; details={"temperature_range_c":[min(vals,default=None),max(vals,default=None)]}
    elif route_id=="d3_citylearn_multi_system":
        temps=[float(o["indoor_dry_bulb_temperature"]) for o in obs[1:]]; grid=sum(max(0,float(o.get("net_electricity_consumption",0))) for o in obs[1:]); base_obs=observations(baseline or {})[1:]; base_grid=sum(max(0,float(o.get("net_electricity_consumption",0))) for o in base_obs); comfort=all(17<=v<=27 for v in temps); energy=grid<=base_grid+1e-8; passed=bool(temps) and comfort and energy; score=(comfort+energy)/2; details={"temperature_range_c":[min(temps,default=None),max(temps,default=None)],"grid_import_kwh":grid,"idle_grid_import_kwh":base_grid,"strong_d3_eligible":False}
    elif route_id=="d3_citylearn_multibuilding_competition":
        xs=obs[1:]; temps=[float(v) for o in xs for k,v in o.items() if k.endswith(".indoor_dry_bulb_temperature")]; head=[float(o.get("shared_meter_headroom_kwh",-math.inf)) for o in xs]; comfort=bool(temps) and all(17<=v<=27 for v in temps); capacity=bool(head) and min(head)>=0; passed=comfort and capacity; score=(comfort+capacity)/2; details={"temperature_range_c":[min(temps,default=None),max(temps,default=None)],"min_shared_meter_headroom_kwh":min(head,default=None),"constraint_origin":"benchmark_layer"}
    elif route_id=="d3_wntr_water_competition":
        xs=obs[1:]; shower=sum(max(0,float(o.get("served_shower_m3_s",0)))*3600 for o in xs); laundry=sum(max(0,float(o.get("served_laundry_m3_s",0)))*3600 for o in xs); tank=min((float(o.get("tank_level_m",-1)) for o in xs),default=-1); passed=shower>0 and laundry>0 and tank>=0; score=sum((shower>0,laundry>0,tank>=0))/3; details={"served_shower_m3":shower,"served_laundry_m3":laundry,"min_tank_level_m":tank}
    elif route_id=="d3_modelica_shared_heat":
        xs=obs[1:]; temps=[float(o[k]) for o in xs for k in ("room_a_temperature_c","room_b_temperature_c")]; dhw=[float(o["dhw_temperature_c"]) for o in xs]; comfort=bool(temps) and all(18<=v<=24 for v in temps); hot=bool(dhw) and min(dhw)>=40; both=any(float(o.get("allocated_space_heat_w",0))>0 and float(o.get("allocated_dhw_heat_w",0))>0 for o in xs); passed=comfort and hot and both; score=(comfort+hot+both)/3; details={"temperature_range_c":[min(temps,default=None),max(temps,default=None)],"min_dhw_temperature_c":min(dhw,default=None),"both_services_observed":both}
    elif route_id=="d3_ev2gym_electric_competition":
        last={0:None,1:None}; appeared={0:False,1:False}; overloaded=False
        for o in obs[1:]:
            overloaded=overloaded or bool(o.get("transformer",{}).get("overloaded"))
            for i,p in enumerate(o.get("ports",[])):
                if p.get("connected") and p.get("soc") is not None: appeared[i]=True; last[i]=float(p["soc"])
        ready=all(appeared.values()) and all((v or 0)>=0.8 for v in last.values()); passed=ready and not overloaded; score=(ready+(not overloaded))/2; details={"last_connected_soc":last,"ports_appeared":appeared,"overloaded":overloaded}
    elif route_id=="d3_energyplus_shared_ventilation":
        xs=obs[1:]; terminal=xs[-1] if xs else {}; co2=[float(terminal.get("zone_a_co2_ppm",math.inf)),float(terminal.get("zone_b_co2_ppm",math.inf))]; temps=[float(o[k]) for o in xs for k in ("zone_a_temperature_c","zone_b_temperature_c")]; fresh=max(co2)<=1200; safe=bool(temps) and all(10<=v<=35 for v in temps); passed=fresh and safe; score=(fresh+safe)/2; details={"terminal_co2_ppm":co2,"temperature_range_c":[min(temps,default=None),max(temps,default=None)]}
    else: raise KeyError(route_id)
    return {"pass":bool(passed),"score":round(float(score),6),"reason":"passed" if passed else "contract_failed","details":jsonable(details)}


def primary_ratio(agent: Mapping[str, Any], idle: Mapping[str, Any]) -> dict[str, Any] | None:
    a=agent.get("metrics",{}); b=idle.get("metrics",{})
    common=[k for k in a if k in b and a[k].get("unit")==b[k].get("unit") and a[k].get("kind") not in {"service","deficit"}]
    if not common: return None
    key=common[0]; av=float(a[key]["value"]); bv=float(b[key]["value"])
    return {"metric":key,"agent":av,"idle":bv,"unit":a[key]["unit"],"agent_over_idle":round(av/bv,6) if bv>0 else None}


def run_case(spec: Mapping[str, Any], client: ChatClient, maximum: int) -> dict[str, Any]:
    route_id=spec["route_id"]; meta=route_metadata(route_id); cadence=float(meta["cadence_seconds"]); steps=int(round(float(meta["horizon_seconds"])/cadence))
    idle=run_idle(spec,steps,cadence); idle_eval=evaluate(route_id,idle,None); idle_phys=physical_consumption(route_id,idle,cadence)
    agent=run_llm(spec,steps,cadence,client,maximum); agent_eval=evaluate(route_id,agent,idle); agent_phys=physical_consumption(route_id,agent,cadence)
    eligible=route_id!="d3_citylearn_multi_system"
    return {"case_id":spec["case_id"],"route_id":route_id,"layer":meta["layer"],"query":spec["query"],"benchmark_contract":spec["benchmark_contract"],"seed":int(spec.get("seed",17)),"cadence_seconds":cadence,"horizon_steps":steps,
            "claim_eligible":eligible,"capability_note":meta.get("capability_note"),
            "agent":{"run":agent,"evaluation":agent_eval,"physical_consumption":agent_phys},
            "idle_baseline":{"run":idle,"evaluation":idle_eval,"physical_consumption":idle_phys},
            "comparison":{"agent_pass_idle_fail":bool(agent_eval["pass"] and not idle_eval["pass"]),"idle_also_passed":bool(agent_eval["pass"] and idle_eval["pass"]),"primary_consumption":primary_ratio(agent_phys,idle_phys)}}


def compact_result(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"case_id":item["case_id"],"route_id":item["route_id"],"layer":item["layer"],"query":item["query"],"claim_eligible":item["claim_eligible"],"capability_note":item.get("capability_note"),
            "agent":{"ok":item["agent"]["run"]["ok"],"termination":item["agent"]["run"]["termination"],"error":item["agent"]["run"].get("error"),"evaluation":item["agent"]["evaluation"],"model_usage":item["agent"]["run"]["model_usage"],"compute":item["agent"]["run"]["compute"],"physical_consumption":item["agent"]["physical_consumption"]},
            "idle_baseline":{"ok":item["idle_baseline"]["run"]["ok"],"evaluation":item["idle_baseline"]["evaluation"],"compute":item["idle_baseline"]["run"]["compute"],"physical_consumption":item["idle_baseline"]["physical_consumption"]},"comparison":item["comparison"]}


def main() -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--specs",type=Path,default=DEFAULT_SPECS); parser.add_argument("--output-dir",type=Path,default=DEFAULT_OUTPUT); parser.add_argument("--base-url",default=DEFAULT_BASE_URL); parser.add_argument("--model"); parser.add_argument("--route"); parser.add_argument("--max-llm-decisions",type=int); parser.add_argument("--resume",action="store_true"); args=parser.parse_args()
    specs=json.loads(args.specs.read_text()); model=args.model or specs["model"]; maximum=args.max_llm_decisions or int(specs["max_llm_decisions"]); api_key=os.environ.get("AIGC_API_KEY")
    if not api_key: raise SystemExit("AIGC_API_KEY is required")
    cases=[c for c in specs["cases"] if not args.route or c["route_id"]==args.route]
    if args.route and not cases: raise SystemExit(f"unknown route in specs: {args.route}")
    args.output_dir.mkdir(parents=True,exist_ok=True); private_dir=args.output_dir/"private"; private_dir.mkdir(exist_ok=True)
    client=ChatClient(args.base_url,model,api_key); results=[]
    for index,spec in enumerate(cases,1):
        public_path=args.output_dir/(spec["route_id"]+".json"); private_path=private_dir/(spec["route_id"]+".json")
        if args.resume and public_path.exists() and private_path.exists():
            results.append(json.loads(public_path.read_text())); print(f"[{index}/{len(cases)}] resume {spec['route_id']}",flush=True); continue
        print(f"[{index}/{len(cases)}] run {spec['route_id']}",flush=True); item=run_case(spec,client,maximum); compact=compact_result(item); results.append(compact)
        private_path.write_text(json.dumps(item,indent=2,ensure_ascii=False,sort_keys=True)+"\n"); public_path.write_text(json.dumps(compact,indent=2,ensure_ascii=False,sort_keys=True)+"\n")
        print(f"  agent={compact['agent']['evaluation']['pass']} idle={compact['idle_baseline']['evaluation']['pass']} calls={compact['agent']['model_usage'].get('calls')}",flush=True)
    totals={k:sum(int(r["agent"]["model_usage"].get(k,0) or 0) for r in results) for k in ("prompt_tokens","completion_tokens","total_tokens","cache_read_tokens","cache_write_tokens","cached_tokens","effectiveCachedTokens")}
    completed=sum(bool(r["agent"]["ok"]) for r in results); passed=sum(bool(r["agent"]["evaluation"]["pass"]) for r in results); idle_passed=sum(bool(r["idle_baseline"]["evaluation"]["pass"]) for r in results); wins=sum(bool(r["comparison"]["agent_pass_idle_fail"]) for r in results); eligible=[r for r in results if r["claim_eligible"]]
    summary={"schema":"backend-casebook-llm-pilot-result.v1","status":"PILOT_ONLY","formal_benchmark":False,"model":model,"seed":17,"route_count":len(results),"execution_completed":completed,"execution_success_rate":completed/max(1,len(results)),"agent_passed":passed,"agent_task_success_rate":passed/max(1,len(results)),"idle_passed":idle_passed,"idle_success_rate":idle_passed/max(1,len(results)),"agent_pass_idle_fail":wins,"claim_eligible_route_count":len(eligible),"claim_eligible_agent_passed":sum(r["agent"]["evaluation"]["pass"] for r in eligible),"model_usage_totals":totals,"results":results,"interpretation_warnings":["These hand-authored case contracts are pilot-only and are not human-grounded benchmark admissions.","An agent pass where idle also passes is not evidence that the task requires persistent intelligence.","Physical consumption retains native units; heterogeneous units are not summed.","d3_citylearn_multi_system is excluded from strong-D3 claim eligibility.","FDS is prefix replay, not verified native online continuation."]}
    (args.output_dir/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False,sort_keys=True)+"\n"); print(json.dumps({k:summary[k] for k in ("route_count","execution_completed","agent_passed","idle_passed","agent_pass_idle_fail","model_usage_totals")},ensure_ascii=False),flush=True)
    return 0 if completed==len(results) else 2


if __name__=="__main__": raise SystemExit(main())
