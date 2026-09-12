"""Native state semantics, not merely agreement between serialized copies."""
import pytest
from unified_compiler.adapters.citylearn_battery_fault import (
    CityLearnBatteryFaultAdapter, FAULT_MODES,
)
from unified_compiler.agent_interface import make_agent_backend


@pytest.mark.parametrize('profile', FAULT_MODES)
def test_native_soc_matches_public_state_through_faults_and_reset(profile):
    adapter = CityLearnBatteryFaultAdapter(profile=profile)
    initial = adapter.reset(seed=0)
    episode = adapter._episode
    actions = [0.75, -0.25, 0.5, 0.0, -0.25, 0.75, 0.0, -0.25]
    for i, action in enumerate(actions):
        record = adapter.step(action)
        native_soc = float(episode.battery.soc[i])
        assert record['observation']['battery_soc'] == native_soc
        assert record['effect']['battery_soc'] == native_soc
        if not record['episode_done']:
            assert adapter.observe() == record['observation']
    assert adapter.reset(seed=0) == initial
    assert adapter.step(actions[0])['observation']['battery_soc'] > 0


def test_public_facade_soc_survives_delta_history_and_terminal():
    from unified_compiler.native_action_conversation import build_native_conversation, append_native_transition
    route = make_agent_backend('d1_citylearn_battery_fault')
    try:
        initial = route.reset(seed=0)
        conversation = build_native_conversation('d1_citylearn_battery_fault', query='test',
            initial_observation=initial['observation'], legal_actions=route.legal_actions(), example_action=0.25)
        for action in [0.25, 0.25, -0.25, 0.0, 0.25, -0.25, 0.0, 0.25]:
            receipt = route.step(action)
            assert receipt['observation']['battery_soc'] == receipt['info']['effect']['battery_soc']
            assert route.observe() == receipt['observation']
            append_native_transition(conversation, action, receipt)
            assert conversation.validate()[-1]['battery_soc'] == receipt['info']['effect']['battery_soc']
        assert receipt['done']
    finally:
        route.close()
