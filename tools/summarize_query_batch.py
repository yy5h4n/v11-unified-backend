"""Account for every extraction attempt without equating structure with admission."""
import argparse
from collections import Counter
import json
from pathlib import Path


def summarize(path):
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    requests = [r for r in rows if r['type'] == 'request']
    ids = [r['source_id'] for r in requests]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate source attempts; summarize each run separately')
    items = []
    usage = 0
    unknown = 0
    for sid in ids:
        events = [r for r in rows if r.get('source_id') == sid]
        receipts = [r for r in events if r['type'] == 'receipt']
        for receipt in receipts:
            total = receipt.get('usage', {}).get('total_tokens')
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                usage += total
            else:
                unknown += 1
        validation = [r for r in events if r['type'] == 'validation']
        errors = [r for r in events if r['type'] in ('validation_error', 'transport_error')]
        if validation:
            proposal = validation[-1]['proposal']
            state = 'candidate_needs_semantic_and_native_validation' if proposal['decision'] == 'candidate' else 'source_rejected_by_model'
            query, reason = proposal['query'], proposal['reason']
        elif errors:
            state, query, reason = errors[-1]['type'], None, errors[-1].get('reason', errors[-1].get('error_type'))
        else:
            state, query, reason = 'incomplete', None, 'No terminal validation event'
        unknown += sum(r['type'] == 'transport_error' for r in events)
        items.append({'source_id': sid, 'state': state, 'query': query, 'reason': reason,
                      'admitted': False})
    return {'source_journal': str(path), 'attempts': len(ids), 'items': items,
            'counts': dict(Counter(r['state'] for r in items)),
            'provider_known_total_tokens': usage, 'unknown_usage_events': unknown,
            'formal_admissions': 0, 'scope': 'Extraction-stage accounting only; not task success rate'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--journal', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.journal)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({k: v for k, v in result.items() if k != 'items'}, ensure_ascii=False))
