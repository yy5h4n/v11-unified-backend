"""Read-only, offline delivery evidence check; no model calls or simulator runs.

Exit zero certifies only the explicitly listed evidence checks, not task success,
all-state physics, portability of native installations, or a benchmark release.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.audit_real_sessions import verify
from tools.audit_semantic_boundary import checks_for, prefix_check, source_check
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS

SESSIONS = {
    'd0_exogenous_context': 'autonomous_wait_llm_pilot_v1',
    'd1_citylearn_battery_fault': 'autonomous_wait_llm_pilot_v1',
    'd3_energyplus_shared_ventilation': 'autonomous_wait_llm_pilot_v1',
    'd1_sustaingym_fault': 'autonomous_wait_long_feedback_diagnostic_v2',
    'energyplus_iaq': 'autonomous_wait_long_feedback_diagnostic_v2',
    'd3_ev2gym_electric_competition': 'autonomous_wait_dedup_v3',
    'd3_citylearn_multibuilding_competition': 'autonomous_wait_format_clarity_network_v4',
    'd1_discrete_device_fault': 'autonomous_wait_coverage_v2',
    'd1_ev2gym_fault': 'autonomous_wait_coverage_v2',
    'd3_citylearn_multi_system': 'autonomous_wait_coverage_v2',
    'd3_modelica_shared_heat': 'autonomous_wait_coverage_v2',
    'd3_wntr_water_competition': 'autonomous_wait_coverage_v2',
    'fds_smoke_fire': 'autonomous_wait_coverage_v2',
    'modelica_buildings_aixlib': 'autonomous_wait_coverage_v2',
    'wntr_residential_water': 'autonomous_wait_coverage_v2',
}
DIAGNOSTICS = {'d1_sustaingym_fault', 'energyplus_iaq'}


def check_route(route):
    row = {'route_id': route, 'passed': False, 'task_success': None}
    try:
        path = ROOT / 'generated' / SESSIONS[route] / (route + '.json')
        row['session_source'] = str(path.relative_to(ROOT))
        report = json.loads(path.read_text())
        if report.get('route_id') != route:
            raise ValueError('evidence belongs to another route')
        interaction = verify(report)
        invariants = [c for receipt in report['transitions'] for c in checks_for(route, receipt)]
        source = source_check(route)
        needs_prefix = route.startswith(('d0_', 'd1_'))
        prefix = prefix_check(route) if needs_prefix else {'status': 'not_checked_by_this_gate'}
        row.update(
            interaction=interaction, native_source=source, future_prefix=prefix,
            invariants={'count': len(invariants), 'failed': [c for c in invariants if not c['passed']],
                        'status': ('passed_scoped_checks' if all(c['passed'] for c in invariants)
                                   else 'failed') if invariants else 'not_checked'},
            request_kind='explicit_policy_diagnostic' if route in DIAGNOSTICS else 'natural_responsibility_request',
            calls=report['attempted_calls'], native_steps=len(report['transitions']),
            model_usage=report.get('model_usage'),
        )
        row['passed'] = bool(interaction['interaction_verified'] and interaction.get('autonomous_wait')
            and source['status'] == 'passed_scoped_source_check'
            and all(c['passed'] for c in invariants)
            and (not needs_prefix or (prefix['status'] == 'passed'
                 and prefix.get('source', '').startswith('generated/native_future_isolation_v1/'))))
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
    return row


def check_scripted_native(route, directory):
    """Recheck raw full-horizon evidence; never trust its conformance label alone."""
    row = {'route_id': route, 'passed': False, 'scope': 'scripted native execution; not LLM'}
    try:
        path = Path(directory) / (route + '.json')
        report = json.loads(path.read_text())
        if report.get('route_id') != route or report.get('model') != 'SCRIPTED_WAIT_CONFORMANCE_NOT_LLM':
            raise ValueError('wrong route or evidence kind')
        audit = verify(report)
        checks = audit['checks']
        # These are the only checks that concern real-provider identity/usage.
        # Do not drop an arbitrary failing check or silently accept new checks.
        required = {'terminal_status', 'closed', 'unchanged_protocol',
                    'decision_batches_and_microtrajectory', 'clock', 'full_horizon'}
        expected = required | {'real_provider_record', 'usage_available'}
        row.update(source=str(path), checks=checks, native_steps=audit['native_steps'])
        row['passed'] = bool(set(checks) == expected and all(checks[k] for k in required)
            and audit['autonomous_wait'] and report.get('external_api_calls') == 0
            and report.get('attempted_calls') == 1 and len(report.get('decisions', [])) == 1)
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
    return row


def build(native_directory=None):
    rows = [check_route(route) for route in PUBLIC_ROUTE_IDS]
    inventory_ok = set(SESSIONS) == set(PUBLIC_ROUTE_IDS)
    native_rows = ([check_scripted_native(route, native_directory) for route in PUBLIC_ROUTE_IDS]
                   if native_directory is not None else [])
    return {
        'scope': 'Recorded autonomous interaction, sampled native sources, available trajectory invariants, and five finite future-prefix counterfactuals; not a complete backend certification.',
        'passed': inventory_ok and all(row['passed'] for row in rows)
                  and (native_directory is None or all(row['passed'] for row in native_rows)),
        'scripted_native_runs': native_rows,
        'inventory_matches': inventory_ok,
        'formal_routes': len(PUBLIC_ROUTE_IDS), 'routes': rows,
        'benchmark_ready': False, 'task_success_rate': None,
        'limitations': [
            'Two long routes use explicit-policy diagnostics, not natural-task success evidence.',
            'SustainGym diagnostic completed interaction but selected the wrong conditional action.',
            'Native source checks are sampled fields/states; future isolation covers one finite prefix per D0/D1 route.',
            'This command reads evidence, not current simulator execution or all source-version compatibility.',
            'FDS is prefix replay; visibility effectiveness is unproven.',
            'CityLearn single-building multi-system has no verified cross-channel competition.',
            'EV shared transformer and CityLearn district budget are constraints, not physical allocation.',
            'WNTR service-flow proxies are not calibrated household satisfaction.',
            'BOPTEST is a candidate, not one of the 15 formal routes.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json', action='store_true', help='Print full report instead of concise rows.')
    parser.add_argument('--native-dir', type=Path, help='Also require all 15 scripted full-horizon native reports from this directory.')
    args = parser.parse_args()
    result = build(args.native_dir)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    else:
        for row in result['routes']:
            print(row['route_id'], 'PASS' if row['passed'] else 'FAIL', row.get('error', ''),
                  row.get('request_kind', ''), 'calls=', row.get('calls'), 'steps=', row.get('native_steps'))
        for row in result['scripted_native_runs']:
            print('NATIVE', row['route_id'], 'PASS' if row['passed'] else 'FAIL', row.get('error', ''))
        print('Recorded evidence only; task success rate is NOT computed.')
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
