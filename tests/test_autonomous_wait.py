import json
import pytest
from unified_compiler.decision_wait import decode_decision
from unified_compiler.llm_backend_session import run_session
from unified_compiler.inference_budget import InferenceBudget
from unified_compiler.llm_conversation import canonical_json, apply_observation_delta


def test_event_path_prompt_does_not_invent_a_public_wrapper():
    from unified_compiler.decision_wait import WAIT_PROTOCOL
    assert WAIT_PROTOCOL['wait_modes']['until_event']['observation_path'] != ['public', 'field']
    assert any('Do not prepend public' in rule for rule in WAIT_PROTOCOL['semantics'])


class Backend:
    def __init__(self):
        self.steps = 0
        self.actions = []
    def reset(self, seed=0):
        return {'time_seconds': 0, 'observation': self.observe(), 'done': False}
    def observe(self):
        return {'co2_ppm': 2000 if self.steps == 2 else 500}
    def legal_actions(self):
        return {'minimum': 0, 'maximum': 1}
    def step(self, action):
        self.actions.append(action)
        self.steps += 1
        return {'action': action, 'time_seconds': self.steps*300, 'delta_t_seconds': 300,
                'done': self.steps == 4, 'observation': self.observe(), 'info': {}}
    def close(self):
        pass


class Client:
    model = 'unit-test'
    retries = 0
    def __init__(self, documents):
        self.documents = iter(documents)
        self.messages = []
    def complete(self, messages):
        self.messages.append(messages)
        return {'content': '<answer>'+canonical_json(next(self.documents))+'</answer>', 'usage': {'total_tokens': 1}}


def run(documents):
    backend = Backend()
    client = Client(documents)
    report = run_session('energyplus_iaq', 'test', client, InferenceBudget(), example_action=.5,
                         backend_factory=lambda _: backend)
    return report, backend, client


def test_four_native_intervals_one_decision_and_transient_is_preserved():
    report, backend, client = run([{'action': .5, 'wait': {'mode': 'for', 'duration_seconds': 1200}}])
    assert report['status'] == 'native_terminal_reached'
    assert report['attempted_calls'] == 1 and backend.steps == 4
    assert backend.actions == [.5]*4
    payload = json.loads(report['final_messages'][-1]['content'])
    assert payload['action_result']['microsteps'][-1]['observation_source'] == 'wake_observation'
    assert 'observation_delta' not in payload['action_result']['microsteps'][-1]
    state = report['initial']['observation']
    wake_state = apply_observation_delta(state, payload['observation_delta'])
    co2 = []
    for step in payload['action_result']['microsteps']:
        state = wake_state if step.get('observation_source') == 'wake_observation' else apply_observation_delta(state, step['observation_delta'])
        co2.append(state['co2_ppm'])
    assert co2 == [500, 2000, 500, 500]


def test_event_wakes_at_public_transient_then_pure_wait_holds_control():
    report, backend, _ = run([
        {'action': .5, 'wait': {'mode': 'until_event', 'timeout_seconds': 1200,
                             'observation_path': ['co2_ppm'], 'equals': 2000}},
        {'action': None, 'wait': {'mode': 'for', 'duration_seconds': 600}}])
    assert report['attempted_calls'] == 2 and backend.steps == 4
    assert report['decisions'][0]['wake_reason'] == 'event'
    assert report['decisions'][0]['time_seconds'] == 600
    assert backend.actions == [.5]*4


@pytest.mark.parametrize('duration', [0, -1, True, 604801])
def test_bad_wait_never_mutates(duration):
    report, backend, _ = run([{'action': .5, 'wait': {'mode': 'for', 'duration_seconds': duration}}])
    assert report['status'] == 'model_format_error' and backend.steps == 0


def test_wait_without_prior_control_does_not_invent_zero():
    report, backend, _ = run([{'action': None, 'wait': {'mode': 'for', 'duration_seconds': 600}}])
    assert report['status'] == 'model_format_error' and backend.steps == 0


def test_until_rounds_boundary_and_caps_terminal():
    _, _, target = decode_decision('<answer>{"action":0.5,"wait":{"mode":"until","time_seconds":601}}</answer>',
                                 cadence=300, now=300, horizon=800)
    assert target == 800


