"""Join frozen scale-batch journals without converting model signals into admission."""
import argparse
import hashlib
import json
from pathlib import Path


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def usage(paths):
    receipts = []
    unknown = []
    for path in paths:
        for row in rows(path):
            if row.get('type') == 'receipt':
                receipts.append(row)
            elif row.get('type') == 'transport_error' and row.get('usage') is None:
                unknown.append({'path': str(path), 'source_id': row.get('source_id'),
                                'error_type': row.get('error_type'), 'cause_type': row.get('cause_type')})
    totals = {key: sum((row.get('usage') or {}).get(key, 0) or 0 for row in receipts)
              for key in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
    return {'receipt_calls': len(receipts), **totals, 'unknown_transport_events': unknown,
            'hard_cap_tokens': 1000000, 'known_remaining_tokens': 1000000 - totals['total_tokens']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--inventory', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--extraction', type=Path, nargs='+', required=True)
    parser.add_argument('--review', type=Path, required=True)
    parser.add_argument('--routing', type=Path, required=True)
    parser.add_argument('--extra-usage-journal', type=Path, nargs='*', default=[])
    parser.add_argument('--agent-result', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()

    inventory = json.loads(args.inventory.read_text())
    manifest = json.loads(args.manifest.read_text())
    extraction_rows = [row for path in args.extraction for row in rows(path)]
    review_rows = rows(args.review)
    routing_rows = rows(args.routing)
    latest_extraction = {}
    for row in extraction_rows:
        if row.get('type') in ('validation', 'validation_error'):
            latest_extraction[row['source_id']] = row
    reviews = {row['source_id']: row for row in review_rows if row.get('type') == 'review'}
    review_errors = {row['source_id']: row for row in review_rows if row.get('type') == 'review_error'}
    routes = {row['source_id']: row for row in routing_rows if row.get('type') == 'routing'}
    route_errors = {row['source_id']: row for row in routing_rows if row.get('type') == 'routing_error'}

    strata = {}
    for corpus, categories in manifest['quota_and_selection'].items():
        for name, selection in categories.items():
            strata.update({source_id: name for source_id in selection['selected']})
    records = []
    for entry in inventory['records']:
        source = entry['source']; source_id = source['id']; extracted = latest_extraction.get(source_id)
        record = {'source_id': source_id, 'corpus': source['corpus'],
                  'sampling_stratum': strata[source_id], 'admitted': False}
        if not extracted or extracted['type'] == 'validation_error':
            record.update(stage='extraction', status='model_output_truncated',
                          decision='generator_failure', query=None)
        else:
            record['query'] = extracted['proposal']['query']
            record['proposal'] = extracted['proposal']
            if source_id in review_errors:
                record.update(stage='semantic_review', status='model_output_truncated',
                              decision='pending_model_review')
            elif source_id not in reviews:
                record.update(stage='semantic_review', status='not_run', decision='pending_model_review')
            elif source_id in route_errors:
                record.update(stage='route_screen', status='model_output_truncated',
                              decision='pending_model_routing')
            elif source_id not in routes:
                record.update(stage='route_screen', status='not_run', decision='pending_backend_screen')
            else:
                candidates = [route['route_id'] for route in routes[source_id]['routes']
                              if route['status'] == 'candidate']
                if candidates:
                    record.update(stage='route_screen', status='candidate_routes',
                                  candidate_routes=candidates, decision='pending_real_interface_mapping')
                else:
                    record.update(stage='route_screen', status='all_formal_routes_unsupported',
                                  candidate_routes=[], decision='rejected_current_catalogue_screen')
        records.append(record)

    counts = {}
    for record in records:
        counts[record['decision']] = counts.get(record['decision'], 0) + 1
    agents = [json.loads(path.read_text()) for path in args.agent_result]
    completed_agents = [agent for agent in agents if agent.get('status') == 'native_terminal_reached']
    if not completed_agents:
        raise ValueError('at least one completed anchor agent run is required')
    agent = completed_agents[-1]
    paths = ([Path('generated/query_construction_v3/probe_deepseek_v4_flash_20260911.jsonl'),
              Path('generated/query_construction_v3/probe_deepseek_v4_flash_20260911_network.jsonl')]
             + args.extraction + [args.review, args.routing] + args.extra_usage_journal)
    ledger = usage(paths)
    ledger['agent_receipt_calls'] = sum(run['model_usage']['calls'] for run in agents)
    for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        ledger[key] += sum(run['model_usage'].get(key, 0) or 0 for run in agents)
    ledger['receipt_calls'] += ledger['agent_receipt_calls']
    ledger['known_remaining_tokens'] = ledger['hard_cap_tokens'] - ledger['total_tokens']
    output = {
        'schema': 'query-scale-diagnostic.v1', 'batch_id': manifest['batch_id'],
        'scope': 'development-scale diagnostic; no batch50 item is admitted',
        'counts': {'selected': len(records), **counts}, 'records': records,
        'usage': ledger,
        'external_anchor_execution': {
            'item_id': 'core-transfer-multiroom-comfort-01',
            'batch50_member': False, 'status': agent['status'],
            'task_success': agent['contract_evaluation']['task_success'],
            'attempted_calls': agent['attempted_calls'], 'native_steps': len(agent['transitions']),
            'trajectory_sha256': hashlib.sha256(args.agent_result[-1].read_bytes()).hexdigest(),
            'claim': 'exact-query model execution on an already accepted V2 core item; not evidence that batch50 candidates are admitted',
        },
        'limitations': [
            'The same model generated and semantically reviewed candidates; clear review is corroboration, not independent validation.',
            'Model output truncation is reported separately from data/backend rejection.',
            'Whole-catalogue routing is model screening, not real-interface feasibility certification.',
            'HIIS source redistribution status remains unresolved and all selected HIIS records are development-only.',
            'A single successful external anchor execution is model evidence for that item only.',
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({'counts': output['counts'], 'usage': ledger,
                      'anchor': output['external_anchor_execution']}, sort_keys=True))


if __name__ == '__main__':
    main()
