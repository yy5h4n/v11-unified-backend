"""Explicit semantic checks over native evidence; absence of checks is pending."""
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
from unified_compiler.llm_conversation import canonical_json
from unified_compiler.native_action_conversation import build_native_conversation, append_native_transition
from tools.probe_observation_sources import matches


def source_check(route):
    """Keep independent native-source evidence separate from algebraic checks."""
    for folder in ('observation_sources_connected_v2', 'observation_sources_fix_v1', 'observation_sources_v1'):
        source = ROOT/'generated'/folder/(route+'.json')
        if not source.exists():
            continue
        data = json.loads(source.read_text())
        evidence = data.get('evidence', [])
        passed = (data.get('route_id') == route and
                  data.get('status') == 'passed_scoped_source_check' and
                  len(evidence) >= 3 and len(evidence) == data.get('transitions_checked', 2) + 1 and all(
                      row.get('passed') is True and bool(row.get('native_values')) and
                      matches(row.get('public_observation'), row['native_values'])
                      for row in evidence))
        return {'status': 'passed_scoped_source_check' if passed else 'failed',
                'source': str(source.relative_to(ROOT)), 'scope': data.get('scope'),
                'points_checked': len(evidence)}
    return {'status': 'pending', 'scope': 'no native source report in this audit'}


def checks_for(route, receipt):
    o = receipt['observation']
    e = receipt.get('info', {}).get('effect', {})
    pairs = []
    if route == 'd1_citylearn_battery_fault':
        pairs = [('battery_soc', o['battery_soc'], e['battery_soc'])]
    elif route == 'd1_ev2gym_fault':
        # Only compare connected-vehicle state; departure can remove the EV.
        if o['vehicle_connected']:
            pairs = [('vehicle_soc', o['vehicle_soc'], e['vehicle_soc_after'])]
    elif route == 'd3_citylearn_multi_system':
        pairs = [(k, o[k], e[v]) for k, v in (
            ('electrical_storage_soc', 'battery_soc'),
            ('indoor_dry_bulb_temperature', 'indoor_temperature_c'),
            ('net_electricity_consumption', 'net_electricity_kwh'))]
    elif route == 'd3_citylearn_multibuilding_competition':
        nets = [v for k, v in o.items() if k.endswith('.net_electricity_consumption')]
        if not nets:
            raise ValueError('missing building meter observations')
        pairs = [('district_meter_sum', o['district_net_kwh'], sum(nets))]
    elif route == 'd3_ev2gym_electric_competition':
        t = o['transformer']
        pairs = [('transformer_headroom', t['remaining_capacity_kw'], t['capacity_kw']-t['power_kw']),
                 ('receipt_transformer_power', t['power_kw'], e['transformer_power_kw'])]
    elif route == 'd3_modelica_shared_heat':
        pairs = [('allocated_heat_sum', o['shared_heat_pump_capacity_used_w'], o['allocated_dhw_heat_w']+o['allocated_space_heat_w'])]
    elif route == 'wntr_residential_water':
        pairs = [('leak_total', o['leak_total_m3_s'], o['leak_bathroom_m3_s']+o['leak_kitchen_m3_s'])]
    return [{'check': name, 'left': a, 'right': b,
             'passed': math.isfinite(a) and math.isfinite(b) and math.isclose(a, b, rel_tol=1e-5, abs_tol=1e-6)}
            for name, a, b in pairs]


