"""Journal a bounded development batch against all formal route cards."""
import argparse
import json
import os
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.sources import SourceRecord
from query_construction.routing import routing_messages, parse_routing
from tools.run_backend_casebook_llm_pilot import ChatClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--journal', type=Path, nargs='+', required=True)
    parser.add_argument('--review-journal', type=Path,
                        help='When supplied, route only candidates with a completed clear review')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int, default=5)
    parser.add_argument('--model', default='deepseek-v4-flash-meituan')
    parser.add_argument('--base-url', default='https://aigc.sankuai.com/v1/openai/native')
    parser.add_argument('--max-tokens', type=int, default=4000)
    args = parser.parse_args()
    if not 1 <= args.limit <= 100:
        parser.error('limit must be 1..100')
    if not 1 <= args.max_tokens <= 8000:
        parser.error('max-tokens must be 1..8000')
    inventory = json.loads(args.inventory.read_text())
    dev = {sid for group in inventory['partition']['groups'] if group['split'] == 'development' for sid in group['record_ids']}
    sources = {r['source']['id']: SourceRecord(**r['source']) for r in inventory['records']}
    rows = [json.loads(line) for journal in args.journal
            for line in journal.read_text().splitlines()]
    candidates = [r for r in rows if r['type'] == 'validation' and r['proposal']['decision'] == 'candidate']
    if args.review_journal:
        review_rows = [json.loads(line) for line in args.review_journal.read_text().splitlines()]
        cleared = {r['source_id'] for r in review_rows
                   if r.get('type') == 'review' and r.get('model_review_clear') is True}
        candidates = [r for r in candidates if r['source_id'] in cleared]
    candidates = candidates[:args.limit]
    if not candidates or any(r['source_id'] not in dev for r in candidates):
        raise ValueError('nonempty development batch required')
    client = ChatClient(args.base_url, args.model, os.environ['AIGC_API_KEY'],
                        timeout=45, retries=0, max_tokens=args.max_tokens)
    with args.output.open('x') as stream:
        def emit(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
            stream.flush(); os.fsync(stream.fileno())
        for candidate in candidates:
            sid = candidate['source_id']
            packet = routing_messages(sources[sid], candidate['proposal'])
            emit({'type': 'request', 'source_id': sid, 'messages': packet,
                  'requested_model': args.model, 'base_url': args.base_url,
                  'max_tokens': args.max_tokens,
                  'source_journals': [str(journal) for journal in args.journal],
                  'review_journal': str(args.review_journal) if args.review_journal else None})
            try:
                receipt = client.complete(packet)
            except Exception as exc:
                emit({'type': 'transport_error', 'source_id': sid, 'error_type': type(exc).__name__, 'usage': None})
                return 1
            emit({'type': 'receipt', 'source_id': sid, **receipt})
            try:
                if receipt['finish_reason'] != 'stop':
                    raise ValueError('incomplete routing response')
                result = parse_routing(sid, receipt['content'])
                emit({'type': 'routing', 'source_id': sid, **result})
                print(sid, {status: sum(r['status'] == status for r in result['routes'])
                            for status in ('candidate', 'unsupported', 'uncertain')}, flush=True)
            except (ValueError, TypeError) as exc:
                emit({'type': 'routing_error', 'source_id': sid, 'reason': str(exc)})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
