"""Freeze a reproducible, development-only, topic-stratified source batch."""
import argparse
import hashlib
import json
import re
from pathlib import Path


CATEGORIES = (
    ('safety_security', r'\b(alarm|camera|security|lock|intrud|fire|smoke|garage|door|window|leak|flood|police)\w*'),
    ('climate_air', r'\b(heat|heating|temperature|cool|air condition|ac\b|fan\b|humidity|humid|weather|rain|blind|curtain)\w*'),
    ('care_health', r'\b(toilet|shower|medicine|medication|sleep|bed\b|elder|health|bathroom|baby|child|dog|cat|pet|feed|bowl)\w*'),
    ('appliance_media', r'\b(tv|television|music|projector|console|oven|coffee|washing|dishwasher|fridge|refrigerator|cook|vacuum|phone|mobile)\w*'),
    ('lighting_presence', r'\b(light|lamp|bright|dark|movement|motion|enter|leave|home|room|person|people|someone|no one|nobody)\w*'),
    ('notification_schedule', r'\b(send|message|warn|notify|calendar|time|hour|minute|morning|night|schedule|remind)\w*'),
)


def category(text):
    return next((name for name, pattern in CATEGORIES if re.search(pattern, text, re.I)), 'other')


def stable_order(record, seed):
    source = record['source']
    return hashlib.sha256(f"{seed}\0{source['id']}".encode()).hexdigest()


def development_records(path):
    inventory = json.loads(path.read_text())
    dev = {sid for group in inventory['partition']['groups']
           if group['split'] == 'development' for sid in group['record_ids']}
    # Text is considered only after the frozen partition says the ID is development.
    return inventory, [r for r in inventory['records'] if r['source']['id'] in dev]


def choose(records, quotas, seed):
    pools = {}
    for record in records:
        pools.setdefault(category(record['source']['text']), []).append(record)
    selected = []
    selected_ids = set()
    audit = {}
    shortfall = 0
    for name, count in quotas.items():
        pool = sorted(pools.get(name, []), key=lambda r: stable_order(r, seed))
        take = pool[:min(count, len(pool))]
        shortfall += count - len(take)
        selected.extend(take)
        selected_ids.update(r['source']['id'] for r in take)
        audit[name] = {'requested': count, 'available': len(pool),
                       'selected': [r['source']['id'] for r in take],
                       'shortfall': count - len(take)}
    if shortfall:
        remaining = sorted([record for record in records if record['source']['id'] not in selected_ids],
                           key=lambda r: stable_order(r, seed + ':quota-fill'))
        if len(remaining) < shortfall:
            raise ValueError(f'cannot fill total quota; shortfall {shortfall}, pool {len(remaining)}')
        fill = remaining[:shortfall]
        selected.extend(fill)
        audit['_quota_fill'] = [{'source_id': record['source']['id'],
                                 'stratum': category(record['source']['text'])}
                                for record in fill]
    return selected, audit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--crowdre', type=Path, required=True)
    parser.add_argument('--hiis', type=Path, required=True)
    parser.add_argument('--inventory-output', type=Path, required=True)
    parser.add_argument('--manifest-output', type=Path, required=True)
    parser.add_argument('--seed', default='query-scale-batch50-v1-20260911')
    parser.add_argument('--batch-id', default='query_scale_batch50_v1')
    parser.add_argument('--exclude-inventory', type=Path, nargs='*', default=[])
    args = parser.parse_args()

    crowdre_inventory, crowdre = development_records(args.crowdre)
    hiis_inventory, hiis = development_records(args.hiis)
    excluded = set()
    for path in args.exclude_inventory:
        prior = json.loads(path.read_text())
        excluded.update(record['source']['id'] for record in prior['records'])
    crowdre = [record for record in crowdre if record['source']['id'] not in excluded]
    hiis = [record for record in hiis if record['source']['id'] not in excluded]
    crowdre_quotas = {name: 3 for name in ('safety_security', 'climate_air', 'care_health',
                                           'appliance_media', 'lighting_presence')}
    hiis_quotas = {name: 5 for name in ('safety_security', 'climate_air', 'care_health',
                                        'appliance_media', 'lighting_presence',
                                        'notification_schedule', 'other')}
    selected_crowdre, crowdre_audit = choose(crowdre, crowdre_quotas, args.seed + ':crowdre')
    selected_hiis, hiis_audit = choose(hiis, hiis_quotas, args.seed + ':hiis')
    selected = selected_crowdre + selected_hiis
    if len(selected) != 50 or len({r['source']['id'] for r in selected}) != 50:
        raise AssertionError('batch must contain exactly 50 unique records')

    inventory = {
        'batch_id': args.batch_id,
        'purpose': 'development-scale diagnostic; not an independent benchmark release',
        'seed': args.seed,
        'sources': [str(args.crowdre), str(args.hiis)],
        'records': selected,
        'partition': {'groups': [
            {'record_ids': [r['source']['id']], 'split': 'development', 'previously_seen': True}
            for r in selected
        ]},
        'dataset_admitted': False,
    }
    manifest = {
        'batch_id': inventory['batch_id'],
        'frozen_before_generation': True,
        'seed': args.seed,
        'count': len(selected),
        'corpus_counts': {'CrowdRE': len(selected_crowdre), 'HIIS': len(selected_hiis)},
        'quota_and_selection': {'CrowdRE': crowdre_audit, 'HIIS': hiis_audit},
        'constraints': [
            'Only records already assigned to development are eligible.',
            'CrowdRE holdout source text is not selected or sent to the model.',
            'Topic labels are heuristic sampling strata, not semantic ground truth.',
            'HIIS is development-only because of historical exposure and has unresolved redistribution status.',
            'Selection does not imply extraction, backend support, feasibility, evaluator validity, or admission.',
        ],
        'source_inventory_digests': {
            'CrowdRE': hashlib.sha256(args.crowdre.read_bytes()).hexdigest(),
            'HIIS': hashlib.sha256(args.hiis.read_bytes()).hexdigest(),
        },
        'excluded_prior_inventories': [
            {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
            for path in args.exclude_inventory
        ],
        'source_metadata': {
            'CrowdRE': {'corpus': crowdre_inventory.get('corpus'), 'license': crowdre_inventory.get('license')},
            'HIIS': {'corpus': hiis_inventory.get('corpus'),
                     'release_status': hiis_inventory.get('release_status')},
        },
    }
    args.inventory_output.parent.mkdir(parents=True, exist_ok=True)
    args.inventory_output.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n')
    args.manifest_output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'count': len(selected), 'corpora': manifest['corpus_counts']}, sort_keys=True))


if __name__ == '__main__':
    main()
