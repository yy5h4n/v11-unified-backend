import pytest
from unified_compiler.native_action_conversation import (
    ACTION_SEMANTICS, build_native_conversation, encode_native_action, decode_native_action, append_native_transition,
)
from unified_compiler.llm_conversation import ConversationProtocolError
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS


@pytest.mark.parametrize("action", [0., [0., -0.1], {"kind": "act", "commands": []}, {"building": {"battery_rate": .5}}])
def test_native_action_shapes_roundtrip(action):
    assert decode_native_action(encode_native_action(action)) == action


@pytest.mark.parametrize("text", [
    'prose <answer>{"action":0}</answer>', '{"action":0}',
    '<answer>{"action":0,"action":1}</answer>', '<answer>{"action":NaN}</answer>',
    '<answer>{"action":1e999}</answer>', '<answer>{"action":0,"reason":"x"}</answer>',
])
def test_ambiguous_outputs_rejected(text):
    with pytest.raises(ValueError): decode_native_action(text)


def conversation():
    return build_native_conversation("energyplus_iaq", query="diagnostic", initial_observation={"x": 0},
                                     legal_actions={"range": [0, 1]}, example_action=0.)


def test_receipt_mismatch_does_not_mutate_history():
    c = conversation()
    before = c.messages()
    with pytest.raises(ConversationProtocolError):
        append_native_transition(c, 0., {"action": 1., "observation": {"x": 1}})
    assert c.messages() == before and not c.pending_action


def test_scalar_transition_uses_existing_lossless_protocol():
    c = conversation()
    append_native_transition(c, .5, {"action": .5, "observation": {"x": 1}, "time_seconds": 600, "info": {}})
    assert c.validate() == [{"x": 0}, {"x": 1}]
    assert "observation_delta" in c.messages()[-1]["content"]


def test_every_formal_route_has_semantics_without_implying_wait_is_off():
    assert set(ACTION_SEMANTICS) == set(PUBLIC_ROUTE_IDS)
    assert "Wait issues no command" in ACTION_SEMANTICS["d0_exogenous_context"]
    assert "retains the previous" in ACTION_SEMANTICS["d1_ev2gym_fault"]
