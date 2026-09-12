"""Revalidate saved provider text without another call or duplicate billing receipt."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.sources import SourceRecord
from query_construction.extraction import validate_proposal
from query_construction.semantic_checks import surface_review


def main():
    parser = argparse.ArgumentParser()
    for name in ('inventory', 'journal', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    args = parser.parse_args()
    inventory = json.loads(args.inventory.read_text())
    sources = {r['source']['id']: SourceRecord(**r['source']) for r in inventory['records']}
    rows = [json.loads(line) for line in args.journal.read_text().splitlines()]
    requests = {r['source_id']: r for r in rows if r['type'] == 'request'}
    seen = set()
    counts = {'validated': 0, 'rejected': 0}
    with args.output.open('x') as stream:
        def emit(row):
            stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+'\n')
        emit({'type': 'revalidation_provenance', 'original_journal': str(args.journal),
              'new_model_calls': 0, 'new_billed_tokens': 0,
              'usage_source': 'Original journal only; no copied provider receipts'})
        for number, row in enumerate(rows, 1):
            if row['type'] != 'receipt':
                continue
            sid = row['source_id']
            if sid in seen:
                raise ValueError('multiple receipts per source require explicit attempt selection')
            seen.add(sid)
            source = sources[sid]
            request = requests[sid]
            if json.loads(request['messages'][-1]['content'])['source_text'] != source.text:
                raise ValueError('source inventory differs from original request')
            emit({**request, 'execute': False, 'replayed_request': True})
            emit({'type': 'receipt_reference', 'source_id': sid,
                  'journal': str(args.journal), 'line': number})
            try:
                if row['finish_reason'] != 'stop':
                    raise ValueError('incomplete original response')
                result = validate_proposal(source, row['content'])
                emit({'type': 'validation', 'source_id': sid, **result})
                emit({'type': 'surface_review', 'source_id': sid,
                      **surface_review(source, result['proposal'])})
                counts['validated'] += 1
            except (ValueError, TypeError) as exc:
                emit({'type': 'validation_error', 'source_id': sid, 'reason': str(exc)})
                counts['rejected'] += 1
    print(json.dumps(counts))


if __name__ == '__main__':
    main()