def prefix_check(route):
    source = ROOT/'generated/native_future_isolation_v1'/(route+'.json')
    if source.exists():
        from tools.probe_exogenous_and_dynamics import physical_outcome
        data = json.loads(source.read_text())
        runs = data.get('runs', [])
        valid = (data.get('route_id') == route and len(runs) == 2 and
                 all(len(r.get('trace', [])) == 7 and len(r.get('prefixes', [])) == 8 for r in runs))
        if valid:
            left, right = runs
            same_actions = canonical_json([r['action'] for r in left['trace']]) == canonical_json([r['action'] for r in right['trace']])
            equal = [canonical_json(a) == canonical_json(b) for a, b in zip(left['prefixes'], right['prefixes'])]
            differences = [i for i, (a, b) in enumerate(zip(left['trace'], right['trace']))
                           if canonical_json(physical_outcome(route, a)) != canonical_json(physical_outcome(route, b))]
            valid = same_actions and all(equal[:5]) and bool(differences)
        return {'status': 'passed' if valid else 'mismatch',
                'source': str(source.relative_to(ROOT)),
                'scope': 'native future intervention at step 5; equal initial and first four public prefixes; one seed only'}
    folder = 'exogenous_dynamics_mechanisms_fix_v1' if route == 'd1_sustaingym_fault' else 'exogenous_dynamics_mechanisms_v1'
    source = ROOT / 'generated' / folder / (route+'.json')
    if not (route.startswith('d0_') or route.startswith('d1_')) or not source.exists():
        return {'status': 'pending', 'scope': 'native counterfactual not tested here'}
    data = json.loads(source.read_text())
    schema = json.loads((ROOT/'generated/public_receipt_audit_v2'/(route+'.json')).read_text())['initial_schema']
    histories = []
    for run in data['runs']:
        c = build_native_conversation(route, query='Identical diagnostic responsibility placeholder.',
            initial_observation=run['initial']['observation'], legal_actions=schema,
            example_action=run['trace'][0]['action'])
        append_native_transition(c, run['trace'][0]['action'], run['trace'][0])
        histories.append(c.messages())
    return {'status': 'passed' if canonical_json(histories[0]) == canonical_json(histories[1]) else 'mismatch',
            'source': str(source.relative_to(ROOT)), 'scope': 'recorded native healthy/fault or context controls; initial plus first step only; not all future arrangements'}


def main():
    rows = []
    for route in PUBLIC_ROUTE_IDS:
        folder = ('battery_soc_repair_v1' if route == 'd1_citylearn_battery_fault' else
                  'citylearn_completed_state_v1' if route in {'d3_citylearn_multi_system', 'd3_citylearn_multibuilding_competition'} else
                  'backend_trust_horizon_fix_v1' if route == 'd1_sustaingym_fault' else 'backend_trust_horizon_v1')
        source = ROOT/'generated'/folder/(route+'.json')
        session_source = ROOT/'generated/native_session_path_v1'/(route+'.json')
        if session_source.exists():
            source = session_source
        try:
            data = json.loads(source.read_text())
            if source == session_source and data.get('conformance_passed') is not True:
                raise ValueError('native session path did not complete')
            trace = data.get('trace', data.get('transitions'))
            if not trace:
                raise ValueError('missing native trace')
            checks = [dict(check, step=i) for i, receipt in enumerate(trace) for check in checks_for(route, receipt)]
            status = 'pending_no_independent_semantic_check' if not checks else 'passed_scoped_checks' if all(c['passed'] for c in checks) else 'failed'
            rows.append({'route_id': route, 'status': status, 'checks': checks,
                         'source': str(source.relative_to(ROOT)), 'prefix_noninterference': prefix_check(route),
                         'native_source_check': source_check(route), 'foundation_ready': False})
        except Exception as exc:
            rows.append({'route_id': route, 'status': 'evidence_error', 'error': str(exc), 'foundation_ready': False})
    destination = ROOT/'generated/semantic_boundary_v1.json'
    destination.write_text(json.dumps({'scope': 'recorded native invariant and limited prefix audit; not LLM/task score', 'routes': rows}, indent=2)+'\n')
    for r in rows:
        print(r['route_id'], 'invariants='+r['status'],
              'native_source='+r.get('native_source_check', {}).get('status', 'unavailable'),
              'future_isolation='+r.get('prefix_noninterference', {}).get('status', 'unavailable'), r.get('error', ''))


if __name__ == '__main__':
    main()
