from copy import deepcopy
import pytest
from unified_compiler.public_receipt import public_native_result, POLICIES
from unified_compiler.inference_budget import InferenceBudget, InferenceBudgetExceeded
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS


def test_private_future_and_reward_removed_without_changing_raw_receipt():
    raw = {'info': {'oracle': 'secret', 'effect': {'native_reward': 123,
        'native_info': {'future_schedule': 'secret'}, 'transformer_power_kw': 4,
        'departures': [{'port': 0, 'final_soc': .7, 'future_schedule': 'secret'}]}}}
    original = deepcopy(raw)
    result = public_native_result('d3_ev2gym_electric_competition', raw)
    assert result == {'info': {'effect': {'transformer_power_kw': 4,
        'departures': [{'port': 0, 'final_soc': .7}]}}}
    assert raw == original


def test_same_public_feedback_under_private_future_mutation():
    for route in PUBLIC_ROUTE_IDS:
        a = {'time_seconds': 60, 'info': {'private_schedule': [1, 2]}}
        b = {'time_seconds': 60, 'info': {'private_schedule': [8, 9], 'reference_policy': 'secret'}}
        assert public_native_result(route, a) == public_native_result(route, b)
    assert set(POLICIES) == set(PUBLIC_ROUTE_IDS)


def test_nested_data_cannot_hide_in_public_scalar():
    with pytest.raises(ValueError):
        public_native_result('d1_citylearn_battery_fault', {'info': {'effect': {'battery_soc': {'future': 1}}}})


class Client:
    retries = 0
    calls = 0
    def complete(self, messages):
        self.calls += 1
        return {'content': 'ok'}


def test_byte_budget_preserves_history_and_never_calls_provider():
    c = Client()
    messages = [{'role': 'user', 'content': '你好' * 20}]
    before = deepcopy(messages)
    with pytest.raises(InferenceBudgetExceeded):
        InferenceBudget(max_message_bytes=30).complete(c, messages)
    assert c.calls == 0 and messages == before


def test_attempts_count_even_when_transport_fails():
    class Broken(Client):
        def complete(self, messages):
            raise RuntimeError('transport')
    budget = InferenceBudget(max_calls=1)
    with pytest.raises(RuntimeError):
        budget.complete(Broken(), [])
    with pytest.raises(InferenceBudgetExceeded):
        budget.complete(Client(), [])
    assert budget.attempted_calls == 1


def test_retries_are_not_hidden_by_budget():
    c = Client()
    c.retries = 2
    with pytest.raises(ValueError):
        InferenceBudget().complete(c, [])
    assert c.calls == 0


def test_real_workflow_nonempty_action_feedback_survives_projection():
    from unified_compiler.agent_interface import make_agent_backend
    from unified_compiler.native_action_conversation import build_native_conversation, append_native_transition
    route = make_agent_backend('d1_discrete_device_fault')
    try:
        initial = route.reset(seed=0)
        action = {'kind': 'act', 'commands': [{'device_id': 'garage_door.main',
            'capability': 'garage.door', 'operation': 'open', 'parameters': {}}]}
        c = build_native_conversation('d1_discrete_device_fault', query='diagnostic',
            initial_observation=initial['observation'], legal_actions=route.legal_actions(), example_action=action)
        receipt = route.step(action)
        assert receipt['info']['feedback']['applied']
        projected = public_native_result('d1_discrete_device_fault', receipt)
        assert projected['info']['feedback']['applied'] == receipt['info']['feedback']['applied']
        append_native_transition(c, action, receipt)
        assert c.validate()[-1] == receipt['observation']
    finally:
        route.close()
