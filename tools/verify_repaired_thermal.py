#!/usr/bin/env python3
"""Native all-step thermal service and policy contrasts after model repair."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from unified_compiler.agent_interface import make_agent_backend


def rollout(route_id,policy):
    route=make_agent_backend(route_id);frames=[]
    try:
        obs=route.reset(seed=17)['observation']
        for i in range(60):
            mean=(obs['room_a_temperature_c']+obs['room_b_temperature_c'])/2
            if route_id=='modelica_buildings_aixlib':
                action={'idle':0.,'fixed':0.3,'once':0.5 if i==0 else 0.,
                        'feedback':min(1.,max(0.,0.4+0.25*(20.-mean)))}[policy]
            else:
                space=min(1.,max(0.,0.45+0.30*(20.-mean)))
                hot=min(1.,max(0.,0.5+0.25*(44.-obs['dhw_temperature_c'])))
                action=({'space_heating_request':space,'dhw_request':hot} if policy=='feedback' else
                        {'space_heating_request':0.5 if policy=='fixed' or (policy=='once' and i==0) else 0.,
                         'dhw_request':0.5 if policy=='fixed' or (policy=='once' and i==0) else 0.})
            rec=route.step(action);obs=rec['observation'];frames.append(rec)
            if rec['done']:break
    finally:route.close()
    temps=[f['observation'][key] for f in frames for key in ['room_a_temperature_c','room_b_temperature_c']]
    hot=[f['observation']['dhw_temperature_c'] for f in frames if 'dhw_temperature_c' in f['observation']]
    passed=bool(temps) and min(temps)>=18 and max(temps)<=24 and (not hot or min(hot)>=40)
    return {'passed':passed,'room_temperature_range_c':[min(temps),max(temps)],'min_hot_water_c':min(hot) if hot else None,'frames':frames}


def main():
    report={}
    for route in ['modelica_buildings_aixlib','d3_modelica_shared_heat']:
        report[route]={}
        for policy in ['idle','once','fixed','feedback']:
            result=rollout(route,policy);report[route][policy]=result
            print(route,policy,{k:v for k,v in result.items() if k!='frames'},flush=True)
    target=ROOT/'environment_repairs_v1/thermal_verification.json';target.write_text(json.dumps(report,indent=2)+'\n')


if __name__=='__main__':main()
