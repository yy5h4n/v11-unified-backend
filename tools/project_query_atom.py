"""Derive one explicit atomic candidate retaining its parent and siblings."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.projection import project_atoms


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--journal', type=Path, required=True)
    p.add_argument('--source-id', required=True)
    p.add_argument('--atom-index', type=int, required=True)
    p.add_argument('--justification', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    rows = [json.loads(line) for line in args.journal.read_text().splitlines()]
    matches = [r for r in rows if r['type'] == 'validation' and r['source_id'] == args.source_id]
    if len(matches) != 1: raise ValueError('one matching proposal required')
    parent = matches[0]['proposal']
    if not 0 <= args.atom_index < len(parent['atoms']): raise ValueError('invalid atom index')
    query = parent['atoms'][args.atom_index]['responsibility']
    result = project_atoms(parent, [args.atom_index], query=query, justification=args.justification)
    with args.output.open('x') as stream:
        stream.write(json.dumps({'type': 'validation', 'source_id': args.source_id,
                                 'parent_journal': str(args.journal), **result}, ensure_ascii=False)+'\n')


if __name__ == '__main__': main()
