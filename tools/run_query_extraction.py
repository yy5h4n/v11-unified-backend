"""Bounded development extraction run; journal every attempt, never auto-retry."""
import argparse
import json
import os
import random
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.sources import SourceRecord
from query_construction.extraction import messages, validate_proposal
from query_construction.semantic_checks import surface_review
from tools.run_backend_casebook_llm_pilot import ChatClient


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--limit', type=int, default=3)
    p.add_argument('--model', default='deepseek-v4-flash-meituan')
    p.add_argument('--base-url', default='https://aigc.sankuai.com/v1/openai/native')
    p.add_argument('--max-tokens', type=int, default=1800)
    p.add_argument('--source-id', help='Explicit development record selection; retained in request journal')
    p.add_argument('--retry-journal', type=Path,
                   help='Select sources with an incomplete-response validation error in this prior journal')
    p.add_argument('--sample-seed', type=int, help='Reproducible random development sample instead of prefix')
    p.add_argument('--execute', action='store_true', help='Send selected public development text to configured provider')
    args = p.parse_args()
    if not 1 <= args.limit <= 100:
        p.error('limit must be between 1 and 100')
    if not 1 <= args.max_tokens <= 8000:
        p.error('max-tokens must be between 1 and 8000')
    inventory = json.loads(args.inventory.read_text())
    groups = inventory['partition']['groups']
    development = {i for g in groups if g['split'] == 'development' for i in g['record_ids']}
    records = [SourceRecord(**r['source']) for r in inventory['records']]
    selectors = sum(value is not None for value in (args.source_id, args.sample_seed, args.retry_journal))
    if selectors > 1:
        p.error('source-id, sample-seed, and retry-journal are mutually exclusive')
    if args.retry_journal:
        prior = [json.loads(line) for line in args.retry_journal.read_text().splitlines()]
        retry_ids = {r['source_id'] for r in prior if r.get('type') == 'validation_error'
                     and r.get('reason') == 'response did not finish normally'}
        selected = [r for r in records if r.id in retry_ids][:args.limit]
        if len(selected) != min(args.limit, len(retry_ids)):
            raise ValueError('retry journal references missing or duplicate inventory records')
    else:
        selected = ([r for r in records if r.id == args.source_id] if args.source_id
                    else [r for r in records if r.id in development][:args.limit])
    if args.sample_seed is not None:
        pool = sorted([r for r in records if r.id in development], key=lambda r: r.id)
        selected = random.Random(args.sample_seed).sample(pool, min(args.limit, len(pool)))
    if not selected or any(r.id not in development for r in selected):
        raise ValueError('this runner only processes explicit development records')
    client = (ChatClient(args.base_url, args.model, os.environ['AIGC_API_KEY'],
                        timeout=45, retries=0, max_tokens=args.max_tokens) if args.execute else None)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        def emit(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        for source in selected:
            packet = messages(source)
            emit({'type': 'request', 'source_id': source.id, 'messages': packet,
                  'execute': args.execute, 'split': 'development', 'admitted': False,
                  'selection_seed': args.sample_seed, 'inventory': str(args.inventory),
                  'retry_journal': str(args.retry_journal) if args.retry_journal else None,
                  'requested_model': args.model, 'base_url': args.base_url,
                  'max_tokens': args.max_tokens})
            if client is None:
                continue
            try:
                receipt = client.complete(packet)
            except Exception as exc:
                emit({'type': 'transport_error', 'source_id': source.id,
                      'error_type': type(exc).__name__,
                      'cause_type': type(exc.__cause__).__name__ if exc.__cause__ else None,
                      'http_status': getattr(exc.__cause__, 'code', None), 'usage': None})
                return 1  # No retry or speculative billing claim after uncertain transport.
            emit({'type': 'receipt', 'source_id': source.id, **receipt})
            try:
                if receipt['finish_reason'] != 'stop':
                    raise ValueError('response did not finish normally')
                result = validate_proposal(source, receipt['content'])
                emit({'type': 'validation', 'source_id': source.id, **result})
                emit({'type': 'surface_review', 'source_id': source.id,
                      **surface_review(source, result['proposal'])})
                print(source.id, 'structurally_valid_not_admitted', flush=True)
            except (ValueError, TypeError) as exc:
                emit({'type': 'validation_error', 'source_id': source.id, 'reason': str(exc)})
                print(source.id, 'validation_error', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
