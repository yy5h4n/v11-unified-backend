"""Actual native D0 task diagnostics; zero LLM/API calls, no private policy inputs."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend_casebook_v0.d0_contract import QUERY, PUBLIC_CONTRACT, evaluate
from unified_compiler.agent_interface import make_agent_backend


def run(policy):
    backend = make_agent_backend('d0_exogenous_context')
    try:
        initial = backend.reset(seed=0)['observation']
        state = initial; trace = []; once = False; delayed = False
        while not state['terminal']:
            action = {'kind': 'wait'}
            if state['context']['occupancy_count'] == 0:
                target = next((d for d, v in [('front_door', 'closed'), ('interior_lights', 'off')]
                               if state['devices'][d] != v), None)
                if policy == 'once' and not once:
                    target = 'front_door'; once = True
                elif policy == 'once':
                    target = None
                if policy == 'late' and target and not delayed:
                    target = None; delayed = True
                if policy == 'idle': target = None
                if target:
                    action = {'kind': 'act', 'command': {'target': target,
                              'operation': 'close' if target == 'front_door' else 'off'}}
                if policy == 'wrong_lights':
                    action = {'kind': 'act', 'command': {'target': 'interior_lights', 'operation': 'on'}}
            receipt = backend.step(action)
            trace.append(receipt); state = receipt['observation']
        return {'policy': policy, 'initial': initial, 'transitions': trace,
                'result': evaluate(initial, trace)}
    finally:
        backend.close()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists(): raise SystemExit('Use a new output path; prior evidence is retained.')
    expected = {'feedback': True, 'idle': False, 'once': False, 'late': False, 'wrong_lights': False}
    runs = [run(policy) for policy in expected]
    passed = all(r['result']['evaluated'] and r['result']['pass'] == expected[r['policy']] for r in runs)
    report = {'query': QUERY, 'public_contract': PUBLIC_CONTRACT, 'runs': runs,
              'diagnostic_passed': passed, 'external_api_calls': 0,
              'human_grounded': False, 'dataset_admitted': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as f: json.dump(report, f, ensure_ascii=False, indent=2)
    for r in runs: print(r['policy'], r['result'])
    return 0 if passed else 1


if __name__ == '__main__': raise SystemExit(main())
