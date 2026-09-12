"""Compare route-independent CO2 primitives against direct checks on saved
native recordings. This is evaluator regression, not query/task admission.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from query_construction.temporal import Clause, Predicate, evaluate
from unified_compiler.route_registry import route_metadata


def check(path):
    report = json.loads(path.read_text())
    route = report['route_id']; meta = route_metadata(route)
    if route not in {'energyplus_iaq', 'd3_energyplus_shared_ventilation'}:
        raise ValueError('this regression checks only the existing IAQ CO2 subcontracts')
    samples = [dict(time_seconds=report['initial']['time_seconds'], observation=report['initial']['observation'])]
    samples += [dict(time_seconds=r['time_seconds'], observation=r['observation']) for r in report['transitions']]
    fields = ['co2_ppm'] if route == 'energyplus_iaq' else ['zone_a_co2_ppm','zone_b_co2_ppm']
    start = 600 if route == 'energyplus_iaq' else 5400
    clauses = [Clause(field,'invariant',Predicate((field,),'le',1200),start,meta['horizon_seconds']) for field in fields]
    verdict = evaluate(clauses,samples,cadence_seconds=meta['cadence_seconds'],horizon_seconds=meta['horizon_seconds'])
    direct = {field:[s['time_seconds'] for s in samples if start <= s['time_seconds'] <= meta['horizon_seconds'] and s['observation'][field] > 1200] for field in fields}
    observed = {c['clause_id']:[v['time_seconds'] for v in c['violations']] for c in verdict.get('clauses',[])}
    return {'source': str(path), 'route': route, 'native_samples': len(samples),
            'agreement': verdict['evaluated'] and observed == direct and verdict['task_success'] == (not any(direct.values())),
            'co2_subcontract_result': verdict['task_success'], 'violations': direct,
            'scope': 'existing disclosed CO2 thresholds only; not whole-task correctness or dataset admission'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    if args.output.exists(): raise SystemExit('choose a new evidence file')
    rows=[]
    for folder in ('autonomous_wait_delivery_native_v5','autonomous_wait_long_feedback_diagnostic_v2'):
        for route in ('energyplus_iaq','d3_energyplus_shared_ventilation'):
            path=ROOT/'generated'/folder/(route+'.json')
            if folder == 'autonomous_wait_long_feedback_diagnostic_v2' and route != 'energyplus_iaq': continue
            rows.append(check(path))
    result={'scope':'three existing recordings; no API or new native execution','rows':rows,
            'passed':all(r['agreement'] for r in rows),'dataset_admitted':False}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with args.output.open('x') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    for row in rows: print(row['route'],row['native_samples'],'agreement=',row['agreement'],'CO2 result=',row['co2_subcontract_result'])
    return 0 if result['passed'] else 1


if __name__ == '__main__': raise SystemExit(main())
