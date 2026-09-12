"""Fresh native counterfactuals: change only a future schedule, never agent input."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.agent_interface import make_agent_backend
from unified_compiler.llm_conversation import canonical_json
from unified_compiler.native_action_conversation import build_native_conversation, append_native_transition
from tools.probe_exogenous_and_dynamics import action_for, physical_outcome

ROUTES = ('d0_exogenous_context', 'd1_sustaingym_fault', 'd1_citylearn_battery_fault', 'd1_ev2gym_fault', 'd1_discrete_device_fault')


def schedule(route, active):
    if route == 'd0_exogenous_context':
        from unified_compiler.adapters.d0_exogenous_context import ExogenousContextSchedule, ExternalContextEvent
        return ExogenousContextSchedule([ExternalContextEvent(5, 'occupancy_change', {'status': 'away', 'count': 0})] if active else [])
    if route == 'd1_sustaingym_fault':
        from unified_compiler.adapters.d1_fault_mechanism import ActuatorFaultSchedule, ActuatorFaultWindow
        return ActuatorFaultSchedule([ActuatorFaultWindow(5, 7, 'failed')] if active else [])
    if route == 'd1_citylearn_battery_fault':
        from unified_compiler.adapters.citylearn_battery_fault import BatteryFaultSchedule, BatteryFaultWindow
        return BatteryFaultSchedule([BatteryFaultWindow(5, 7, 'unavailable')] if active else [])
    if route == 'd1_ev2gym_fault':
        from unified_compiler.adapters.ev2gym_fault import ChargerFaultSchedule, ChargerFaultWindow
        return ChargerFaultSchedule([ChargerFaultWindow(5, 7, 'outage')] if active else [])
    from unified_compiler.adapters.d1_discrete_device_fault import DiscreteFaultSchedule, DiscreteFaultWindow
    return DiscreteFaultSchedule([DiscreteFaultWindow('garage_door.main', 5, 7, 'jammed')] if active else [])


def probe(route):
    runs = []
    for active in (False, True):
        backend = make_agent_backend(route)
        try:
            # Diagnostic-only intervention before reset; this does NOT admit
            # a new public route or grant the model schedule mutation rights.
            if route == 'd1_sustaingym_fault':
                backend.route._episode = backend.route.open_episode()
                backend.route._episode.schedule = schedule(route, active)
            else:
                backend.route.schedule = schedule(route, active)
            initial = backend.reset(seed=0)
            legal = backend.legal_actions()
            example = action_for(route, legal, 0, 0)
            c = build_native_conversation(route, query='Fixed-input causal diagnostic.',
                initial_observation=initial['observation'], legal_actions=legal, example_action=example)
            trace = []
            prefixes = [c.messages()]
            for i in range(7):
                action = action_for(route, legal, i, 0)
                if route == 'd1_discrete_device_fault':
                    action['commands'][0]['operation'] = 'open' if i < 5 else 'close'
                receipt = backend.step(action)
                trace.append(receipt)
                append_native_transition(c, action, receipt)
                prefixes.append(c.messages())
            runs.append({'initial': initial, 'trace': trace, 'prefixes': prefixes})
        finally:
            backend.close()
    same_actions = canonical_json([r['action'] for r in runs[0]['trace']]) == canonical_json([r['action'] for r in runs[1]['trace']])
    prefix_equal = [canonical_json(a) == canonical_json(b) for a, b in zip(runs[0]['prefixes'], runs[1]['prefixes'])]
    differing_outcomes = [i for i, (a, b) in enumerate(zip(runs[0]['trace'], runs[1]['trace'])) if
                         canonical_json(physical_outcome(route, a)) != canonical_json(physical_outcome(route, b))]
    # Public boundary state may announce the fault once boundary 5 is reached.
    # Require all earlier prefixes, including schema/system, to be identical.
    verified = same_actions and all(prefix_equal[:5]) and bool(differing_outcomes)
    return {'route_id': route, 'status': 'passed_scoped_native_test' if verified else 'failed',
            'same_actions': same_actions, 'equal_public_prefixes_0_through_7': prefix_equal,
            'physical_outcome_differing_steps': differing_outcomes,
            'scope': 'one seed, future onset at step 5; not all future schedules or observed variables',
            'runs': runs}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--route', choices=ROUTES, required=True)
    p.add_argument('--output-dir', type=Path, default=ROOT/'generated/native_future_isolation_v1')
    args = p.parse_args()
    try:
        result = probe(args.route)
    except Exception as exc:
        result = {'route_id': args.route, 'status': 'runtime_error', 'error': f'{type(exc).__name__}: {exc}'}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir/(args.route+'.json')).write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'runs'}))
    raise SystemExit(0 if result['status'] == 'passed_scoped_native_test' else 1)
