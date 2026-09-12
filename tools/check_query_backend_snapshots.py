"""Check capability-card paths/types against all real initial public snapshots."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.binding import resolve
from query_construction.capabilities import CARDS
from query_construction.temporal import finite
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS


def check(directory):
    rows = []
    for route in PUBLIC_ROUTE_IDS:
        path = directory / (route+'.json')
        issues = []; count = 0
        try:
            snapshot = json.loads(path.read_text())
            if snapshot['route_id'] != route or snapshot['status'] != 'snapshot_ok':
                raise ValueError('snapshot identity/status mismatch')
            if not snapshot.get('legal_actions'):
                issues.append({'code': 'legal_actions_missing'})
            obs = snapshot['initial']['observation']
            for name, quantity in CARDS[route][1].items():
                for pattern in quantity.paths:
                    paths = resolve(pattern, obs)
                    if not paths:
                        issues.append({'code': 'missing_path', 'quantity': name, 'pattern': pattern})
                    for concrete in paths:
                        count += 1; value = obs
                        for key in concrete: value = value[key]
                        valid = (isinstance(value, str) if quantity.unit == 'category' else
                                 isinstance(value, bool) if quantity.unit == 'boolean' else finite(value))
                        if not valid:
                            issues.append({'code': 'value_type_mismatch', 'quantity': name,
                                           'path': concrete, 'actual_type': type(value).__name__})
                        elif quantity.unit == 'fraction' and not 0 <= value <= 1:
                            issues.append({'code': 'fraction_out_of_range', 'quantity': name, 'path': concrete})
                        elif quantity.unit == 'count' and (value < 0 or value != int(value)):
                            issues.append({'code': 'invalid_count', 'quantity': name, 'path': concrete})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            issues.append({'code': 'snapshot_unusable', 'reason': str(exc)})
        rows.append({'route_id': route, 'concrete_quantity_fields': count,
                     'compatible': not issues, 'issues': issues})
    return {'routes': rows, 'all_compatible': all(r['compatible'] for r in rows),
            'scope': 'initial path/type/range and action-schema presence; not unit calibration, control reachability or trajectory proof',
            'admitted': False}


def main():
    p = argparse.ArgumentParser(); p.add_argument('--directory', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True); args = p.parse_args()
    report = check(args.directory)
    with args.output.open('x') as f: json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['all_compatible'] else 1


if __name__ == '__main__': raise SystemExit(main())
