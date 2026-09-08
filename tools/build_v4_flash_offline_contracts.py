"""Freeze and validate hand-authored all-route pilot contracts offline.

No model/API calls occur here. Contracts are authored before native witness
execution; witness, baseline, violation and replay are only used to validate
the contract/evaluator design.
"""
from __future__ import annotations
import hashlib,json,sys,time,signal,math,copy
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS,route_metadata
from v4_flash_pilot_evaluator import score_run as score_run

def dg(x):return hashlib.sha256(json.dumps(x,sort_keys=True,default=str).encode()).hexdigest()

def current_source_binding():
    evaluator_path=Path(__file__).with_name('v4_flash_pilot_evaluator.py')
    return {'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'evaluator_sha256':hashlib.sha256(evaluator_path.read_bytes()).hexdigest()}

# These are semantic engineering tasks, independent of backend probe helper
# variants. They are frozen before native calls and have explicit units,
# tolerances, deadlines and violation controls.
CONTRACTS=[
('d0_exogenous_context','Turn interior lights off during this 60-second decision window.',{'kind':'act','command':{'target':'interior_lights','operation':'off'}},{'kind':'act','command':{'target':'interior_lights','operation':'on'}},{'kind':'act','command':{'target':'interior_lights','operation':'on'}},'terminal devices.interior_lights == off; time_seconds==60; no non-light device changes'),
('d1_sustaingym_fault','Reduce the bounded cooling request for the next control interval while keeping every actuator request finite.',[-0.05]*27,[0.0]*27,[0.0]*27,'terminal finite cooling vector; every value in [-1,1]; one 300-second transition'),
('d1_citylearn_battery_fault','Charge the battery at normalized rate +0.75 until SOC increases by at least 0.05 without exceeding SOC 1.0.',0.75,0.0,-0.75,'terminal battery SOC reaches the predeclared increase/range clause; one 3600-second transition'),
('d1_ev2gym_fault','Set the charger power request to 1.0 kW for one native charging decision.',{'type':'SET_CHARGE_POWER','kw':1.0},{'type':'SET_CHARGE_POWER','kw':0.0},{'type':'SET_CHARGE_POWER','kw':0.0},'terminal native charger receipt accepted; finite power; one 900-second transition'),
('d1_discrete_device_fault','Load the washer at the first decision, then start it at the second; reach a washing public state within four 60-second decisions.',[{'kind':'act','commands':[{'device_id':'laundry.washer','capability':'laundry.control','operation':'load','parameters':{}}]},{'kind':'act','commands':[{'device_id':'laundry.washer','capability':'laundry.control','operation':'start','parameters':{}}]},{'kind':'act','commands':[]},{'kind':'act','commands':[]}],{'kind':'act','commands':[]},{'kind':'act','commands':[]},'terminal workflow state is washing; no private schedule fields; four 60-second decisions'),
('energyplus_iaq','Increase the published IAQ control scalar to 1.0 for one 600-second decision.',1.0,0.0,0.0,'terminal native time advances 600 seconds and public IAQ observation is finite'),
('wntr_residential_water','Maintain the tank level in the public safe band for three hourly hydraulic decisions.',0,0,1,'terminal tank level remains in the declared safe band; open-valve control is the legal targeted violation'),
('fds_smoke_fire','Replay a door-open fraction of 1.0 for one real FDS prefix transition.',1.0,0.0,0.0,'real FDS prefix replay reaches t=1 second; online continuation is not claimed'),
('modelica_buildings_aixlib','Set the scalar heating request to 1.0 for one 60-second FMI decision.',1.0,0.0,0.0,'terminal FMI receipt advances 60 seconds with finite thermal observation'),
('d3_citylearn_multi_system','Charge the native battery at normalized rate 1.0 for one hourly decision while HVAC request remains zero.',{'battery_rate':1.0,'hvac_rate':0.0},{'battery_rate':0.0,'hvac_rate':0.0},{'battery_rate':-1.0,'hvac_rate':0.0},'battery response is finite; explicitly an Episode task, not verified D3 cross-channel coupling'),
('d3_citylearn_multibuilding_competition','Request a capacity-feasible small battery charge in the first declared building while holding the second declared building at zero; both native building ids are required.',{'resstock-amy2018-2021-release-1-102040':{'battery_rate':0.1,'hvac_rate':0.0},'resstock-amy2018-2021-release-1-103125':{'battery_rate':0.0,'hvac_rate':0.0}},{'resstock-amy2018-2021-release-1-102040':{'battery_rate':0.0,'hvac_rate':0.0},'resstock-amy2018-2021-release-1-103125':{'battery_rate':0.0,'hvac_rate':0.0}},{'resstock-amy2018-2021-release-1-102040':{'battery_rate':1.0,'hvac_rate':0.0},'resstock-amy2018-2021-release-1-103125':{'battery_rate':0.0,'hvac_rate':0.0}},'terminal district observation finite; rate 1.0 is a legal negative-headroom counterexample, no native coupling claim'),
('d3_wntr_water_competition','Open the shower valve and keep the laundry valve closed for one hydraulic decision.',{'shower_valve_open':1.0,'laundry_valve_open':0.0},{'shower_valve_open':0.0,'laundry_valve_open':0.0},{'shower_valve_open':0.0,'laundry_valve_open':0.0},'terminal pressure/flow remains finite and shower request is accepted'),
('d3_modelica_shared_heat','Request both space heating and DHW service at 1.0 for one shared-capacity decision.',{'space_heating_request':1.0,'dhw_request':1.0},{'space_heating_request':0.0,'dhw_request':0.0},{'space_heating_request':0.0,'dhw_request':0.0},'terminal allocation and service-shortfall fields are finite; one 60-second step'),
('d3_ev2gym_electric_competition','Request a capacity-feasible 0.5 normalized charge on each connected port for one native transformer decision.',{'charger_0_rate':0.5,'charger_1_rate':0.5},{'charger_0_rate':0.0,'charger_1_rate':0.0},{'charger_0_rate':1.0,'charger_1_rate':1.0},'terminal native transformer receipt records both SOC increases, loading <=15 kW, and feasibility; one 900-second step'),
('d3_energyplus_shared_ventilation','Request zone A airflow at 1.0 while holding zone B at zero for one shared ventilation decision.',{'zone_a_airflow_request':1.0,'zone_b_airflow_request':0.0},{'zone_a_airflow_request':0.0,'zone_b_airflow_request':0.0},{'zone_a_airflow_request':0.0,'zone_b_airflow_request':0.0},'terminal zone airflow/IAQ observations finite; one 900-second step'),
]

# Machine-readable task predicates.  These are frozen design clauses, not
# post-hoc thresholds inferred from a witness trajectory.
CLAUSES={
'd0_exogenous_context': {'steps':3,'target':{'path':'devices.interior_lights','equals':'off'},'constraints':[{'path':'devices.front_door','equals':'closed'}],'legal_counterexample':'all three actions keep lights on; terminal clause fails'},
'd1_sustaingym_fault': {'steps':3,'target':{'path':'zone_temperatures_c.0','max_delta_from_initial':0.5},'constraints':[{'path':'zone_temperatures_c.0','range':[0,50]}],'schema_revision':'public_obs_v2: zone_temperatures_c[0] is the first zone; no RH field is exposed','legal_counterexample':'zero cooling vector is legal but does not satisfy the bounded temperature target'},
'd1_citylearn_battery_fault': {'steps':3,'target':{'path':'battery_soc','min_delta_from_initial':0.05,'range':[0,1]},'constraints':[{'path':'non_shiftable_load_kwh','finite':True}],'legal_counterexample':'zero battery command is legal but leaves SOC below the +0.05 target'},
'd1_ev2gym_fault': {'steps':3,'target':{'path':'vehicle_soc','min_delta_from_initial':0.01,'range':[0,1]},'constraints':[{'path':'household_load_kw','max':8.0}],'schema_revision':'public_obs_v2: household_load_kw is exposed; overload boolean is not','legal_counterexample':'zero charge is legal but leaves vehicle SOC unchanged'},
'd1_discrete_device_fault': {'steps':4,'target':{'path':'devices.laundry.washer.state','one_of':['washing','running','active','started','in_progress']},'constraints':[{'path':'active_rule_ids','type':'list'}],'state_mapping_version':'washer_public_state_v1: washing is the native in-progress state','legal_counterexample':'empty command is legal but washer remains idle'},
'energyplus_iaq': {'steps':3,'target':{'path':'co2_ppm','max':800.0},'constraints':[{'path':'relative_humidity_pct','range':[0,100]}],'legal_counterexample':'zero ventilation is legal but does not meet the 800 ppm target'},
'wntr_residential_water': {'steps':3,'target':{'path':'tank_level_m','range':[5.5,6.5]},'constraints':[{'path':'pressure_bathroom_m','min':0.0}],'legal_counterexample':'open-valve control is legal but drives the tank below the safe band'},
'fds_smoke_fire': {'steps':4,'target':{'path':'room_b_visibility_m','min':25.0},'constraints':[{'path':'room_b_temperature_c','max':80.0}],'legal_counterexample':'door fraction zero is legal but fails the specified visibility target'},
'modelica_buildings_aixlib': {'steps':3,'target':{'path':'heater_heat_flow_w','min':1.0},'constraints':[{'path':'room_a_temperature_c','range':[15,35]}],'legal_counterexample':'zero heater request is legal but produces no heater heat flow'},
'd3_citylearn_multi_system': {'steps':3,'target':{'path':'electrical_storage_soc','min_delta_from_initial':0.05,'range':[0,1]},'constraints':[{'path':'net_electricity_consumption','finite':True}],'capability':'Episode generation only; not D3 coupling','legal_counterexample':'zero battery request is legal but fails the SOC increase'},
'd3_citylearn_multibuilding_competition': {'steps':3,'target':{'path':'district_net_kwh','max':8.0},'constraints':[{'path':'shared_meter_headroom_kwh','min':0.0}],'capability':'district aggregation/feasibility, not native cross-building physical feedback','legal_counterexample':'rate 1.0 is legal but produces negative shared-meter headroom'},
'd3_wntr_water_competition': {'steps':3,'target':{'path':'served_shower_m3_s','min':1e-9},'constraints':[{'path':'pressure_shower_m','min':0.0},{'path':'tank_level_m','min':0.0}],'legal_counterexample':'closed shower is legal but serves no shower flow'},
'd3_modelica_shared_heat': {'steps':3,'target':{'all_of':[{'path':'allocated_space_heat_w','min':1.0},{'path':'allocated_dhw_heat_w','min':1.0}]},'constraints':[{'path':'service_shortfall_w','max':2400.0}],'legal_counterexample':'zero requests are legal but allocate neither service'},
'd3_ev2gym_electric_competition': {'prefix_steps':26,'seed':3,'steps':2,'target':{'all_of':[{'path':'ports.0.soc','min_delta_from_prefix':0.001},{'path':'ports.1.soc','min_delta_from_prefix':0.001}]},'constraints':[{'path':'transformer.capacity_kw','equals':15.0},{'path':'transformer.overloaded','equals':False}],'precondition':'both native ports connected at seed 3 prefix step 26','legal_counterexample':'both rates zero are legal but neither connected vehicle SOC increases'},
'd3_energyplus_shared_ventilation': {'steps':3,'target':{'path':'zone_a_actual_airflow_m3_s','min':1e-6},'constraints':[{'path':'zone_b_actual_airflow_m3_s','max':1.0},{'path':'zone_a_co2_ppm','finite':True}],'legal_counterexample':'both airflow requests zero are legal but zone A airflow target fails'},
}

# Every trajectory clause explicitly declares which captured frames are in
# scope.  The default is pilot trace only; pre-action frames are included only
# when a contract says so, and delta operators still require explicit anchors.
for _clause in CLAUSES.values():
    _clause.setdefault('constraint_scope', {
        'include_initial': False,
        'include_prefix': False,
        'include_trace': True,
    })
ALLOWED_OPERATORS={'equals','one_of','min','max','range','finite','min_delta_from_initial','max_delta_from_initial','min_delta_from_prefix','all_of','type'}

def render_query(route, c):
 q={
 'd0_exogenous_context':'Turn the interior lights OFF at the first decision and keep the front door CLOSED for three 60-second intervals (180 seconds total).',
 'd1_sustaingym_fault':'For three 300-second control intervals, keep the first public zone temperature within 0.5 C of its initial value and within [0,50] C.',
 'd1_citylearn_battery_fault':'Over three hourly decisions, charge the battery until SOC increases by at least 0.05, without exceeding SOC 1.0; non-shiftable load must remain finite.',
 'd1_ev2gym_fault':'Over three 900-second charging decisions, increase the connected vehicle SOC by at least 0.01 while public household load stays at or below 8 kW.',
 'd1_discrete_device_fault':'Within four 60-second decisions, start the washer and reach a running or active state; keep the public rule list well formed.',
 'energyplus_iaq':'Over three 600-second decisions, achieve CO2 at or below 800 ppm while relative humidity remains in [0,100] percent.',
 'wntr_residential_water':'Over three hourly hydraulic decisions, keep tank level in [5.5,6.5] m and bathroom pressure nonnegative; open-valve control is the legal targeted violation.',
 'fds_smoke_fire':'Replay four 1-second FDS decisions so room B visibility reaches at least 25 m while temperature stays below 80 C; this is historical replay.',
 'modelica_buildings_aixlib':'Over three 60-second FMI decisions, request useful heating so heater heat flow reaches at least 1 W while room A temperature stays in [15,35] C.',
 'd3_citylearn_multi_system':'Over three hourly Episode decisions, increase storage SOC by at least 0.05 without claiming native D3 cross-channel coupling.',
 'd3_citylearn_multibuilding_competition':'Over three hourly decisions, keep district net electricity at or below 8 kWh and shared-meter headroom nonnegative; this is aggregation, not native cross-building feedback.',
 'd3_wntr_water_competition':'Over three hourly hydraulic decisions, serve positive shower flow while shower pressure and tank level remain nonnegative.',
 'd3_modelica_shared_heat':'Over three 60-second decisions, allocate at least 1 W to both space heating and DHW, with service shortfall at most 2400 W.',
 'd3_ev2gym_electric_competition':'At seed 3, use the documented 26-step public connected prefix, then over two 900-second decisions increase both connected port SOC values by at least 0.001 without transformer overload.',
 'd3_energyplus_shared_ventilation':'Over three 900-second decisions, produce positive zone A airflow, keep zone B airflow at or below 1 m3/s, and keep zone A CO2 finite.'}
 return q[route]

def _get_path(obj, path):
 parts=path.split('.'); i=0
 while i<len(parts):
  if isinstance(obj,dict) and parts[i] not in obj:
   # Public device/building identifiers may themselves contain dots.
   found=None; candidates=[]
   for j in range(len(parts),i,-1):
    key='.'.join(parts[i:j])
    if key in obj: candidates.append((key,j))
   if len(candidates)>1: raise ValueError('ambiguous dotted public path:'+path)
   if candidates: found=candidates[0]
   if found is None: raise KeyError(path)
   obj=obj[found[0]]; i=found[1]; continue
  part=parts[i]
  if isinstance(obj, dict): obj=obj[part]
  elif isinstance(obj, list): obj=obj[int(part)]
  else: raise KeyError(path)
  i+=1
 return obj

def _paths(node):
 if isinstance(node,dict):
  if 'path' in node: yield node['path']
  for v in node.values(): yield from _paths(v)
 elif isinstance(node,list):
  for v in node: yield from _paths(v)

def _find_public_path(obj, path):
 try: return _get_path(obj,path)
 except Exception: pass
 if isinstance(obj,dict):
  for v in obj.values():
   try: return _find_public_path(v,path)
   except KeyError: pass
 elif isinstance(obj,list):
  for v in obj:
   try: return _find_public_path(v,path)
   except KeyError: pass
 raise KeyError(path)

def validate_clause_tree(node):
 if isinstance(node,dict):
  if 'constraint_scope' in node:
   scope=node['constraint_scope']
   if not isinstance(scope,dict) or set(scope)-{'include_initial','include_prefix','include_trace'} or not all(isinstance(v,bool) for v in scope.values()) or not any(scope.values()):
    raise ValueError('invalid constraint_scope')
   node={k:v for k,v in node.items() if k!='constraint_scope'}
  for k,v in node.items():
   if k in {'path','equals','one_of','min','max','range','finite','type','min_delta_from_initial','max_delta_from_initial','min_delta_from_prefix'}: continue
   if k not in {'all_of','target','constraints','constraint_scope','capability','legal_counterexample','precondition','steps','prefix_steps','seed','state_mapping_version','schema_revision'}: raise ValueError('unknown clause field:'+k)
   validate_clause_tree(v)
 elif isinstance(node,list):
  for v in node: validate_clause_tree(v)

def main():
 import argparse
 ap=argparse.ArgumentParser(); ap.add_argument('--contracts-only',action='store_true'); ap.add_argument('--aggregate-only',action='store_true'); ap.add_argument('--routes',default='all'); ap.add_argument('--resume',action='store_true'); ap.add_argument('--output-dir',type=Path,default=ROOT/'generated/episode_pilot_v4_flash_offline_v2'); a=ap.parse_args()
 out=a.output_dir; out.mkdir(parents=True,exist_ok=True)
 contracts=[]; validation=[]
 selected=set(r.strip() for r in a.routes.split(',') if r.strip()) if a.routes!='all' else {x[0] for x in CONTRACTS}
 checkpoint=out/'offline_validation.checkpoint.json'
 for route,query,witness,baseline,violation,predicate in CONTRACTS:
  meta=route_metadata(route); clause=CLAUSES[route]
  c={'contract_id':'v4flash-'+route,'route_id':route,'query':render_query(route,clause),'seed':clause.get('seed',17),'initial_configuration':'native reset(seed) and documented public precondition; pre-action observation captured before witness','allowed_action_transport':{'outer_type':'object','field':'action','value_type':'route_native_scalar_or_list_or_object'},'witness_action':witness,'witness_plan': route=='d1_discrete_device_fault','baseline_action':baseline,'violation_action':violation,'violation_kind':'legal_targeted_counterexample','prefix_steps':clause.get('prefix_steps',0),'pilot_horizon_steps':clause['steps'],'native_horizon_seconds':meta.get('horizon_seconds'),'cadence_seconds':meta.get('cadence_seconds'),'task_clauses':clause,'terminal_and_trajectory_predicate':predicate,'tolerance':'clause-specific units/ranges in task_clauses; finite/action/time checks are validity only','termination':'all task clauses evaluated at declared endpoint or explicit failure/timeout; validity never counts as task success','requirement_rationale':'hand-authored measurable native public-state target; baseline and legal targeted violation are separate controls'}
  contracts.append(c); validate_clause_tree(clause)
 (out/'task_contracts.json').write_text(json.dumps({'schema':'v4.flash.pilot.contracts.v2','formal_benchmark':False,'contracts':contracts},indent=2,sort_keys=True)+'\n')
 (out/'task_contracts.md').write_text('# V4 Flash offline task contracts\n\n'+''.join(f"- **{c['route_id']}**: {c['query']} Horizon={c['pilot_horizon_steps']} step(s), cadence={c['cadence_seconds']}s. Predicate: {c['terminal_and_trajectory_predicate']}\n" for c in contracts))
 if a.contracts_only:
  (out/'offline_validation.json').write_text(json.dumps({'schema':'v4.flash.pilot.offline.validation.v2','validation':[],'all_contracts_frozen_before_native':True,'transport_roundtrip':all(json.loads(json.dumps({'action':c['witness_action']}))['action']==c['witness_action'] for c in contracts),'machine_clause_schema':True,'horizon_range_ok':all(2<=c['pilot_horizon_steps']<=6 for c in contracts),'native_validation_deferred':'awaiting root contract review'},indent=2,sort_keys=True)+'\n')
  print(json.dumps({'contracts':len(contracts),'native_validation':'deferred'})); return
 if a.aggregate_only:
  merged={}
  for rf in (out/'routes').glob('*.json') if (out/'routes').exists() else []:
   try:
    item=json.loads(rf.read_text()); rid=item.get('route_id')
    current=next((cc for cc in contracts if cc['route_id']==rid),None)
    if rid and current is not None and item.get('contract_sha256')==dg(current) and all(item.get('source_binding',{}).get(k)==v for k,v in current_source_binding().items()):
     merged[rid]=item['result']
   except Exception:
    continue
  validation=[merged[r] for r, *_ in CONTRACTS if r in merged]
  (out/'offline_validation.json').write_text(json.dumps({'schema':'v4.flash.pilot.offline.validation.v2','validation':validation,'all_contracts_frozen_before_native':True,'transport_roundtrip':all(json.loads(json.dumps({'action':c['witness_action']}))['action']==c['witness_action'] for c in contracts)},indent=2,sort_keys=True)+'\n')
  print(json.dumps({'contracts':len(contracts),'validation':len(validation),'aggregate_only':True})); return
 prior=[]
 if a.resume and checkpoint.exists():
  try: prior=json.loads(checkpoint.read_text()).get('validation',[])
  except Exception: prior=[]
 for route,query,witness,baseline,violation,predicate in CONTRACTS:
  if route not in selected: continue
  clause=CLAUSES[route]; c=next(x for x in contracts if x['route_id']==route)
  sample=ROOT/'generated/episode_campaign_round9_final'/f'{route}.json'; path_errors=[]
  if sample.exists():
   try:
    raw=json.loads(sample.read_text()); obs=raw.get('initial_observation',raw.get('observation',raw))
    for p in _paths(clause):
     try: _find_public_path(raw,p)
     except Exception: path_errors.append(p)
   except Exception as e: path_errors.append('sample:'+type(e).__name__)
  else: path_errors.append('missing_sample')
  def run(action):
   r=make_agent_backend(route); trace=[]; started=time.monotonic()
   def timeout(_sig,_frame): raise TimeoutError('native route step exceeded 120s')
   try:
    signal.signal(signal.SIGALRM,timeout); signal.alarm(120)
    obs=r.reset(seed=c['seed']); initial_obs=obs.get('observation',obs) if isinstance(obs,dict) else obs
    for _ in range(c['prefix_steps']): obs=r.step(baseline)
    prefix_obs=obs.get('observation',obs) if isinstance(obs,dict) else obs
    for i in range(c['pilot_horizon_steps']):
     step_action=action[i] if c.get('witness_plan') and isinstance(action,list) and i<len(action) else ({'kind':'act','commands':[]} if c.get('witness_plan') and isinstance(action,list) else action)
     if route=='d1_discrete_device_fault': print('PLAN_STEP',i,repr(step_action),flush=True)
     x=r.step(copy.deepcopy(step_action)); trace.append({'time_seconds':x.get('time_seconds'),'observation':x.get('observation'),'done':x.get('done')}); obs=x
    return {'ok':True,'seed':c['seed'],'elapsed_seconds':time.monotonic()-started,'initial_observation':initial_obs,'prefix_observation':prefix_obs,'trace':trace,'final':trace[-1] if trace else None}
   except Exception as e: return {'ok':False,'elapsed_seconds':time.monotonic()-started,'error':type(e).__name__+':'+str(e),'last_action':locals().get('step_action'),'trace':trace}
   finally:
    signal.alarm(0)
    try: r.close()
    except Exception: pass
  print('NATIVE',route,flush=True)
  try:
   w=run(witness); b=run(baseline); v=run(violation); rp=run(witness)
  except Exception as e:
   err=type(e).__name__+':'+str(e)
   w={'ok':False,'error':err,'trace':[]}; b={'ok':False,'error':err,'trace':[]}; v={'ok':False,'error':err,'trace':[]}; rp={'ok':False,'error':err,'trace':[]}
  ws=score_run(w,clause); bs=score_run(b,clause); vs=score_run(v,clause); rs=score_run(rp,clause)
  def complete_valid(run):
   if not run.get('ok') or run.get('seed') != c['seed'] or not isinstance(run.get('trace'),list) or len(run['trace']) != c['pilot_horizon_steps']:
    return False
   return all(isinstance(f,dict) and isinstance(f.get('observation'),dict) and isinstance(f.get('time_seconds'),(int,float)) and not isinstance(f.get('time_seconds'),bool) and math.isfinite(float(f['time_seconds'])) and float(f['time_seconds']) > 0 for f in run['trace'])
  def trace_digest(run):
   return dg([(f.get('time_seconds'),f.get('observation')) for f in run.get('trace',[])])
  legal=bool(complete_valid(w) and complete_valid(v) and trace_digest(w)!=trace_digest(v))
  replay=bool(complete_valid(w) and complete_valid(rp) and trace_digest(w)==trace_digest(rp))
  validation.append({'route_id':route,'witness':w,'baseline':b,'violation':v,'replay':rp,'witness_score':ws,'baseline_score':bs,'violation_score':vs,'replay_score':rs,'path_errors':sorted(set(path_errors)),'design_gate':bool(not path_errors and complete_valid(w) and complete_valid(v) and complete_valid(rp) and ws['pass'] and not vs['pass'] and replay),'replay_agrees':replay,'violation_contrast':legal,'legal_baseline_fails_target':bool(not bs['pass'])})
  route_file=out/'routes'/f'{route}.json'; route_file.parent.mkdir(exist_ok=True)
  evaluator_path=Path(__file__).with_name('v4_flash_pilot_evaluator.py'); source_binding={'driver':str(Path(__file__).resolve()),'driver_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),'evaluator':str(evaluator_path),'evaluator_sha256':hashlib.sha256(evaluator_path.read_bytes()).hexdigest(),'python':sys.executable,'runtime':meta.get('runtime_path',sys.executable)}
  tmp=route_file.with_suffix('.json.tmp'); tmp.write_text(json.dumps({'schema':'v4.flash.route.result.v1','route_id':route,'contract_id':c['contract_id'],'contract_sha256':dg(c),'source_binding':source_binding,'result':validation[-1]},indent=2,sort_keys=True)+'\n'); tmp.replace(route_file)
  checkpoint.write_text(json.dumps({'schema':'v4.flash.offline.checkpoint.v1','validation':prior+validation},indent=2,sort_keys=True)+'\n')
 merged={v['route_id']:v for v in validation}
 for rf in (out/'routes').glob('*.json') if (out/'routes').exists() else []:
  try:
   item=json.loads(rf.read_text()); rid=item.get('route_id')
   current=next((cc for cc in contracts if cc['route_id']==rid),None)
   if rid and rid not in merged and current is not None and item.get('contract_sha256')==dg(current) and all(item.get('source_binding',{}).get(k)==v for k,v in current_source_binding().items()): merged[rid]=item['result']
  except Exception: continue
 validation=list(merged.values())
 (out/'offline_validation.json').write_text(json.dumps({'schema':'v4.flash.pilot.offline.validation.v2','validation':validation,'all_contracts_frozen_before_native':True,'transport_roundtrip':all(json.loads(json.dumps({'action':c['witness_action']}))['action']==c['witness_action'] for c in contracts)},indent=2,sort_keys=True)+'\n')
 print(json.dumps({'contracts':len(contracts),'validation':sum(x['design_gate'] for x in validation),'replay':sum(x['replay_agrees'] for x in validation),'violation_contrast':sum(x['violation_contrast'] for x in validation)}))
if __name__=='__main__': main()
