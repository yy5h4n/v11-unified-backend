"""Necessary timing checks from explicit action lower bounds, not planning proofs."""
from query_construction.temporal import finite


def audit_reactive_contract(public_contract):
    """Audit known discrete response lower bounds, retaining unknown coverage.

This rejects *reactive* feasibility, not anticipatory safety policies: keeping
lights off before departure can meet a zero-grace invariant. That distinction
must remain visible to the task's claim-admission stage.
"""
    rows = []
    for entry in public_contract['conditions']:
        condition = entry['condition']; clause = entry['compiled_clause']
        if clause['kind'] == 'response':
            deadline = clause['within_seconds']
        elif clause.get('active') is not None:
            deadline = clause.get('activation_grace_seconds', 0)
        else:
            rows.append({'condition_id': condition['id'], 'status': 'not_reactive_condition'})
            continue
        quantity = condition['goal']['quantity']
        if public_contract['route_id'] == 'd0_exogenous_context' and quantity in ('light.state', 'door.state'):
            check = check_response_budget(deadline_seconds=deadline, command_count=1,
                commands_per_interval=1, cadence_seconds=public_contract['cadence_seconds'],
                evidence='D0 one-device command per native interval; restoring a wrong device state needs at least one command')
            rows.append({'condition_id': condition['id'],
                         'status': 'necessary_bound_passed' if check['timing_compatible'] else 'reactive_deadline_incompatible',
                         'check': check})
        else:
            rows.append({'condition_id': condition['id'], 'status': 'action_lower_bound_unverified'})
    return {'conditions': rows, 'reactive_deadline_rejected': any(
        r['status'] == 'reactive_deadline_incompatible' for r in rows),
        'all_reactive_bounds_checked': all(r['status'] != 'action_lower_bound_unverified' for r in rows),
        'admitted': False, 'scope': 'individual lower bounds; simultaneous commands and physical settling remain unverified'}


def check_response_budget(*, deadline_seconds, command_count, commands_per_interval,
                          cadence_seconds, evidence):
    if not finite(deadline_seconds) or deadline_seconds < 0:
        raise ValueError('finite nonnegative response deadline required')
    if not finite(cadence_seconds) or cadence_seconds <= 0:
        raise ValueError('positive native interval required')
    for name, value, minimum in (('command_count', command_count, 0),
                                  ('commands_per_interval', commands_per_interval, 1)):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError('invalid '+name)
    if not isinstance(evidence, str) or not evidence.strip():
        raise ValueError('action-count lower bound requires explicit evidence')
    intervals = (command_count + commands_per_interval - 1) // commands_per_interval
    lower_bound = intervals * cadence_seconds
    return {'timing_compatible': deadline_seconds >= lower_bound,
            'minimum_seconds': lower_bound, 'deadline_seconds': deadline_seconds,
            'command_count_lower_bound': command_count,
            'commands_per_interval': commands_per_interval, 'evidence': evidence,
            'feasibility': 'not_established',
            'scope': 'necessary bound after event observation; excludes dynamics, failures, contention and unknown actuation delays',
            'admitted': False}
