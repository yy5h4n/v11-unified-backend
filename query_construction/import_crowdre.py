"""Read CrowdRE without displaying requirement text; freeze team-aware splits.

Only the requirement table is imported. Demographic/personality/completion data
are never included. Malformed rows remain quarantined with their raw columns.
"""
import argparse
import csv
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import zipfile

from .sources import SourceRecord, partition

HEADER = ['id', 'rid', 'uid', 'gid', 'context', 'stimuli', 'response',
          'created_at', 'published', '', '']


def import_archive(path):
    with zipfile.ZipFile(path) as archive:
        rows = list(csv.reader(io.StringIO(archive.read('all_requirements.csv').decode('utf-8-sig'))))
    if rows[0] != HEADER:
        raise ValueError('unrecognized requirement schema')
    records, rejected, teams, seen, grouping_records = [], [], {}, set(), []
    for number, row in enumerate(rows[1:], 1):
        if len(row) != len(HEADER) or any(row[9:]):
            rejected.append({'record_number': number, 'reason': 'column_count_or_nonempty_unnamed_column', 'raw_columns': row})
            continue
        identifier, rid, uid, gid, context, stimuli, response, created, published, _, _ = row
        if not all(x.strip() for x in (identifier, uid, gid, response)):
            rejected.append({'record_number': number, 'reason': 'missing_identity_or_response', 'raw_columns': row})
            continue
        if identifier in seen:
            raise ValueError('duplicate source ID; inventory not frozen')
        seen.add(identifier)
        source = SourceRecord('crowdre:'+identifier, 'CrowdRE-3550721-v1.0.0',
                              f'https://doi.org/10.5281/zenodo.3550721#all_requirements.csv:id={identifier}',
                              response, uid, 'crowdsourced smart-home requirement user story; not verified deployment',
                              'CC-BY-4.0')
        grouping_records.append(source)
        # All three fields contain human requirement semantics, not merely metadata.
        # Labels are serialization delimiters, not human-authored wording.
        source = replace(source, text=f'Context: {context}\nStimuli: {stimuli}\nResponse: {response}')
        records.append({'source': asdict(source), 'record_number': number, 'team_id': gid,
                        'original_requirement_id': rid, 'elicitation_context': context,
                        'stimuli': stimuli, 'response_raw': response, 'published_raw': published,
                        'text_serialization': 'Verbatim context/stimuli/response with explicit field labels'})
        teams.setdefault(gid, []).append(source.id)
    # Response-only duplicates deliberately over-group instead of separating
    # records sharing an outcome. Preserves the first frozen split exactly.
    split = partition(grouping_records, development_ids=[],
                      salt='crowdre-v1-team-split-2026-09-10', related_groups=list(teams.values()))
    return {'corpus': 'CrowdRE', 'source_url': 'https://zenodo.org/records/3550721',
            'license': 'CC-BY-4.0', 'parsed_count': len(rows)-1, 'records': records,
            'quarantined': rejected, 'partition': split,
            'admitted': False, 'semantic_screening': 'not_performed',
            'limitations': ['Crowd elicitation, not evidence of real household deployment',
                           'Normalized exact duplication only; paraphrase leakage not certified',
                           'Duplicate grouping conservatively includes identical response fields',
                           'Original three semantic fields serialized with non-human field labels']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = import_archive(args.archive)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    counts = {s: sum(len(g['record_ids']) for g in result['partition']['groups'] if g['split'] == s)
              for s in ('development', 'holdout')}
    print(json.dumps({'parsed': result['parsed_count'], 'retained': len(result['records']),
                      'quarantined': len(result['quarantined']), 'split_counts': counts}))


if __name__ == '__main__':
    main()
