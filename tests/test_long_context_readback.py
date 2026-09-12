import json
from tools.probe_long_context_readback import prepare
from unified_compiler.native_action_conversation import build_native_conversation, append_native_transition


def test_readback_uses_preterminal_history_without_sending_expected_values():
    c = build_native_conversation('energyplus_iaq', query='diagnostic',
        initial_observation={'co2_ppm': 456.789, 'constant': 19},
        legal_actions={'minimum': 0, 'maximum': 1}, example_action=0.0)
    for i in range(3):
        append_native_transition(c, 0.0, {'action': 0.0, 'time_seconds': (i+1)*600,
            'observation': {'co2_ppm': 600+i, 'constant': 19}, 'done': i==2})
    original = c.messages()
    messages, selected = prepare({'messages': original})
    assert dict((p, v) for _, p, v in selected)[('co2_ppm',)] == 601
    assert dict((p, v) for _, p, v in selected)[('constant',)] == 19
    prompt = json.loads(messages[-1]['content'])
    assert set(prompt) == {'read_current_numeric_paths', 'instruction'}
    assert c.messages() == original
    assert len(messages) == len(original)-1
