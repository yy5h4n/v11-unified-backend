"""Read-only recorded interaction audit. Never computes task success."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from unified_compiler.llm_conversation import canonical_json, validate_canonical_conversation, parse_assistant_action, apply_observation_delta
from unified_compiler.native_action_conversation import decode_native_action
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS, route_metadata
from unified_compiler.decision_wait import decode_decision, held_action, event_matches
from unified_compiler.public_receipt import public_native_result


def verify_batches(report, calls, trace, wake_states):
    """Reconstruct every microstate, not just matching wake snapshots."""
    route = report['route_id']
    metadata = route_metadata(route)
    decisions = report['decisions']
    if len(decisions) != len(calls) or len(wake_states) != len(calls)+1 or not calls:
        return False
    cursor, now, previous_action = 0, report['initial']['time_seconds'], None
    state = report['initial']['observation']
    for i, (call, decision) in enumerate(zip(calls, decisions)):
        document, wait, target = decode_decision(call['content'], cadence=metadata['cadence_seconds'], now=now, horizon=metadata['horizon_seconds'])
        message = report['final_messages'][2+2*i]
        if canonical_json(document) != canonical_json(decision['document']) or canonical_json(document) != canonical_json(parse_assistant_action(message)):
            return False
        payload = json.loads(report['final_messages'][3+2*i]['content'])['action_result']
        micros = payload['microsteps']
        end = cursor + len(micros)
        if not micros or end > len(trace) or decision['transition_start'] != cursor or decision['transition_end'] != end:
            return False
        for j, (micro, receipt) in enumerate(zip(micros, trace[cursor:end])):
            if set(micro) not in ({'action', 'action_result', 'observation_delta'},
                                  {'action', 'action_result', 'observation_source'}):
                return False
            expected = held_action(route, document['action'], previous_action, first=j == 0, cadence=metadata['cadence_seconds'])
            if micro.get('observation_source') == 'wake_observation':
                if j != len(micros)-1 or 'observation_delta' in micro:
                    return False
                state = wake_states[i+1]
            else:
                state = apply_observation_delta(state, micro['observation_delta'])
            if any(canonical_json(a) != canonical_json(b) for a, b in (
                (expected, receipt['action']), (micro['action'], receipt['action']),
                (state, receipt['observation']), (micro['action_result'], public_native_result(route, receipt)))):
                return False
            now = receipt['time_seconds']
            reason = 'terminal' if receipt['done'] else 'event' if event_matches(wait, state) else 'deadline' if now >= target else None
            if j < len(micros)-1 and reason is not None:
                return False
        if reason is None or payload['wake_reason'] != reason or decision['wake_reason'] != reason:
            return False
        if payload['time_seconds'] != now or decision['time_seconds'] != now or canonical_json(wake_states[i+1]) != canonical_json(state):
            return False
        if document['action'] is not None:
            previous_action = document['action']
        cursor = end
    return cursor == len(trace)


def verify(report):
    route = report['route_id']
    checks = {}
    checks['terminal_status'] = report.get('status') == 'native_terminal_reached'
    checks['real_provider_record'] = bool(report.get('model')) and 'script' not in report['model'].lower()
    checks['closed'] = report.get('backend_closed') is True
    adaptive = report.get('autonomous_wait') is True
    checks['unchanged_protocol'] = (report.get('history_mode') == ('initial_full_plus_lossless_decision_batches' if adaptive else 'initial_full_plus_lossless_deltas')
        and report.get('automatic_retries') == 0 and report.get('format_repair_attempts') == 0)
    calls = [c for c in report.get('calls', []) if c.get('attempted')]
    trace = report.get('transitions', [])
    states = validate_canonical_conversation(report['final_messages'],
        expected_query=report['query'], expected_initial_observation=report['initial']['observation'])
    if adaptive:
        checks['decision_batches_and_microtrajectory'] = verify_batches(report, calls, trace, states)
    else:
        checks['one_call_per_action'] = len(calls) == len(trace) > 0
        checks['actions_match_model'] = checks['one_call_per_action'] and all(
            canonical_json(decode_native_action(c['content'])) == canonical_json(r['action'])
            for c, r in zip(calls, trace))
        checks['reconstructed_states'] = canonical_json(states[1:]) == canonical_json([r['observation'] for r in trace])
        checks['history_actions'] = canonical_json([parse_assistant_action(m)['action']
            for m in report['final_messages'][2::2]]) == canonical_json([r['action'] for r in trace])
    previous = report['initial']['time_seconds']
    clock_ok = True
    for r in trace:
        clock_ok &= r['time_seconds'] > previous and abs(r['time_seconds'] - previous - r['delta_t_seconds']) < 1e-6
        previous = r['time_seconds']
    checks['clock'] = clock_ok
    checks['full_horizon'] = bool(trace) and trace[-1]['done'] is True and abs(previous-route_metadata(route)['horizon_seconds']) < 1e-6
    checks['usage_available'] = bool(calls) and all(bool(c.get('usage')) for c in calls)
    return {'route_id': route, 'interaction_verified': all(checks.values()), 'checks': checks,
            'calls': len(calls), 'reported_total_tokens': report.get('model_usage', {}).get('total_tokens'),
            'autonomous_wait': adaptive, 'native_steps': len(trace),
            'task_success': None}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('directories', nargs='+', type=Path)
    args = p.parse_args()
    rows = []
    for directory in args.directories:
        for source in sorted(directory.glob('*.json')):
            try:
                row = verify(json.loads(source.read_text()))
            except Exception as exc:
                row = {'interaction_verified': False, 'error': f'{type(exc).__name__}: {exc}'}
            rows.append(dict(row, source=str(source)))
    covered = {r['route_id'] for r in rows if r['interaction_verified']}
    autonomous = {r['route_id'] for r in rows if r['interaction_verified'] and r.get('autonomous_wait')}
    print(json.dumps({'scope': 'recorded model/native interaction only; not task success or full backend certification',
        'rows': rows, 'verified_routes': sorted(covered), 'pending_routes': sorted(set(PUBLIC_ROUTE_IDS)-covered),
        'autonomous_verified_routes': sorted(autonomous),
        'autonomous_pending_routes': sorted(set(PUBLIC_ROUTE_IDS)-autonomous)}, indent=2))
    # Old fixed-step evidence must never satisfy the restored delivery gate.
    return 0 if autonomous == set(PUBLIC_ROUTE_IDS) else 1


if __name__ == '__main__':
    raise SystemExit(main())
