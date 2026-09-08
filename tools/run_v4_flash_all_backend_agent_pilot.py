"""Independent 15-route V4 Flash Agent pilot bridge (pilot-only).

It uses the existing ChatClient transport and answer envelope, but constructs
its own route-specific public task messages and evaluator contracts. No old
workflow Episode IDs, Harness evaluator, or witness action is sent to the
Agent.
"""
from __future__ import annotations
import argparse, concurrent.futures, hashlib, json, os, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
DEFAULT_BASE="https://aigc.sankuai.com/v1/openai/native"
DEFAULT_MODEL="deepseek-v4-flash-meituan"

def digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,default=str).encode()).hexdigest()
def parse_answer(content):
    if not isinstance(content,str): raise ValueError('response_not_text')
    t=content.strip()
    if t.startswith('<answer>') and t.endswith('</answer>'): t=t[8:-9].strip()
    x=json.loads(t)
    if not isinstance(x,dict): raise ValueError('response_not_object')
    if set(x) != {'action'}:
        raise ValueError('response_missing_or_extra_action_envelope_fields')
    # The envelope is always an object on the wire; the value keeps the
    # route's native scalar/list/dict schema and is decoded before step().
    return x['action']

def _public_contract(route):
    p=ROOT/'generated/episode_pilot_luna_final_v1'/'task_contracts.json'
    if p.exists():
        try:
            for c in json.loads(p.read_text()).get('contracts',[]):
                if c.get('route_id')==route:
                    return c
        except Exception:
            pass
    return None

def contract(route):
    if route=='d0_exogenous_context': return ('Turn interior lights off for this decision.',0)
    return (f'Execute one valid {route} control that changes the requested native actuator state while respecting the published action schema.',1)

def _public_schema(value):
    """Describe native shape without publishing witness values."""
    if isinstance(value, dict):
        return {"type": "object", "properties": {str(k): _public_schema(v) for k, v in value.items()}}
    if isinstance(value, list):
        return {"type": "array", "items": _public_schema(value[0]) if value else {"type": "any"}}
    if isinstance(value, bool): return {"type": "boolean"}
    if isinstance(value, (int, float)): return {"type": "number"}
    return {"type": "string"}

def _trace_digest(trace):
    return digest([(f.get("time_seconds"), f.get("observation")) for f in trace])

def run_model_episode(route, route_obj, client, frozen, meta, *, max_steps=None):
    """Run the real multi-turn bridge; client is injectable for offline tests."""
    from evaluate_workflow_v4_flash_v10 import observation_delta, apply_observation_delta
    from v4_flash_pilot_evaluator import score_run
    reset=route_obj.reset(seed=frozen.get('seed',17)); obs=reset['observation']; obs0=obs
    legal=route_obj.legal_actions(); steps=int(max_steps or frozen['pilot_horizon_steps'])
    protocol={'route_id':route,'native_action_schema':_public_schema(legal),'envelope':'<answer>{"action": NATIVE_ACTION}</answer>','observation_fields':sorted(obs0) if isinstance(obs0,dict) else [],'units':meta.get('native_time_unit'),'continuation':meta.get('continuation'),'public_only':True}
    messages=[{'role':'system','content':json.dumps({'role':'V4 Flash pilot controller','protocol':protocol,'rule':'Return exactly one <answer>{"action": ...}</answer>; action value must match the published native schema.'},sort_keys=True)},
              {'role':'user','content':json.dumps({'query':frozen['query'],'initial_observation':obs0,'action_protocol':protocol},sort_keys=True)}]
    trace=[]; actions=[]; calls=0; rejected=0; termination='budget_exhausted'
    for index in range(steps):
        response=client.complete(messages); calls += 1
        raw=response.get('content','') if isinstance(response,dict) else ''
        usage=response.get('usage') or {} if isinstance(response,dict) else {}
        action=parse_answer(raw)
        actions.append({'index':index,'action':action,'status':'parsed','raw_response_sha256':digest(raw),'raw_response_bytes':len(raw.encode()),'output_tokens':usage.get('completion_tokens',usage.get('output_tokens')) if usage else 'unavailable'})
        messages.append({'role':'assistant','content':'<answer>'+json.dumps({'action':action},sort_keys=True,separators=(',',':'))+'</answer>'})
        try:
            receipt=route_obj.step(action)
        except Exception as exc:
            actions[-1].update(status='rejected',error=type(exc).__name__+':'+str(exc)); rejected += 1
            messages.append({'role':'user','content':json.dumps({'message_type':'environment_observation','action_result':{'status':'rejected','error_class':type(exc).__name__},'observation_delta':None},sort_keys=True)})
            termination='agent_action_rejected'; break
        actions[-1]['status']='accepted'
        nxt=receipt['observation']; delta=observation_delta(obs,nxt)
        frame={'time_seconds':receipt.get('time_seconds'),'observation':nxt,'done':bool(receipt.get('done'))}; trace.append(frame)
        reconstructed=apply_observation_delta(obs,delta)
        if reconstructed != nxt: termination='delta_reconstruction_error'; break
        messages.append({'role':'user','content':json.dumps({'message_type':'environment_observation','action_result':{'status':'accepted','time_seconds':receipt.get('time_seconds'),'delta_t_seconds':receipt.get('delta_t_seconds')},'observation_delta':delta},sort_keys=True)})
        obs=nxt
        if receipt.get('done'):
            termination='native_done'; break
        if index == steps-1: termination='contract_horizon_reached'
    run={'ok':len(trace)==steps and termination in {'contract_horizon_reached','native_done'},'initial_observation':obs0,'trace':trace}
    score=score_run(run,frozen['task_clauses'])
    return {'calls':calls,'actions':actions,'rejected_actions':rejected,'messages':messages,'trace':trace,'termination':termination,'score':score,'trace_digest':_trace_digest(trace),'public_protocol':protocol}

