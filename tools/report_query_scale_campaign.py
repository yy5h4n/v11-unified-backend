"""Combine the two frozen source batches and non-source diagnostics."""
import argparse
import json
from pathlib import Path


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--batch1-report', type=Path, required=True)
    parser.add_argument('--batch2-inventory', type=Path, required=True)
    parser.add_argument('--batch2-extraction', type=Path, required=True)
    parser.add_argument('--batch2-review', type=Path, required=True)
    parser.add_argument('--variants', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    first = json.loads(args.batch1_report.read_text())
    inventory = json.loads(args.batch2_inventory.read_text())
    extraction = jsonl(args.batch2_extraction)
    reviews = jsonl(args.batch2_review)
    extracted = {row['source_id']: row for row in extraction
                 if row.get('type') in ('validation', 'validation_error')}
    clear = {row['source_id'] for row in reviews
             if row.get('type') == 'review' and row.get('model_review_clear') is True}
    review_error = {row['source_id'] for row in reviews if row.get('type') == 'review_error'}
    second_records = []
    for entry in inventory['records']:
        source = entry['source']; row = extracted[source['id']]
        record = {'source_id': source['id'], 'corpus': source['corpus'], 'admitted': False}
        if row['type'] == 'validation_error':
            record.update(query=None, decision='generator_failure', reason=row['reason'])
        elif row['proposal']['decision'] == 'reject':
            record.update(query=None, decision='rejected_at_extraction', reason=row['proposal']['reason'])
        elif source['id'] in clear:
            record.update(query=row['proposal']['query'], decision='review_clear_unrouted')
        elif source['id'] in review_error:
            record.update(query=row['proposal']['query'], decision='pending_model_review')
        else:
            record.update(query=row['proposal']['query'], decision='pending_model_review_not_run')
        second_records.append(record)
    all_records = first['records'] + second_records
    counts = {}
    for record in all_records:
        counts[record['decision']] = counts.get(record['decision'], 0) + 1
    receipts = [row for row in extraction + reviews if row.get('type') == 'receipt']
    second_usage = {key: sum((row.get('usage') or {}).get(key, 0) or 0 for row in receipts)
                    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    combined_usage = {key: first['usage'][key] + second_usage[key]
                      for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    combined_usage.update(receipt_calls=first['usage']['receipt_calls'] + len(receipts),
                          hard_cap_tokens=1000000,
                          known_remaining_tokens=1000000 - combined_usage['total_tokens'],
                          unknown_transport_events=first['usage']['unknown_transport_events'])
    variants = json.loads(args.variants.read_text())
    output = {
        'schema': 'query-scale-campaign.v1',
        'source_batches': 2, 'selected_source_records': len(all_records),
        'source_record_counts': counts, 'source_records': all_records,
        'structured_extraction_count': sum(r['decision'] != 'generator_failure' for r in all_records),
        'candidate_query_count': sum(r.get('query') is not None for r in all_records),
        'admitted_source_item_count': 0,
        'surface_variant_artifact': {
            'path': str(args.variants), 'count': variants['count'],
            'semantic_cluster_count': variants['semantic_cluster_count'],
            'independent_demand_count': variants['independent_demand_count'],
            'counts': variants['counts'],
        },
        'external_anchor_execution': first['external_anchor_execution'],
        'usage': combined_usage,
        'claim_boundary': [
            'The 100 source records are development diagnostics, not an admitted benchmark release.',
            'Generator/reviewer truncation is model-side and kept separate from source rejection or backend mismatch.',
            'Surface variants do not increase the number of independent demand observations.',
            'The successful native model trajectory belongs only to the pre-existing accepted V2 anchor item.',
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: output[k] for k in ('selected_source_records', 'structured_extraction_count',
                                              'candidate_query_count', 'admitted_source_item_count')},
                     sort_keys=True))
    print(json.dumps({'counts': counts, 'usage': combined_usage}, sort_keys=True))


if __name__ == '__main__':
    main()
