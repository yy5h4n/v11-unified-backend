from copy import deepcopy
import pytest
from tools.audit_real_sessions import verify
from unified_compiler.native_action_conversation import build_native_conversation, append_native_transition, encode_native_action


@pytest.fixture
def record():
    # Synthetic unit fixture, independent of ignored generated files/API.
    route = 'd1_citylearn_battery_fault'
    initial = {'observation': {'battery_soc': 0.}, 'time_seconds': 0}
    conversation = build_native_conversation(route, query='test', initial_observation=initial['observation'],
        legal_actions={}, example_action=0.)
    trace = []
    for i in range(8):
        receipt = {'observation': {'battery_soc': 0.}, 'action': 0.,
            'time_seconds': (i+1)*3600, 'delta_t_seconds': 3600, 'done': i == 7, 'info': {}}
        append_native_transition(conversation, 0., receipt)
        trace.append(receipt)
    return {'route_id': route, 'query': 'test', 'model': 'UNIT_TEST_PROVIDER',
        'status': 'native_terminal_reached', 'backend_closed': True,
        'history_mode': 'initial_full_plus_lossless_deltas', 'automatic_retries': 0,
        'format_repair_attempts': 0, 'initial': initial, 'transitions': trace,
        'calls': [{'attempted': True, 'content': encode_native_action(0.), 'usage': {'total_tokens': 1}} for _ in trace],
        'final_messages': conversation.messages()}


def test_recorded_complete_session(record):
    assert verify(record)['interaction_verified']


@pytest.mark.parametrize('change', ['scripted', 'action', 'clock', 'terminal', 'usage'])
def test_pass_label_cannot_override_bad_evidence(record, change):
    bad = deepcopy(record)
    if change == 'scripted':
        bad['model'] = 'SCRIPTED_TEST'
    elif change == 'action':
        bad['calls'][0]['content'] = '<answer>{"action":1.0}</answer>'
    elif change == 'clock':
        bad['transitions'][0]['delta_t_seconds'] = 1
    elif change == 'terminal':
        bad['transitions'][-1]['done'] = False
    else:
        bad['calls'][0]['usage'] = None
    assert not verify(bad)['interaction_verified']
