import pytest
from unified_compiler.llm_backend_session import run_session
from unified_compiler.inference_budget import InferenceBudget
from unified_compiler.agent_interface import AgentActionError


class Backend:
    def __init__(self):
        self.steps = 0
        self.closed = False
    def reset(self, seed=0):
        return {'observation': self.observe(), 'time_seconds': 0, 'done': False}
    def observe(self):
        return {'co2_ppm': self.steps}
    def legal_actions(self):
        return {'minimum': 0, 'maximum': 1}
    def step(self, action):
        if action != 0.5:
            raise AgentActionError('invalid test action')
        self.steps += 1
        return {'action': action, 'observation': self.observe(), 'time_seconds': self.steps*600,
                'delta_t_seconds': 600, 'done': self.steps == 2, 'info': {}}
    def close(self):
        self.closed = True


class Client:
    model = 'unit-test-double'
    retries = 0
    content = '<answer>{"action":0.5}</answer>'
    def complete(self, messages):
        return {'content': self.content, 'usage': {'prompt_tokens': 1}}


def run(client=None, budget=None):
    backend = Backend()
    events = []
    result = run_session('energyplus_iaq', 'diagnostic', client or Client(), budget or InferenceBudget(),
        example_action=.5, backend_factory=lambda _: backend, emit=events.append)
    return result, backend, events


def test_complete_loop_is_not_task_success():
    result, backend, events = run()
    assert result['status'] == 'native_terminal_reached'
    assert result['task_success'] is None
    assert result['state_reconstruction_verified']
    assert result['attempted_calls'] == backend.steps == 2
    assert backend.closed
    assert [e['type'] for e in events][:4] == ['session_started', 'request_intent', 'model_response', 'action_intent']
    assert not result['declared_horizon_reached']


def test_budget_stop_never_submits_an_extra_action():
    result, backend, _ = run(budget=InferenceBudget(max_calls=1))
    assert result['status'] == 'budget_exhausted'
    assert backend.steps == result['attempted_calls'] == 1
    assert result['calls'][-1]['attempted'] is False
    assert backend.closed


@pytest.mark.parametrize('content, status, owner', [
    ('not JSON', 'model_format_error', 'model_format'),
    ('<answer>{"action":9}</answer>', 'action_rejected', 'model_action'),
])
def test_model_errors_do_not_run_fallback_actions(content, status, owner):
    client = Client()
    client.content = content
    result, backend, _ = run(client)
    assert result['status'] == status and result['failure_owner'] == owner
    assert backend.steps == 0 and result['attempted_calls'] == 1
    assert backend.closed


def test_transport_failure_is_not_model_failure():
    class Broken(Client):
        def complete(self, messages):
            raise RuntimeError('provider unavailable')
    result, backend, _ = run(Broken())
    assert result['failure_owner'] == 'transport'
    assert backend.steps == 0 and result['attempted_calls'] == 1


def test_provider_output_limit_is_not_malformed_model_action():
    class Truncated(Client):
        def complete(self, messages):
            return {'content': '<answer>{"action":', 'finish_reason': 'length', 'usage': {'completion_tokens': 320}}
    result, backend, _ = run(Truncated())
    assert result['status'] == 'model_output_budget_error'
    assert result['failure_owner'] == 'infrastructure_output_budget'
    assert backend.steps == 0 and result['attempted_calls'] == 1


def test_cumulative_bytes_limit_counts_failed_requests():
    budget = InferenceBudget(max_total_message_bytes=2)
    budget.complete(Client(), [])
    with pytest.raises(RuntimeError):
        budget.complete(Client(), [])
    assert budget.attempted_calls == 1 and budget.total_message_bytes == 2


def test_native_value_error_is_infrastructure_not_model_action():
    from unified_compiler.agent_interface import AgentReceiptAdapter, AgentRuntimeError
    class Native:
        def step(self, action):
            raise ValueError('solver table invalid after internal update')
    adapter = AgentReceiptAdapter(Native())
    adapter._started = True
    adapter.action_validator = lambda action: None
    with pytest.raises(AgentRuntimeError):
        adapter.step(.5)
    assert adapter._poisoned
