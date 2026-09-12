"""Execute a constructed public contract using the trusted autonomous-wait loop."""
import json
from .contracts import compile_contract
from .temporal import evaluate
from .scenario_checks import check_episode_identity
from .timing import audit_reactive_contract
from unified_compiler.llm_backend_session import run_session


def execute_contract(query, public, snapshot, client, budget, *, example_action,
                     emit=lambda event: None):
    if not isinstance(query, str) or not query.strip():
        raise ValueError('natural query required')
    if snapshot.get('status') != 'snapshot_ok' or public['route_id'] != snapshot['route_id']:
        raise ValueError('successful matching snapshot required')
    conditions = {}
    for entry in public['conditions']:
        condition = entry['condition']
        if condition['id'] in conditions and conditions[condition['id']] != condition:
            raise ValueError('conflicting public conditions')
        conditions[condition['id']] = condition
    compiled = compile_contract(public['route_id'], list(conditions.values()), snapshot['initial']['observation'])
    if json.dumps(compiled['public_contract'], sort_keys=True) != json.dumps(public, sort_keys=True):
        raise ValueError('public contract differs from current compiler; regenerate explicitly')
    timing = audit_reactive_contract(public)
    if timing.get('reactive_deadline_rejected'):
        raise ValueError('reactive timing rejected; do not charge a model run for this contract')
    packet = json.dumps({'user_responsibility': query, 'public_evaluation_conditions': public},
                        ensure_ascii=False, allow_nan=False)
    identity = None
    def observe(event):
        nonlocal identity
        if event['type'] == 'session_started':
            identity = check_episode_identity(snapshot, {'route_id': public['route_id'],
                'seed': snapshot['seed'], 'initial': event['initial']})
            if not identity['compatible']:
                raise ValueError('native initial state differs from construction snapshot')
        emit(event)
    result = run_session(public['route_id'], packet, client, budget,
                         example_action=example_action, seed=snapshot['seed'], emit=observe)
    result['user_responsibility'] = query
    result['public_contract'] = public
    result['reactive_timing_audit'] = timing
    result['episode_identity'] = identity
    result['admitted'] = False
    result['construction_scope'] = 'Native execution and contract scoring only; source meaning and mechanism challenge remain separate'
    if 'initial' in result and identity is not None and identity['compatible']:
        result['contract_evaluation'] = evaluate(compiled['clauses'],
            [result['initial']] + result['transitions'],
            cadence_seconds=public['cadence_seconds'], horizon_seconds=public['horizon_seconds'])
    else:
        result['contract_evaluation'] = {'task_success': None,
            'reason': 'episode_identity_not_established', 'scored': False}
    return result
