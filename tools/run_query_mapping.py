"""Map an existing extracted proposal against a recorded real public interface."""
import argparse
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.mapping import mapping_messages, compile_mapping, parse_mapping_response
from query_construction.timing import audit_reactive_contract
from tools.run_backend_casebook_llm_pilot import ChatClient


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--journal', type=Path, required=True)
    p.add_argument('--source-id', required=True)
    p.add_argument('--snapshot', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--model', default='deepseek-v4-flash-meituan')
    p.add_argument('--base-url', default='https://aigc.sankuai.com/v1/openai/native')
    p.add_argument('--max-tokens', type=int, default=2200)
    args = p.parse_args()
    if not 1 <= args.max_tokens <= 8000: p.error('max-tokens must be 1..8000')
    rows = [json.loads(line) for line in args.journal.read_text().splitlines()]
    candidates = [r for r in rows if r['type'] == 'validation' and r['source_id'] == args.source_id]
    if len(candidates) != 1: raise ValueError('exactly one validated source proposal required')
    proposal = candidates[0]['proposal']
    snapshot = json.loads(args.snapshot.read_text())
    if snapshot['status'] != 'snapshot_ok': raise ValueError('successful native snapshot required')
    route = snapshot['route_id']; obs = snapshot['initial']['observation']
    packet = mapping_messages(proposal, route, obs, snapshot['legal_actions'])
    client = ChatClient(args.base_url, args.model, os.environ['AIGC_API_KEY'],
                        timeout=45, retries=0, max_tokens=args.max_tokens)
    with args.output.open('x') as stream:
        def emit(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False,
                                    default=lambda o: asdict(o) if is_dataclass(o) else str(o))+'\n')
            stream.flush(); os.fsync(stream.fileno())
        emit({'type': 'request', 'source_id': args.source_id, 'route_id': route, 'messages': packet,
              'source_journal': str(args.journal), 'snapshot': str(args.snapshot),
              'requested_model': args.model, 'base_url': args.base_url,
              'max_tokens': args.max_tokens})
        try:
            receipt = client.complete(packet)
        except Exception as exc:
            emit({'type': 'transport_error', 'error_type': type(exc).__name__, 'usage': None}); return 1
        emit({'type': 'receipt', **receipt})
        try:
            if receipt['finish_reason'] != 'stop': raise ValueError('incomplete mapping response')
            assignments, wrapper = parse_mapping_response(receipt['content'])
            result = compile_mapping(proposal, route, assignments, obs)
            if result['compiled']:
                result['reactive_timing_audit'] = audit_reactive_contract(result['contract']['public_contract'])
            emit({'type': 'mapping', 'parsed_wrapper': wrapper, **result})
            print(json.dumps({'compiled': result['compiled'], 'admitted': False,
                              'reason': result.get('reason')}), flush=True)
        except (ValueError, TypeError, KeyError) as exc:
            emit({'type': 'mapping_error', 'reason': str(exc)}); return 1
    return 0


if __name__ == '__main__': raise SystemExit(main())
