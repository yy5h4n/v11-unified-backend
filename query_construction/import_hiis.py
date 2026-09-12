"""Import the pinned historical HIIS user-study table without promoting old data
to an unseen holdout. Source text is data, never executable instructions.
"""
import argparse
import csv
from dataclasses import asdict
import hashlib
import io
import json
import re
from pathlib import Path
from query_construction.sources import SourceRecord, partition

CORPUS = 'hiis_user_study_204'
COMMIT = 'eb2f3ee5cdde01f6163c16e9052ae92c3d3884ff'
EXPECTED_DIGEST = '62ccfb6e93ad0bdaf1f19499bfcfbf35a43322262ce25460840051729d9ae40b'
REPOSITORY = 'https://github.com/andrematt/trigger_action_rules'
SOURCE_PATH = 'user study dataset/rules_nl_en.csv'


def parse_table(raw):
    # Encoding is pinned from the prior source importer, not guessed or decoded
    # with replacement: otherwise temperature/typographic characters corrupt.
    reader = csv.DictReader(io.StringIO(raw.decode('cp1252')), delimiter=';')
    if reader.fieldnames != ['user', 'rule', 'nl']:
        raise ValueError('unexpected source columns; do not guess the text/participant fields')
    rows = []
    rejected = []
    for index, row in enumerate(reader, 1):
        locator = f'{REPOSITORY}/blob/{COMMIT}/user%20study%20dataset/rules_nl_en.csv#record={index}'
        try:
            if set(row) != {'user', 'rule', 'nl'} or any(v is None for v in row.values()):
                raise ValueError('malformed CSV record')
            if re.search(r'(?:^|\n)\s*\d+\s*;[^;\n]+;', row['nl']):
                raise ValueError('suspected embedded CSV record in text; attribution requires source repair')
            source = SourceRecord(f'{CORPUS}:{index}', CORPUS, locator, row['nl'], row['user'],
                                  'participant_authored_study_rule_english_release', 'unresolved')
            rows.append({'source': asdict(source), 'record_number': index, 'original_rule_name': row['rule']})
        except ValueError as exc:
            rejected.append({'record_number': index, 'locator': locator, 'reason': str(exc),
                             'raw_fields': row})
    return rows, rejected


def build(raw, legacy_families):
    digest = hashlib.sha256(raw).hexdigest()
    if digest != EXPECTED_DIGEST:
        raise ValueError('source differs from the pinned source snapshot; review a new source version explicitly')
    rows, rejected = parse_table(raw)
    if len(rows) + len(rejected) != 203:
        raise ValueError('pinned English CSV inventory differs from its observed 203 records')
    by_number = {r['record_number']: r for r in rows}
    reused = []
    for family in legacy_families['families']:
        old = family['source_record']; row = by_number.get(old['csv_record_number'])
        if row is None or row['source']['text'] != old['original_text'] or row['source']['participant'] != old['user_id']:
            raise ValueError(f'legacy source mismatch: {family["family_id"]}')
        reused.append(row['source']['id'])
    records = [SourceRecord(**r['source']) for r in rows]
    # Historical snapshots include AI coding batches beyond the seven pilot
    # families. Their full exposure is not known. Conservatively use this whole
    # historical corpus for development, not a newly invented clean holdout.
    split = partition(records, development_ids={r.id for r in records}, salt='query-construction-source-freeze-v1')
    return {'corpus': CORPUS, 'repository': REPOSITORY, 'commit': COMMIT, 'source_path': SOURCE_PATH,
            'source_digest': digest, 'encoding': 'cp1252', 'source_rows': len(rows)+len(rejected), 'accepted_source_rows': len(rows),
            'repository_declared_study_rule_count': 204,
            'count_discrepancy': 'Repository README says 204 study automations; pinned English CSV has 203 records. No row is invented to reconcile them.',
            'source_parse_rejections': rejected, 'known_old_pilot_records': reused, 'partition': split,
            'holdout_count': 0, 'release_status': 'license_unresolved_do_not_redistribute',
            'collection_claim': 'user-study authored rules, not verified household deployment or new human labels',
            'translation_note': 'English source release; no new claim that every English sentence is verbatim original-language participant text',
            'exposure_note': 'entire historical corpus is development due to prior AI coding artifacts; unseen validation requires additional source inventory',
            'records': rows, 'dataset_admitted': False}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--csv', type=Path, required=True)
    p.add_argument('--legacy-families', type=Path, required=True)
    p.add_argument('--output-dir', type=Path, required=True)
    args = p.parse_args()
    raw = args.csv.read_bytes()
    report = build(raw, json.loads(args.legacy_families.read_text()))
    args.output_dir.mkdir(parents=True, exist_ok=False)
    with (args.output_dir/'rules_nl_en.csv').open('xb') as f: f.write(raw)
    with (args.output_dir/'source_inventory.json').open('x') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({k: report[k] for k in ('corpus','source_rows','accepted_source_rows','holdout_count','release_status','exposure_note')}, ensure_ascii=False))


if __name__ == '__main__': main()
