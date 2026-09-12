"""Build a clustered query-variant artifact with inherited parent decisions."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.evidence_batch import load_and_validate


def journal_rows(paths):
    return [json.loads(line) for path in paths for line in path.read_text().splitlines()]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--targets', type=Path, required=True)
    parser.add_argument('--journal', type=Path, nargs='+', required=True)
    parser.add_argument('--semantic-audit', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    spec = json.loads(args.targets.read_text())
    audit = json.loads(args.semantic_audit.read_text())
    audit_status = {group['item_id']: group['status'] for group in audit['groups']}
    rows = journal_rows(args.journal)
    latest = {}
    for row in rows:
        if row.get('type') in ('validation', 'validation_error'):
            latest[row['item_id']] = row
    records = []
    for target in spec['targets']:
        batch, _ = load_and_validate(root / target['batch'])
        item = next(item for item in batch['items'] if item['id'] == target['item_id'])
        result = latest.get(item['id'])
        if not result or result['type'] != 'validation' or audit_status.get(item['id']) != 'passed':
            raise ValueError(f"no reviewed successful variants for {item['id']}")
        disposition = {
            'accepted_core': 'usable_surface_variant_of_accepted_core',
            'accepted_calibration': 'usable_surface_variant_of_accepted_calibration',
            'rejected': 'rejected_inherited_from_anchor',
            'pending': 'pending_inherited_from_anchor',
        }[item['decision']]
        for variant in result['variants']:
            records.append({'id': variant['id'], 'query': variant['query'],
                            'semantic_cluster_id': item['id'],
                            'anchor_query': item['query'], 'anchor_decision': item['decision'],
                            'classification': item['classification'],
                            'citations': item['citations'], 'change_log': variant['change_log'],
                            'semantic_audit': 'passed_AI_assistant_not_human_annotation',
                            'disposition': disposition, 'admitted_as_independent_need': False})
    receipts = [row for row in rows if row.get('type') == 'receipt']
    usage = {key: sum((row.get('usage') or {}).get(key, 0) or 0 for row in receipts)
             for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    counts = {}
    for record in records:
        counts[record['disposition']] = counts.get(record['disposition'], 0) + 1
    output = {'schema': 'evidence-query-surface-variants.v1', 'count': len(records),
              'independent_demand_count': 0, 'semantic_cluster_count': len(spec['targets']),
              'counts': counts, 'records': records, 'provider_usage': {'receipt_calls': len(receipts), **usage},
              'claim_boundary': spec['claim_boundary'] + ' ' + audit['claim_boundary']}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'count': len(records), 'clusters': len(spec['targets']),
                      'counts': counts, 'provider_usage': output['provider_usage']}, sort_keys=True))


if __name__ == '__main__':
    main()
