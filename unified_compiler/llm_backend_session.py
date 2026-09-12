"""One closed-loop session with explicit failure ownership and no action repair."""
from copy import deepcopy
import time

from .agent_interface import make_agent_backend, AgentActionError
from .inference_budget import InferenceBudgetExceeded
from .llm_conversation import canonical_json, observation_delta, apply_observation_delta
from .decision_wait import decode_decision, held_action, event_matches
from .public_receipt import public_native_result
from .native_action_conversation import build_native_conversation, decode_native_action, append_native_transition
from .route_registry import route_metadata
from .observation_contracts import validate_public_observation


def run_session(route_id, query, client, budget, *, example_action, seed=0,
                backend_factory=make_agent_backend, emit=lambda event: None, autonomous_wait=True):
    started = time.monotonic()
    report = {'route_id': route_id, 'seed': seed, 'query': query, 'model': getattr(client, 'model', None),
              'status': 'starting', 'task_success': None, 'calls': [], 'transitions': [],
              'clock': 'paused_during_inference', 'history_mode': 'initial_full_plus_lossless_deltas',
              'automatic_retries': 0, 'format_repair_attempts': 0}
    report['decisions'] = []
    report['autonomous_wait'] = autonomous_wait
    if autonomous_wait:
        report['history_mode'] = 'initial_full_plus_lossless_decision_batches'
    backend = None
    stage = 'initialization'
    conversation = None
    try:
        backend = backend_factory(route_id)
        initial = backend.reset(seed=seed)
        validate_public_observation(route_id, initial['observation'])
        legal = backend.legal_actions()
        conversation = build_native_conversation(route_id, query=query,
            initial_observation=initial['observation'], legal_actions=legal,
            example_action=example_action, initial_time_seconds=initial.get('time_seconds', 0),
            runtime_limits={'max_model_calls': budget.max_calls, 'max_message_bytes': budget.max_message_bytes,
                            'max_total_message_bytes': budget.max_total_message_bytes,
                            'on_exhaustion': 'stop without another action; not a task failure or successful completion',
                            'history_truncation': False}, autonomous_wait=autonomous_wait)
        report['initial'] = initial
        emit({'type': 'session_started', 'route_id': route_id, 'initial': initial, 'schema': legal,
              'budget': {'max_calls': budget.max_calls, 'max_message_bytes': budget.max_message_bytes,
                         'max_total_message_bytes': budget.max_total_message_bytes}})
        previous_time = initial.get('time_seconds', 0)
        metadata = route_metadata(route_id)
        last_action = None
        wake_observations = []
        while not initial.get('done', False):
            stage = 'model_transport'
            messages = conversation.messages()
            decision = len(report['calls'])
            # Persist intent before sending. A crash here is NOT resumable by
            # replaying actions; external request outcome could be unknown.
            emit({'type': 'request_intent', 'decision': decision, 'messages': messages})
            before_calls = budget.attempted_calls
            call = {'decision': decision, 'attempted': False}
            report['calls'].append(call)
            try:
                response = budget.complete(client, messages)
            finally:
                call['attempted'] = budget.attempted_calls > before_calls
            call.update(response)
            emit({'type': 'model_response', **deepcopy(call)})
            if response.get('finish_reason') == 'length':
                stage = 'model_output_budget'
                raise ValueError('provider reported output token limit; no native action executed')
            stage = 'model_format'
            if autonomous_wait:
                document, wait, target = decode_decision(response['content'], cadence=metadata['cadence_seconds'],
                    now=previous_time, horizon=metadata['horizon_seconds'])
                action = document['action']
                if route_id == 'd1_discrete_device_fault' and isinstance(action, dict) and action.get('kind') == 'wait' and 'wait' in document:
                    raise ValueError('native workflow wait and outer wait cannot be nested')
            else:
                action = decode_native_action(response['content'])
                document, wait, target = {'action': action}, {'mode': 'for'}, previous_time
            emit({'type': 'action_intent', 'decision': decision, 'action': action, 'decision_document': document})
            begin = len(report['transitions'])
            microsteps = []
            previous_observation = conversation.latest_observation
            wake_reason = 'deadline'
            while True:
                stage = 'model_format'
                native_action = held_action(route_id, action, last_action, first=not microsteps,
                    cadence=metadata['cadence_seconds']) if autonomous_wait else action
                stage = 'native_execution'
                receipt = backend.step(native_action)
                report['transitions'].append(receipt)
                emit({'type': 'native_receipt', 'decision': decision, 'receipt': receipt})
                stage = 'receipt_protocol'
                validate_public_observation(route_id, receipt['observation'])
                if receipt['time_seconds'] <= previous_time:
                    raise ValueError('native clock did not advance')
                if abs(receipt['time_seconds'] - previous_time - receipt['delta_t_seconds']) > 1e-6:
                    raise ValueError('native receipt time and delta disagree')
                if canonical_json(backend.observe()) != canonical_json(receipt['observation']):
                    raise ValueError('observe differs from actual receipt')
                delta = observation_delta(previous_observation, receipt['observation'])
                if canonical_json(apply_observation_delta(previous_observation, delta)) != canonical_json(receipt['observation']):
                    raise ValueError('microstep observation cannot be reconstructed')
                microsteps.append({'action': deepcopy(native_action), 'action_result': public_native_result(route_id, receipt), 'observation_delta': delta})
                previous_observation = receipt['observation']
                previous_time = receipt['time_seconds']
                if receipt['done']:
                    wake_reason = 'terminal'; break
                if autonomous_wait and event_matches(wait, receipt['observation']):
                    wake_reason = 'event'; break
                if not autonomous_wait or previous_time >= target:
                    break
            if action is not None:
                last_action = deepcopy(action)
            if autonomous_wait:
                # The outer wake delta already reconstructs the final state.
                # Reference it rather than duplicate a potentially large zone
                # vector delta in the last microstep. Earlier states remain.
                microsteps[-1].pop('observation_delta')
                microsteps[-1]['observation_source'] = 'wake_observation'
                conversation.append_action(document)
                conversation.append_environment({'wake_reason': wake_reason, 'time_seconds': previous_time,
                    'microsteps': microsteps}, receipt['observation'])
            else:
                append_native_transition(conversation, action, receipt)
            wake_observations.append(receipt['observation'])
            report['decisions'].append({'document': document, 'transition_start': begin,
                'transition_end': len(report['transitions']), 'wake_reason': wake_reason, 'time_seconds': previous_time})
            emit({'type': 'decision_finished', 'decision': decision, 'native_steps': len(microsteps), 'wake_reason': wake_reason})
            if receipt['done']:
                break
        stage = 'final_verification'
        states = conversation.validate()
        if canonical_json(states[1:]) != canonical_json(wake_observations):
            raise ValueError('history cannot reconstruct native public observations')
        report['state_reconstruction_verified'] = True
        report['status'] = 'native_terminal_reached'
        report['declared_horizon_seconds'] = route_metadata(route_id)['horizon_seconds']
        report['final_time_seconds'] = previous_time
        report['declared_horizon_reached'] = abs(previous_time-report['declared_horizon_seconds']) < 1e-6
    except InferenceBudgetExceeded as exc:
        report.update(status='budget_exhausted', error=str(exc), failure_owner='infrastructure_budget')
    except AgentActionError as exc:
        report.update(status='action_rejected', error=str(exc), failure_owner='model_action' if stage == 'native_execution' else 'infrastructure')
    except Exception as exc:
        owner = ('model_format' if stage == 'model_format' else 'transport' if stage == 'model_transport'
                 else 'infrastructure_output_budget' if stage == 'model_output_budget' else 'infrastructure')
        report.update(status=stage+'_error', error=f'{type(exc).__name__}: {exc}', failure_owner=owner)
    finally:
        report['last_stage'] = stage
        report['attempted_calls'] = budget.attempted_calls
        report['message_bytes_attempted'] = budget.total_message_bytes
        report['wall_seconds'] = time.monotonic() - started
        if conversation is not None:
            report['final_messages'] = conversation.messages()
        if backend is not None:
            try:
                backend.close()
                report['backend_closed'] = True
            except Exception as exc:
                report['cleanup_error'] = f'{type(exc).__name__}: {exc}'
                report['status'] = 'cleanup_error'
        emit({'type': 'session_finished', 'status': report['status'], 'attempted_calls': report['attempted_calls'],
              'error': report.get('error'), 'failure_owner': report.get('failure_owner')})
    return report
