"""Review existing development outputs without regenerating them."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.sources import SourceRecord
from query_construction.review import review_messages, parse_review
from tools.run_backend_casebook_llm_pilot import ChatClient


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--inventory', type=Path, required=True)
    p.add_argument('--journal', type=Path, nargs='+', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--limit', type=int, default=3)
    p.add_argument('--model', default='deepseek-v4-flash-meituan')
    p.add_argument('--base-url', default='https://aigc.sankuai.com/v1/openai/native')
    p.add_argument('--max-tokens', type=int, default=1800)
    args = p.parse_args()
    if not 1 <= args.limit <= 100: p.error('limit must be 1..100')
    if not 1 <= args.max_tokens <= 8000: p.error('max-tokens must be 1..8000')
    inventory = json.loads(args.inventory.read_text())
    dev = {i for g in inventory['partition']['groups'] if g['split'] == 'development' for i in g['record_ids']}
    sources = {r['source']['id']: SourceRecord(**r['source']) for r in inventory['records']}
    rows = [json.loads(line) for journal in args.journal
            for line in journal.read_text().splitlines()]
    candidates = [r for r in rows if r['type'] == 'validation' and r['proposal']['decision'] == 'candidate'][:args.limit]
    if any(r['source_id'] not in dev for r in candidates): raise ValueError('development only')
    client = ChatClient(args.base_url, args.model, os.environ['AIGC_API_KEY'],
                        timeout=45, retries=0, max_tokens=args.max_tokens)
    with args.output.open('x') as stream:
        def emit(row):
            stream.write(json.dumps(row, ensure_ascii=False)+'\n'); stream.flush(); os.fsync(stream.fileno())
        for r in candidates:
            sid = r['source_id']; packet = review_messages(sources[sid], r['proposal'])
            emit({'type': 'request', 'source_id': sid, 'messages': packet,
                  'requested_model': args.model, 'base_url': args.base_url,
                  'max_tokens': args.max_tokens,
                  'source_journals': [str(journal) for journal in args.journal]})
            try:
                receipt = client.complete(packet)
            except Exception as exc:
                emit({'type': 'transport_error', 'error_type': type(exc).__name__, 'usage': None}); break
            emit({'type': 'receipt', 'source_id': sid, **receipt})
            try:
                if receipt['finish_reason'] != 'stop': raise ValueError('incomplete review')
                result = parse_review(sid, receipt['content'])
                emit({'type': 'review', 'source_id': sid, **result})
                print(sid, result['model_review_clear'], flush=True)
            except (ValueError, TypeError) as exc:
                emit({'type': 'review_error', 'source_id': sid, 'reason': str(exc)})


if __name__ == '__main__': main()
