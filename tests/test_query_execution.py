import json
from pathlib import Path
import pytest
from query_construction.contracts import compile_contract
from query_construction.execution import execute_contract
from unified_compiler.inference_budget import InferenceBudget


class ScriptedDiagnostic:
    model = 'scripted-test-not-llm'
    retries = 0
    def __init__(self):
        self.calls = 0
    def complete(self, messages):
        self.calls += 1
        assert 'public_evaluation_conditions' in json.dumps(messages)
        return {'content': '<answer>{"action":{"kind":"wait"}}</answer>',
                'finish_reason': 'stop', 'usage': {}}


def setup(grace=60):
    root = Path(__file__).resolve().parents[1]
    snapshot = json.loads((root / 'generated/query_construction_v1/backend_snapshots_v1/d0_exogenous_context.json').read_text())
    conditions = [{'id': 'diagnostic', 'kind': 'invariant',
        'goal': {'quantity': 'light.state', 'unit': 'category', 'comparator': 'eq', 'target': 'off'},
        'active': {'quantity': 'occupancy.count', 'unit': 'count', 'comparator': 'eq', 'target': 0},
        'activation_grace_seconds': grace, 'start_seconds': 0, 'end_seconds': 720,
        'parameter_origin': 'Synthetic test fixture; not a human query or admitted benchmark task'}]
    public = compile_contract(snapshot['route_id'], conditions, snapshot['initial']['observation'])['public_contract']
    return snapshot, json.loads(json.dumps(public))


def test_real_native_loop_receives_and_scores_same_public_contract():
    snapshot, public = setup()
    client = ScriptedDiagnostic()
    result = execute_contract('Synthetic integration diagnostic', public, snapshot, client,
                              InferenceBudget(max_calls=20), example_action={'kind': 'wait'})
    assert result['status'] == 'native_terminal_reached'
    assert len(result['transitions']) == 12
    assert result['contract_evaluation']['task_success'] is True
    assert not result['admitted'] and client.calls > 0


def test_impossible_reactive_deadline_rejected_before_model_call():
    snapshot, public = setup(0)
    client = ScriptedDiagnostic()
    with pytest.raises(ValueError, match='reactive timing'):
        execute_contract('diagnostic', public, snapshot, client, InferenceBudget(), example_action={'kind': 'wait'})
    assert client.calls == 0


def test_wrong_initial_state_is_not_scored_or_sent_to_model():
    snapshot, public = setup()
    snapshot['initial']['observation']['devices']['interior_lights'] = 'on'
    client = ScriptedDiagnostic()
    result = execute_contract('diagnostic', public, snapshot, client,
                              InferenceBudget(), example_action={'kind': 'wait'})
    assert client.calls == 0
    assert result['episode_identity']['compatible'] is False
    assert result['contract_evaluation'] == {'task_success': None,
        'reason': 'episode_identity_not_established', 'scored': False}
