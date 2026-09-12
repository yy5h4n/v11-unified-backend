#!/usr/bin/env python3
"""Execute one frozen evidence-query packet with the real model/backend loop."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from query_construction.execution import execute_contract
from tools.backend_acceptance_runner import interpreter_for
from tools.run_backend_casebook_llm_pilot import ChatClient, idle_action, summarize_model_usage
from unified_compiler.inference_budget import InferenceBudget


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--model', default='deepseek-v4-flash-meituan')
    parser.add_argument('--base-url', default='https://aigc.sankuai.com/v1/openai/native')
    parser.add_argument('--max-tokens', type=int, default=2000)
    parser.add_argument('--max-calls', type=int, default=10)
    parser.add_argument('--max-total-message-bytes', type=int, default=5000000)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.max_tokens <= 8000 or not 1 <= args.max_calls <= 30:
        parser.error('max-tokens must be 1..8000 and max-calls must be 1..30')
    packet = json.loads(args.packet.read_text())
    route = packet['route_id']
    if not args.worker:
        return subprocess.call([interpreter_for(route), str(Path(__file__).resolve()),
                                *sys.argv[1:], '--worker'])
    if packet.get('schema') != 'evidence-query-agent-packet.v1':
        raise ValueError('unsupported packet schema')
    args.output_dir.mkdir(parents=True, exist_ok=False)
    client = ChatClient(args.base_url, args.model, os.environ['AIGC_API_KEY'],
                        timeout=120, retries=0, max_tokens=args.max_tokens)
    budget = InferenceBudget(max_calls=args.max_calls, max_message_bytes=1000000,
                             max_total_message_bytes=args.max_total_message_bytes)
    events_path = args.output_dir / 'events.jsonl'
    with events_path.open('x') as stream:
        def emit(event):
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        emit({'type': 'run_configuration', 'packet': str(args.packet),
              'requested_model': args.model, 'base_url': args.base_url,
              'max_tokens': args.max_tokens, 'max_calls': args.max_calls,
              'max_total_message_bytes': args.max_total_message_bytes})
        result = execute_contract(packet['query'], packet['public_contract'], packet['snapshot'],
                                  client, budget,
                                  example_action=idle_action(route, packet['snapshot']['legal_actions']),
                                  emit=emit)
    result['model_usage'] = summarize_model_usage([call for call in result['calls'] if call['attempted']])
    result['source_packet'] = str(args.packet)
    (args.output_dir / 'result.json').write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'status': result['status'], 'attempted_calls': result['attempted_calls'],
                      'task_success': result.get('contract_evaluation', {}).get('task_success'),
                      'usage': result['model_usage']}, sort_keys=True))
    return 0 if result['status'] == 'native_terminal_reached' else 1


if __name__ == '__main__':
    raise SystemExit(main())
