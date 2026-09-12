"""Native development diagnostics for the constructed lighting contract.

Explicitly route-specific policies, not a universal reference-policy generator.
No user corpus is fabricated and no native observation is overwritten.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unified_compiler.agent_interface import make_agent_backend
from query_construction.contracts import compile_contract
from query_construction.temporal import evaluate


def main():
    p = argparse.ArgumentParser(); p.add_argument('--mapping', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); args = p.parse_args()
    records = [json.loads(line) for line in args.mapping.read_text().splitlines()]
    public = next(r['contract']['public_contract'] for r in records if r['type'] == 'mapping' and r['compiled'])
    if public['route_id'] != 'd0_exogenous_context': raise ValueError('D0 diagnostic only')
    conditions = {r['condition']['id']: r['condition'] for r in public['conditions']}
    results = []
    with args.output.open('x') as stream:
        for policy in ('idle', 'always_on', 'occupancy_feedback'):
            backend = make_agent_backend(public['route_id'])
            try:
                initial = backend.reset(seed=0); current = initial; samples = [initial]
                clauses = compile_contract(public['route_id'], list(conditions.values()), initial['observation'])['clauses']
                while not current['done'] and current['time_seconds'] < public['horizon_seconds']:
                    if policy == 'idle': action = {'kind': 'wait'}
                    else:
                        operation = ('off' if policy == 'occupancy_feedback' and
                                     current['observation']['context']['occupancy_count'] == 0 else 'on')
                        action = {'kind': 'act', 'command': {'target': 'interior_lights', 'operation': operation}}
                    current = backend.step(action, dt_seconds=public['cadence_seconds'])
                    samples.append(current)
                score = evaluate(clauses, samples, cadence_seconds=public['cadence_seconds'],
                                 horizon_seconds=public['horizon_seconds'])
                results.append({'policy': policy, 'samples': samples, 'evaluation': score})
                print(policy, score['task_success'], flush=True)
            finally: backend.close()
        json.dump({'route_id': public['route_id'], 'seed': 0, 'public_contract': public,
                   'runs': results, 'admitted': False,
                   'scope': 'native development counterpolicies; not source semantic certification'},
                  stream, ensure_ascii=False, indent=2)


if __name__ == '__main__': main()