def test_batch_audit_detects_lost_transient_even_if_final_state_matches():
    from copy import deepcopy
    from tools.audit_real_sessions import verify_batches
    from unified_compiler.llm_conversation import validate_canonical_conversation
    report, _, _ = run([{'action': .5, 'wait': {'mode': 'for', 'duration_seconds': 1200}}])
    states = validate_canonical_conversation(report['final_messages'])
    assert verify_batches(report, report['calls'], report['transitions'], states)
    bad = deepcopy(report)
    payload = json.loads(bad['final_messages'][-1]['content'])
    payload['action_result']['microsteps'][1]['observation_delta'] = {'op': 'none'}
    bad['final_messages'][-1]['content'] = canonical_json(payload)
    assert not verify_batches(bad, bad['calls'], bad['transitions'], states)


def test_original_workflow_native_wait_still_advances_multiple_ticks():
    from unified_compiler.agent_interface import make_agent_backend
    backend = make_agent_backend('d1_discrete_device_fault')
    client = Client([{'action': {'kind': 'wait', 'mode': 'for', 'duration_seconds': 600}}])
    report = run_session('d1_discrete_device_fault', 'test', client, InferenceBudget(max_calls=1),
        example_action={'kind': 'wait', 'mode': 'for', 'duration_seconds': 60}, backend_factory=lambda _: backend)
    assert report['status'] == 'native_terminal_reached'
    assert report['attempted_calls'] == 1
    assert report['final_time_seconds'] == 600


def test_native_d0_wait_does_not_reissue_door_command():
    from unified_compiler.adapters.d0_exogenous_context import D0ContextTrajectory, ExogenousContextSchedule, ExternalContextEvent
    from unified_compiler.agent_interface import AgentReceiptAdapter
    native = D0ContextTrajectory(ExogenousContextSchedule([ExternalContextEvent(2, 'door_state_change', {'state': 'open'})]), 4)
    backend = AgentReceiptAdapter(native, tick_seconds=60)
    client = Client([{'action': {'kind': 'act', 'command': {'target': 'front_door', 'operation': 'close'}},
                      'wait': {'mode': 'for', 'duration_seconds': 240}}])
    report = run_session('d0_exogenous_context', 'test', client, InferenceBudget(),
        example_action={'kind': 'wait'}, backend_factory=lambda _: backend)
    assert report['status'] == 'native_terminal_reached'
    assert report['attempted_calls'] == 1
    assert [r['action']['kind'] for r in report['transitions']] == ['act', 'wait', 'wait', 'wait']
    assert report['transitions'][-1]['observation']['devices']['front_door'] == 'open'


@pytest.mark.parametrize('wake_on_fault', [False, True])
def test_native_battery_fault_trace_is_identical_with_fewer_decisions(wake_on_fault):
    from tools.audit_real_sessions import verify_batches
    from unified_compiler.llm_conversation import validate_canonical_conversation
    route = 'd1_citylearn_battery_fault'
    fixed = run_session(route, 'Native diagnostic; scripted client, no model score.',
        Client([{'action': .5} for _ in range(8)]), InferenceBudget(max_calls=8),
        example_action=.5, autonomous_wait=False)
    first_wait = ({'mode': 'until_event', 'timeout_seconds': 28800,
                   'observation_path': ['battery_health', 'active'], 'equals': True}
                  if wake_on_fault else {'mode': 'for', 'duration_seconds': 28800})
    decisions = [{'action': .5, 'wait': first_wait}]
    if wake_on_fault:
        decisions.append({'action': None, 'wait': {'mode': 'until', 'time_seconds': 28800}})
    adaptive = run_session(route, 'Native diagnostic; scripted client, no model score.',
        Client(decisions), InferenceBudget(max_calls=2), example_action=.5)
    assert fixed['status'] == adaptive['status'] == 'native_terminal_reached'
    assert canonical_json(fixed['transitions']) == canonical_json(adaptive['transitions'])
    assert adaptive['attempted_calls'] == (2 if wake_on_fault else 1)
    health = [r['observation']['battery_health']['active'] for r in adaptive['transitions']]
    assert any(health) and health[-1] is False
    if wake_on_fault:
        assert adaptive['decisions'][0]['wake_reason'] == 'event'
        assert adaptive['decisions'][0]['time_seconds'] == 7200
    else:
        assert adaptive['decisions'][0]['wake_reason'] == 'terminal'
    states = validate_canonical_conversation(adaptive['final_messages'])
    assert verify_batches(adaptive, adaptive['calls'], adaptive['transitions'], states)