def run_route(route, base, model, out):
    from evaluate_harness_v2_v4_flash import ChatClient
    from evaluate_workflow_v4_flash_v10 import observation_delta, apply_observation_delta
    from unified_compiler.agent_interface import make_agent_backend
    from unified_compiler.route_registry import route_metadata
    from v4_flash_pilot_evaluator import score_run
    started=time.monotonic(); seed=17; query,_=contract(route); meta=route_metadata(route)
    frozen=_public_contract(route)
    if frozen: query=frozen['query']; seed=frozen.get('seed',seed)
    rec={'route_id':route,'layer':meta['layer'],'query':query,'seed':seed,'model':model,'execution_mode':meta.get('continuation',meta.get('execution_mode')),'status':'failed','failure_class':None,'calls':0,'api_output_tokens':None,'source_hashes':{'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'evaluator_sha256':hashlib.sha256((ROOT/'tools/v4_flash_pilot_evaluator.py').read_bytes()).hexdigest(),'python':sys.executable},'contract':frozen,'actions':[]}
    route_obj=None
    try:
        frozen=_public_contract(route)
        client=ChatClient(base,model,retries=2,timeout=120)
        episode=run_model_episode(route,route_obj,client,frozen,meta)
        rec.update({'calls':episode['calls'],'actions':episode['actions'],'evaluator':episode['score'],'termination':episode['termination'],'agent_trace_digest':episode['trace_digest'],'public_protocol':episode['public_protocol'],'api_output_tokens':next((a['output_tokens'] for a in episode['actions'] if a.get('output_tokens') not in (None,'unavailable')),'unavailable')})
        rec['status']='passed' if episode['score']['pass'] else 'failed'; rec['failure_class']=None if rec['status']=='passed' else ('agent_action_rejected' if episode['termination']=='agent_action_rejected' else 'task_failed')
    except Exception as exc:
        rec['failure_class']='backend_or_transport_error'; rec['error']=type(exc).__name__+':'+str(exc)
    finally:
        if route_obj:
            try:route_obj.close()
            except Exception:pass
    rec['wall_seconds']=round(time.monotonic()-started,6); return rec

def main():
    from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
    p=argparse.ArgumentParser();p.add_argument('--output-dir',type=Path,default=ROOT/'generated/episode_pilot_v4_flash_v1');p.add_argument('--base-url',default=DEFAULT_BASE);p.add_argument('--model',default=DEFAULT_MODEL);p.add_argument('--workers',type=int,default=2);p.add_argument('--calibration-only',action='store_true');a=p.parse_args();a.output_dir.mkdir(parents=True,exist_ok=True)
    routes=list(PUBLIC_ROUTE_IDS); targets=routes[:1] if a.calibration_only else routes
    results=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1,min(a.workers,len(targets)))) as ex:
        fs=[ex.submit(run_route,r,a.base_url,a.model,a.output_dir) for r in targets]
        for f in fs:
            result=f.result(); results.append(result)
            (a.output_dir/(result['route_id']+'.json.tmp')).write_text(json.dumps(result,sort_keys=True,default=str))
            (a.output_dir/(result['route_id']+'.json.tmp')).replace(a.output_dir/(result['route_id']+'.json'))
    results.sort(key=lambda x:routes.index(x['route_id']))
    summary={'schema':'v11.v4_flash_all_backend_agent_pilot.v1','status':'PILOT_ONLY','formal_benchmark':False,'model':a.model,'base_url_host':a.base_url.split('/')[2],'workers':a.workers,'calibration_only':a.calibration_only,'execution_finished':True,'route_count':len(results),'agent_passed':sum(r['status']=='passed' for r in results),'results':results,'budget':{'max_route_calls':15,'max_model_calls_per_route':1,'transport_retries':2,'request_timeout_seconds':120,'max_output_tokens':512,'calibration_route':'d0_exogenous_context'},'limitations':['hand-authored contracts; no frozen membership certification','CityLearn single-building is not verified D3 coupling','FDS online continuation is not claimed']}
    public=[]; private=[]
    for r in results:
        public.append({k:r.get(k) for k in ('route_id','layer','query','seed','execution_mode','status','failure_class','agent_action','agent_receipt','api_output_tokens','raw_response_sha256','raw_response_bytes')})
        private.append(r)
    (a.output_dir/'episodes_public.jsonl').write_text(''.join(json.dumps(x,sort_keys=True,default=str)+'\n' for x in public))
    (a.output_dir/'episodes_private.jsonl').write_text(''.join(json.dumps(x,sort_keys=True,default=str)+'\n' for x in private))
    (a.output_dir/'summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True,default=str)+'\n')
    return 0 if all(r['status']=='passed' for r in results) else 1
if __name__=='__main__':raise SystemExit(main())
