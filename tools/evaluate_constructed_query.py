"""Evaluate a compiled mapping on full recorded native samples, not LLM claims."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.contracts import compile_contract
from query_construction.scenario_checks import check_episode_identity
from query_construction.temporal import evaluate


def main():
    p = argparse.ArgumentParser()
    for name in ('mapping', 'snapshot', 'trajectory', 'output'):
        p.add_argument('--'+name, type=Path, required=True)
    args = p.parse_args()
    rows = [json.loads(line) for line in args.mapping.read_text().splitlines()]
    mappings = [r for r in rows if r['type'] == 'mapping' and r.get('compiled')]
    if len(mappings) != 1: raise ValueError('one compiled mapping required')
    public = mappings[0]['contract']['public_contract']
    snapshot = json.loads(args.snapshot.read_text()); trajectory = json.loads(args.trajectory.read_text())
    identity = check_episode_identity(snapshot, trajectory)
    if not identity['compatible'] or public['route_id'] != snapshot['route_id']:
        raise ValueError('mapping/snapshot/trajectory identity mismatch')
    # Recompile exact public conditions, not independently rewritten scoring code.
    unique = {}
    for entry in public['conditions']:
        c = entry['condition']
        if c['id'] in unique and unique[c['id']] != c: raise ValueError('conflicting public conditions')
        unique[c['id']] = c
    compiled = compile_contract(public['route_id'], list(unique.values()), snapshot['initial']['observation'])
    samples = [trajectory['initial']] + trajectory['transitions']
    result = evaluate(compiled['clauses'], samples, cadence_seconds=public['cadence_seconds'],
                      horizon_seconds=public['horizon_seconds'])
    report = {'evaluation': result, 'identity': identity, 'admitted': False,
              'scope': 'recorded trajectory under constructed public contract; not mechanism challenge or semantic certification',
              'inputs': {k: str(getattr(args, k)) for k in ('mapping', 'snapshot', 'trajectory')}}
    with args.output.open('x') as f: json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__': main()
