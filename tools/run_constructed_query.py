"""Run a mapped query with its public contract, preserving full native samples."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.execution import execute_contract
from tools.backend_acceptance_runner import interpreter_for
from tools.run_backend_casebook_llm_pilot import ChatClient, summarize_model_usage
from unified_compiler.inference_budget import InferenceBudget


def main():
    parser = argparse.ArgumentParser()
    for name in ('mapping', 'snapshot', 'output_dir'):
        parser.add_argument('--'+name.replace('_', '-'), type=Path, required=True)
    parser.add_argument('--example-action', required=True)
    parser.add_argument('--max-calls', type=int, default=30)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.mapping.read_text().splitlines()]
    requests = [r for r in rows if r['type'] == 'request']
    mappings = [r for r in rows if r['type'] == 'mapping' and r.get('compiled')]
    if len(requests) != 1 or len(mappings) != 1:
        raise ValueError('one successful mapping and its exact request required')
    packet = json.loads(requests[0]['messages'][-1]['content'])
    public = mappings[0]['contract']['public_contract']
    if packet['route_id'] != public['route_id']:
        raise ValueError('mapping route mismatch')
    if not args.worker:
        return subprocess.call([interpreter_for(public['route_id']), str(Path(__file__).resolve()),
                                *sys.argv[1:], '--worker'])
    snapshot = json.loads(args.snapshot.read_text())
    args.output_dir.mkdir(parents=True, exist_ok=False)
    client = ChatClient('https://aigc.sankuai.com/v1/openai/native', 'deepseek-v4-flash-meituan',
                        os.environ['AIGC_API_KEY'], timeout=60, retries=0)
    with (args.output_dir / 'events.jsonl').open('x') as stream:
        def emit(event):
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
            stream.flush(); os.fsync(stream.fileno())
        result = execute_contract(packet['query'], public, snapshot, client,
                                  InferenceBudget(max_calls=args.max_calls),
                                  example_action=json.loads(args.example_action), emit=emit)
    result['model_usage'] = summarize_model_usage([c for c in result['calls'] if c['attempted']])
    result['mapping_journal'] = str(args.mapping)
    with (args.output_dir / 'result.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(json.dumps({'status': result['status'], 'evaluation': result.get('contract_evaluation'),
                      'admitted': False}))
    return 0 if result['status'] == 'native_terminal_reached' else 1


if __name__ == '__main__':
    raise SystemExit(main())
