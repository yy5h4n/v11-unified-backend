"""Join exact journal paths into an auditable per-source construction report.

This reports progress, not admission. Native trajectory validation and semantic
certification cannot be inferred from an extraction or model-review result.
"""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.sources import SourceRecord
from query_construction.semantic_checks import surface_review
from query_construction.native_evidence import summarize_native


def read(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def report(inventory_path, extraction_path, review_path=None, routing_path=None, mapping_paths=(), native_paths=(), diagnostic_paths=()):
    inventory = json.loads(inventory_path.read_text())
    sources = {r['source']['id']: SourceRecord(**r['source']) for r in inventory['records']}
    extraction = read(extraction_path)
    requests = [r for r in extraction if r['type'] == 'request']
    if len({r['source_id'] for r in requests}) != len(requests):
        raise ValueError('duplicate source attempts in extraction journal')
    reviews = read(review_path) if review_path else []
    routing = read(routing_path) if routing_path else []
    proposals = {r['source_id']: r['proposal'] for r in extraction if r['type'] == 'validation'}
    for stage in (reviews, routing):
        for request in (r for r in stage if r['type'] == 'request'):
            sid = request['source_id']
            supplied = json.loads(request['messages'][-1]['content'])
            if sid not in proposals or supplied['source_text'] != sources[sid].text or supplied['query'] != proposals[sid]['query'] or supplied['atoms'] != proposals[sid]['atoms']:
                raise ValueError('stage reviewed a different source or proposal')
    mappings = []
    for path in mapping_paths:
        events = read(path)
        metadata = [r for r in events if r['type'] == 'request']
        if len(metadata) != 1 or Path(metadata[0]['source_journal']).resolve() != extraction_path.resolve():
            raise ValueError('mapping belongs to a different extraction run')
        mappings.append((path, metadata[0], events))
    natives = [(path, json.loads(path.read_text())) for path in native_paths]
    diagnostics = [(path, json.loads(path.read_text())) for path in diagnostic_paths]
    used_native, used_diagnostics = set(), set()
    items = []
    for request in requests:
        sid = request['source_id']
        source = sources[sid]
        # A matching ID alone is insufficient if the input text has changed.
        supplied = json.loads(request['messages'][-1]['content'])
        if supplied['source_text'] != source.text:
            raise ValueError('inventory and extraction text differ')
        events = [r for r in extraction if r.get('source_id') == sid]
        validations = [r for r in events if r['type'] == 'validation']
        item = {'source_id': sid, 'source_locator': source.locator,
                'query': None, 'admitted': False, 'native_validation': 'not_reported',
                'extraction_errors': [r for r in events if r['type'].endswith('_error')],
                'review': [r for r in reviews if r.get('source_id') == sid and r['type'] in ('review', 'review_error')],
                'routing': [r for r in routing if r.get('source_id') == sid and r['type'] in ('routing', 'routing_error')],
                'mappings': []}
        if validations:
            proposal = validations[-1]['proposal']
            item['query'] = proposal['query']
            item['extraction_decision'] = proposal['decision']
            item['current_surface_check'] = surface_review(source, proposal)
            item['surface_check_note'] = 'Recomputed by current code; original journal is unchanged'
        for path, metadata, events in mappings:
            if metadata['source_id'] == sid:
                item['mappings'].append({'path': str(path), 'route_id': metadata['route_id'],
                                        'results': [r for r in events if r['type'] != 'request' and r['type'] != 'receipt']})
                for native_path, native in natives:
                    if Path(native['mapping_journal']).resolve() != path.resolve():
                        continue
                    successful = [r for r in events if r['type'] == 'mapping' and r.get('compiled')]
                    if len(successful) != 1:
                        raise ValueError('native evidence requires one compiled mapping')
                    public = successful[0]['contract']['public_contract']
                    matching = [(p, d) for p, d in diagnostics if d.get('public_contract') == public]
                    if len(matching) > 1:
                        raise ValueError('ambiguous diagnostic evidence')
                    summary = summarize_native(native, public, item['query'], matching[0][1] if matching else None)
                    item.setdefault('native_runs', []).append({'path': str(native_path), **summary})
                    item['native_validation'] = 'recorded_samples_recomputed'
                    used_native.add(native_path)
                    if matching: used_diagnostics.add(matching[0][0])
        items.append(item)
    if used_native != set(native_paths) or used_diagnostics != set(diagnostic_paths):
        raise ValueError('unmatched native or diagnostic evidence; refusing silent omission')
    return {'items': items, 'attempts': len(items), 'formal_admissions': 0,
            'inputs': {'inventory': str(inventory_path), 'extraction': str(extraction_path),
                       'review': str(review_path) if review_path else None,
                       'routing': str(routing_path) if routing_path else None},
            'scope': 'Per-source construction evidence; no inferred native or semantic pass'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    for name in ('inventory', 'extraction', 'output'):
        parser.add_argument('--'+name, type=Path, required=True)
    for name in ('review', 'routing'):
        parser.add_argument('--'+name, type=Path)
    parser.add_argument('--mapping', type=Path, action='append', default=[])
    parser.add_argument('--native', type=Path, action='append', default=[])
    parser.add_argument('--diagnostic', type=Path, action='append', default=[])
    args = parser.parse_args()
    result = report(args.inventory, args.extraction, args.review, args.routing, args.mapping, args.native, args.diagnostic)
    with args.output.open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'attempts': result['attempts'], 'formal_admissions': 0}))
