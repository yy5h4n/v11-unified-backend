from copy import deepcopy
import pytest
from backend_casebook_v0.d0_contract import evaluate
from tools.check_d0_case import run
from unified_compiler.agent_interface import make_agent_backend


@pytest.mark.parametrize('policy,expected', [('feedback', True), ('idle', False), ('once', False), ('late', False), ('wrong_lights', False)])
def test_native_policies_distinguish_sustained_responsibility(policy, expected):
    result = run(policy)['result']
    assert result['evaluated'] is True
    assert result['pass'] is expected


@pytest.mark.parametrize('damage', ['short', 'missing', 'duplicate', 'clock', 'terminal', 'unknown_state'])
def test_invalid_trace_is_not_a_model_failure(damage):
    r = run('feedback'); trace = r['transitions']
    if damage == 'short': del trace[-1]
    if damage == 'missing': del trace[3]
    if damage == 'duplicate': trace.insert(3, deepcopy(trace[3]))
    if damage == 'clock': trace[0]['observation']['time_seconds'] = True
    if damage == 'terminal': trace[-1]['done'] = False
    if damage == 'unknown_state': trace[4]['observation']['devices']['front_door'] = 'unknown'
    result = evaluate(r['initial'], trace)
    assert result['evaluated'] is False and result['task_success'] is None


def test_unrelated_and_repeated_events_do_not_extend_deadline():
    r = run('idle')
    for receipt in r['transitions'][4:7]:
        receipt['observation']['events'] += [
            {'source': 'external', 'event_type': 'context_update', 'payload': {'key': 'weather', 'value': 'rain'}},
            {'source': 'external', 'event_type': 'door_state_change', 'payload': {'state': 'open'}}]
    result = evaluate(r['initial'], r['transitions'])
    assert [v['time_seconds'] for v in result['violations']] == [300, 360, 420]


def test_home_release_does_not_erase_prior_failure():
    r = run('late')
    assert r['transitions'][-1]['observation']['context']['occupancy_count'] > 0
    assert r['transitions'][-1]['observation']['devices']['front_door'] == 'closed'
    assert r['result']['violations'] == [{'device': 'front_door', 'time_seconds': 300, 'deadline': 300}]


def test_two_devices_can_be_corrected_with_single_device_action_interface():
    backend = make_agent_backend('d0_exogenous_context')
    try:
        initial = backend.reset(seed=0)['observation']; state = initial; trace = []
        while not state['terminal']:
            # Actual legal actions while home create a two-device correction
            # at departure. No private state editing or combined action.
            if state['time_seconds'] == 0: target, op = 'interior_lights', 'on'
            elif state['time_seconds'] == 60: target, op = 'front_door', 'open'
            elif state['context']['occupancy_count'] == 0 and state['devices']['front_door'] == 'open': target, op = 'front_door', 'close'
            elif state['context']['occupancy_count'] == 0 and state['devices']['interior_lights'] == 'on': target, op = 'interior_lights', 'off'
            else: target, op = None, None
            action = {'kind': 'act', 'command': {'target': target, 'operation': op}} if target else {'kind': 'wait'}
            receipt = backend.step(action); trace.append(receipt); state = receipt['observation']
        result = evaluate(initial, trace)
        assert trace[1]['observation']['devices'] == {'front_door': 'open', 'interior_lights': 'on'}
        assert result['pass'] is True
        assert len([r for r in result['response_obligations'] if r['deadline'] == 240]) == 2
    finally:
        backend.close()
