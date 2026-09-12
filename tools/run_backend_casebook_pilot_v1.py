#!/usr/bin/env python3
"""Auditable protocol pilot: freeze contracts, gate with witnesses, then run LLM.

All policies use public state and one decision per native step. No oracle or
future fault schedule is supplied to the model. Excluded routes remain visible.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.run_backend_casebook_llm_pilot import (
    ChatClient, canonical, digest, evaluate as old_evaluate, idle_action,
    jsonable, observations, parse_action, physical_consumption, route_factory,
    summarize_model_usage, _rusage, _delta_usage,
)
from unified_compiler.route_registry import route_metadata
from unified_compiler.llm_conversation import OBSERVATION_DELTA_INSTRUCTIONS, observation_delta, apply_observation_delta
from backend_casebook_v0.claim_designs import DESIGNS
from backend_casebook_v0.d0_contract import PUBLIC_CONTRACT as D0_CONTRACT, evaluate as evaluate_d0

OUT = ROOT / 'generated/backend_casebook_pilot_v1'
GARAGE = {'device_id': 'garage_door.main', 'capability': 'garage.door',
          'operation': 'close', 'parameters': {}}

# Outcome definitions are frozen before witness or model execution.
CASES = [
    dict(route_id='d0_exogenous_context', query='Whenever everyone is away, keep the front door closed and the interior lights off until someone comes home.',
         criteria=D0_CONTRACT['text']),
    dict(route_id='d1_discrete_device_fault', query="After I park, make sure the garage door doesn't stay open, even if it gets stuck or reopens.",
         criteria='Whenever the door is open and available, restore closed within one 60-second response step and keep it closed. Newly observed external obstruction gets one response step. Do not command an unavailable device. Finish closed.'),
    dict(route_id='d1_ev2gym_fault', query='Have my car charged enough by its scheduled departure each morning, and avoid expensive charging when there is enough time.',
         criteria='Meet the public required_departure_soc at the public declared_departure_time, never exceed household_power_limit_kw. Charging cost is reported separately after success; no claim of cost optimality.'),
    dict(route_id='energyplus_iaq', query='Keep the indoor air from getting stuffy throughout the day, without running ventilation harder than needed.',
         criteria='Every post-action CO2 sample must be at most 1200 ppm. Temperature must remain in [5,40] C and relative humidity in [0,100] percent. These are safety bounds, not a comfort claim. Report ventilation effort separately.'),
    dict(route_id='d3_energyplus_shared_ventilation', query='Clear the stale air in both rooms, then keep both rooms fresh even when they share the same ventilation system.',
         criteria='Initial CO2 is already elevated. Recover both rooms to at most 1200 ppm within 90 minutes, then keep every subsequent sample at or below 1200 ppm. Both temperatures must stay in [10,35] C throughout. The two requests share 0.8 m3/s; requests are fractions in [0,1].'),
]

QUARANTINE = {
    'd1_sustaingym_fault': 'Scenario starts cold; cooling query and initial conditions mismatch. Needs feasible heating/cooling contract and witness.',
    'd1_citylearn_battery_fault': 'Solar-only query conflicts with empty battery and selected solar window. Feasibility unproven.',
    'wntr_residential_water': 'Household units need calibration; previous one-sample pressure test did not enforce sustained service.',
    'fds_smoke_fire': 'Idle passes; smoke challenge absent in short horizon. Prefix replay, not native online continuation.',
    'modelica_buildings_aixlib': 'Idle passes with unchanged 20 C. Need causal cooling disturbance and feasible recovery.',
    'd3_citylearn_multi_system': 'Idle passes; strong cross-channel coupling unproven.',
    'd3_citylearn_multibuilding_competition': 'Empty initial battery and shared-meter constraint: feasibility unproven.',
    'd3_wntr_water_competition': 'Positive flow alone is insufficient service; need demand/deadline/shortfall with calibrated household units.',
    'd3_modelica_shared_heat': 'Old evaluator demanded simultaneous actuation, not outcome. Need service-demand trajectories before admission.',
    'd3_ev2gym_electric_competition': 'Observation mask naming fixed. Departure SOC needs terminal event receipt, and isolated runtime/feasibility remain unverified.',
}


def public_view(route_id, obs, legal):
    """Declared task scope removes unrelated garage devices, never adds state."""
    obs, legal = deepcopy(obs), deepcopy(legal)
    if route_id == 'd1_discrete_device_fault':
        obs = {k: v for k, v in obs.items() if k in {
            'step', 'time_seconds', 'terminal', 'events', 'active_device_faults', 'devices'}}
        obs['devices'] = {'garage_door.main': obs['devices']['garage_door.main']}
        legal = {'type': 'harness_agent_action', 'commands': {
            'garage_door.main': legal['commands']['garage_door.main']}}
    return obs, legal


def action_example(route_id):
    return {
        'd0_exogenous_context': {'kind': 'act', 'command': {'target': 'interior_lights', 'operation': 'off'}},
        'd1_discrete_device_fault': {'kind': 'act', 'commands': [GARAGE]},
        'd1_ev2gym_fault': {'type': 'SET_CHARGE_POWER', 'kw': 1.0},
        'energyplus_iaq': 0.5,
        'd3_energyplus_shared_ventilation': {'zone_a_airflow_request': 0.5, 'zone_b_airflow_request': 0.5},
    }[route_id]


def validate_action(route_id, action, obs, legal):
    """Pure preflight; invalid answers cannot mutate or poison a simulator."""
    def exact(value, keys):
        if not isinstance(value, dict) or set(value) != set(keys):
            raise ValueError('expected object fields: ' + ', '.join(keys))
    def number(value, low, high):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            raise ValueError(f'expected finite number in [{low},{high}]')
    canonical(action)
    if route_id == 'd0_exogenous_context':
        exact(action, ['kind', 'command'])
        if action['kind'] != 'act': raise ValueError('kind must be act')
        exact(action['command'], ['target', 'operation'])
        c = action['command']
        if c['operation'] not in {'front_door': ['open', 'close'], 'interior_lights': ['on', 'off']}.get(c['target'], []):
            raise ValueError('unknown target/operation')
    elif route_id == 'd1_discrete_device_fault':
        exact(action, ['kind', 'commands'])
        if action['kind'] != 'act' or not isinstance(action['commands'], list) or len(action['commands']) > 1:
            raise ValueError('kind=act and zero or one garage command required')
        for c in action['commands']:
            exact(c, ['device_id', 'capability', 'operation', 'parameters'])
            if c['device_id'] != GARAGE['device_id'] or c['capability'] != GARAGE['capability'] or c['operation'] not in ['open', 'close'] or c['parameters'] != {}:
                raise ValueError('invalid garage command')
            # Availability is a backend execution outcome, not an interface
            # syntax error. Do not replace a model's attempted action with a
            # free correction: the failed attempt consumes its native tick.
    elif route_id == 'd1_ev2gym_fault':
        exact(action, ['type', 'kw'])
        if action['type'] != 'SET_CHARGE_POWER': raise ValueError('unknown action type')
        number(action['kw'], 0, float(obs['charger_max_power_kw']))
    elif route_id == 'energyplus_iaq': number(action, 0, 1)
    elif route_id == 'd3_energyplus_shared_ventilation':
        exact(action, ['zone_a_airflow_request', 'zone_b_airflow_request'])
        for v in action.values(): number(v, 0, 1)
    else: raise ValueError('unsupported route')


def witness_action(route_id, obs, legal, policy):
    if policy == 'idle': return idle_action(route_id, legal)
    if route_id == 'd0_exogenous_context':
        c = {'target': 'interior_lights', 'operation': 'off'}
        if obs['context']['occupancy_count'] == 0 and obs['devices']['front_door'] != 'closed':
            c = {'target': 'front_door', 'operation': 'close'}
        return {'kind': 'act', 'command': c}
    if route_id == 'd1_discrete_device_fault':
        d = obs['devices']['garage_door.main']
        return {'kind': 'act', 'commands': [deepcopy(GARAGE)] if d['state'] == 'open' and d.get('availability', 'available') == 'available' else []}
    if route_id == 'd1_ev2gym_fault':
        power = max(0, min(obs['charger_max_power_kw'], obs['household_power_limit_kw'] - obs['household_load_kw']))
        return {'type': 'SET_CHARGE_POWER', 'kw': power if obs['vehicle_connected'] and obs['vehicle_soc'] < obs['required_departure_soc'] else 0}
    if route_id == 'energyplus_iaq': return 1.0
    if route_id == 'd3_energyplus_shared_ventilation':
        return {'zone_a_airflow_request': 1.0, 'zone_b_airflow_request': 1.0}
    raise ValueError(route_id)


def evaluate(spec, run):
    route_id = spec['route_id']
    if not run['ok']: return {'pass': False, 'reason': 'execution_error', 'details': {}}
    if route_id == 'd0_exogenous_context':
        return evaluate_d0(run['initial_observation'], [f['receipt'] for f in run['frames']])
    if route_id == 'd1_discrete_device_fault':
        violations = []
        for frame in run['frames']:
            o = frame['receipt']['observation']; d = o['devices']['garage_door.main']
            previous = run['initial_observation'] if frame['index'] == 0 else run['frames'][frame['index']-1]['receipt']['observation']
            pd = previous['devices']['garage_door.main']
            actionable_before = pd.get('availability', 'available') == 'available'
            external_now = any(e.get('type') == 'garage_door_obstruction' for e in o.get('events', []))
            if actionable_before and not external_now and d['state'] != 'closed': violations.append(frame['index'])
        final_closed = observations(run)[-1]['devices']['garage_door.main']['state'] == 'closed'
        return {'pass': not violations and final_closed, 'details': {'violation_steps': violations, 'final_closed': final_closed}}
    if route_id == 'd3_energyplus_shared_ventilation':
        # Frames must contain every native transition, not only LLM wakeups.
        # This historical task's disclosed thresholds remain unchanged; never
        # fit them to the model result. Current case admission is separate.
        frames = run.get('frames', [])
        meta = route_metadata(route_id)
        fields = ('zone_a_co2_ppm', 'zone_b_co2_ppm', 'zone_a_temperature_c', 'zone_b_temperature_c')
        def finite(value):
            return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
        try:
            xs = [f['receipt']['observation'] for f in frames]
            times = [f['receipt']['time_seconds'] for f in frames]
            initial = run['initial_observation']
            if not times or not all(finite(t) for t in times):
                raise ValueError('missing or invalid native simulation timestamps')
            previous = 0.0
            for t in times:
                if not math.isclose(t - previous, meta['cadence_seconds'], abs_tol=1e-6):
                    raise ValueError('missing, duplicated or out-of-order native samples')
                previous = t
            if not math.isclose(times[-1], meta['horizon_seconds'], abs_tol=1e-6):
                raise ValueError('incomplete declared horizon')
            if not all(finite(o[k]) for o in [initial, *xs] for k in fields):
                raise ValueError('non-finite or invalid public measurements')
        except (KeyError, TypeError, ValueError) as exc:
            return {'pass': False, 'evaluated': False, 'task_success': None,
                    'reason': 'invalid_or_incomplete_trajectory', 'details': {'error': str(exc)}}
        violations = [i for i, (t, o) in enumerate(zip(times, xs))
                      if t >= 5400 and max(o['zone_a_co2_ppm'], o['zone_b_co2_ppm']) > 1200]
        safe = all(10 <= o[k] <= 35 for o in [initial, *xs] for k in fields[2:])
        return {'pass': safe and not violations, 'evaluated': True,
                'details': {'co2_violation_steps_after_90_minutes': violations,
                            'temperature_safe': safe, 'samples_checked': len(xs),
                            'final_co2': [xs[-1][k] for k in fields[:2]]}}
    return old_evaluate(route_id, run)


def run(spec, policy, client=None):
    route_id = spec['route_id']; meta = route_metadata(route_id)
    cadence = float(meta['cadence_seconds']); steps = round(float(meta['horizon_seconds'])/cadence)
    route = route_factory(route_id); started = time.monotonic(); usage = _rusage()
    frames=[]; calls=[]; exchanges=[]; initial=None; error=None; repairs=0
    try:
        reset = route.reset(seed=17); initial = jsonable(reset['observation']); obs = initial
        system = canonical({
            'role': 'closed_loop_household_controller', 'query': spec['query'],
            'outcome_requirements': spec['criteria'],
            'response_format': 'Return only a JSON object {"action": NATIVE_ACTION}. No prose. A single <answer> envelope or json code fence is also accepted.',
            'wire_format_example_not_a_policy': {'action': action_example(route_id)},
            'semantics': ['Each accepted action advances exactly one native step; its observation delta then arrives.',
                          'Missing availability in the RECONSTRUCTED state means available; omission from a delta means unchanged.',
                          'Action acceptance is not proof the intended state was achieved. Check the next observation and continue until termination.',
                          'Current events and faults may change. Future events and private evaluator state are unavailable.',
                          'Invalid output gets at most two correction attempts without advancing simulated time; all calls count.',
                          'For the garage, empty commands means wait one 60-second step; only this task device is in scope.'],
            'cadence_seconds': cadence, 'planned_steps': steps,
            'observation_delta_instructions': list(OBSERVATION_DELTA_INSTRUCTIONS),
        })
        history=[{'role':'system','content':system}]; previous_view=None; last_feedback=None
        for i in range(steps):
            legal = route.legal_actions(); view, public_legal = public_view(route_id, obs, legal)
            if client:
                payload={'native_step':i,'remaining_steps':steps-i,'legal_actions':public_legal,'action_result':last_feedback}
                if previous_view is None:
                    payload.update(message_type='initial_request',initial_observation=view)
                else:
                    delta=observation_delta(previous_view,view)
                    assert apply_observation_delta(previous_view,delta)==view
                    payload.update(message_type='environment_observation',observation_delta=delta)
                message={'role':'user','content':canonical(payload)}
                messages=[*history,message]
                for attempt in range(3):
                    response=client.complete(messages)
                    response['response_bytes']=len(response['content'].encode())
                    calls.append(response)
                    raw=response['content']; messages.append({'role':'assistant','content':raw})
                    try:
                        error_stage='response_format'
                        action=parse_action(raw)
                        error_stage='action_legality'
                        validate_action(route_id,action,view,public_legal)
                        break
                    except (ValueError,TypeError,KeyError) as exc:
                        calls[-1]['protocol_error']=str(exc)
                        calls[-1]['error_stage']=error_stage
                        repairs+=1
                        if attempt==2: raise ValueError('protocol repair budget exhausted: '+str(exc))
                        messages.append({'role':'user','content':canonical({'protocol_error':str(exc),'time_advanced':False,'instruction':'Correct the action JSON using the current schema.'})})
                exchanges.append({'native_step':i,'new_messages':deepcopy(messages[len(history):]),'parsed_action':deepcopy(action)})
                history=messages
                previous_view=deepcopy(view)
            else:
                action=witness_action(route_id,obs,legal,policy)
                validate_action(route_id,action,view,public_legal)
            receipt=jsonable(route.step(deepcopy(action),dt_seconds=cadence))
            frames.append({'index':i,'action':deepcopy(action),'receipt':receipt})
            obs=receipt['observation']; last_feedback={'action':action,'info':receipt.get('info',{}),'time_seconds':receipt['time_seconds']}
            if receipt.get('done'): break
        ok=len(frames)==steps or bool(frames and frames[-1]['receipt'].get('done'))
    except Exception as exc:
        ok=False; error=f'{type(exc).__name__}: {exc}'
    finally:
        route.close()
    result={'ok':ok,'error':error,'initial_observation':initial,'frames':frames,'calls':calls,
            'exchanges':exchanges,'system_prompt':locals().get('system'),'protocol':'initial_full_then_lossless_delta',
            'protocol_errors':repairs,'model_usage':summarize_model_usage(calls),
            'compute':{**_delta_usage(usage,_rusage()),'wall_seconds':time.monotonic()-started,'backend_steps':len(frames)}}
    result['evaluation']=evaluate(spec,result)
    result['physical_consumption']=physical_consumption(route_id,result,cadence)
    return result


def main():
    p=argparse.ArgumentParser(); p.add_argument('--phase', choices=['gate','llm'],required=True)
    p.add_argument('--output-dir',type=Path,default=OUT); p.add_argument('--route'); args=p.parse_args()
    out=args.output_dir; out.mkdir(parents=True,exist_ok=True)
    client=None
    if args.phase=='llm': client=ChatClient('https://aigc.sankuai.com/v1/openai/native','deepseek-v4-flash-meituan',os.environ['AIGC_API_KEY'])
    for spec in CASES:
        rid=spec['route_id']
        if args.route and args.route!=rid: continue
        target=out/(rid+'.json')
        if args.phase=='gate':
            print('GATE '+rid,flush=True)
            idle=run(spec,'idle'); witness=run(spec,'witness')
            admitted=idle['ok'] and witness['ok'] and witness['evaluation']['pass'] and not idle['evaluation']['pass']
            record={'spec':spec,'spec_hash':digest(spec),'seed':17,'idle':idle,'witness':witness,'admitted':admitted,
                    'admission_scope':'feasible and non-idle-trivial only; does not establish persistent-planning difficulty or human grounding'}
        else:
            record=json.loads(target.read_text())
            if record['spec_hash']!=digest(spec): raise ValueError('contract changed after gate')
            if not record['admitted']:
                print('SKIP unadmitted '+rid,flush=True); continue
            print('LLM '+rid,flush=True); record['agent']=run(spec,'llm',client)
        target.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({'route':rid,'admitted':record['admitted'],'witness':record['witness']['evaluation'],
                          'agent':record.get('agent',{}).get('evaluation'),'error':record.get('agent',record['witness']).get('error')},ensure_ascii=False),flush=True)
    records=[json.loads((out/(s['route_id']+'.json')).read_text()) for s in CASES if (out/(s['route_id']+'.json')).exists()]
    agents=[r for r in records if 'agent' in r]; admitted=[r for r in records if r['admitted']]
    summary={'schema':'backend-pilot.v1','formal_benchmark':False,'model':'deepseek-v4-flash-meituan',
             'candidate_count':len(CASES),'gated_count':len(records),'admitted_count':len(admitted),'evaluated_count':len(agents),
             'passed':sum(r['agent']['evaluation']['pass'] for r in agents),
             'execution_completed':sum(r['agent']['ok'] for r in agents),'protocol_errors':sum(r['agent']['protocol_errors'] for r in agents),
             'model_usage':summarize_model_usage([c for r in agents for c in r['agent']['calls']]),
             'quarantine':QUARANTINE,
             'cases':[{'route_id':r['spec']['route_id'],'admitted':r['admitted'],'witness':r['witness']['evaluation'],
                       'idle':r['idle']['evaluation'],'agent':r.get('agent',{}).get('evaluation'),
                       'model_usage':r.get('agent',{}).get('model_usage')} for r in records],
             'limitations':['One development seed; no generalization estimate.',
                            'Case queries are hand-authored, not admitted human-grounded dataset items.',
                            'Witness is a feasibility proof, not optimal policy or gold action sequence.',
                            'One-step reactive witnesses may pass; sustained observations do not prove planning complexity.',
                            'Protocol repairs have zero simulated-time cost and counted model cost.',
                            'Do not compare success rates to v0 as a model improvement: contracts, cadence and denominator changed.']}
    summary['all_backend_coverage']=[{
        'route_id':d['route'],'query':d['query'],'claim':d['claim'],
        'execution_status':next(('development_run_available' if 'agent' in r else 'witness_checked'
                                for r in records if r['spec']['route_id']==d['route']), 'scenario_implementation_pending'),
        'claim_admission':'pending_mechanism_contrast','remaining_work':d['missing'],
    } for d in DESIGNS]
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:summary[k] for k in ['admitted_count','evaluated_count','passed','protocol_errors','model_usage']},ensure_ascii=False),flush=True)


if __name__=='__main__': main()
