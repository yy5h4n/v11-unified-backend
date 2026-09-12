"""Agent-selected wake times; no simulator time skipping or control policy."""
from copy import deepcopy
import math
from .llm_conversation import ConversationProtocolError, canonical_json, parse_assistant_action


def decode_decision(content, *, cadence, now, horizon):
    document = parse_assistant_action({'role': 'assistant', 'content': content})
    canonical_json(document)
    if set(document) not in ({'action'}, {'action', 'wait'}):
        raise ConversationProtocolError('decision fields must be action and optional wait')
    wait = document.get('wait', {'mode': 'for', 'duration_seconds': cadence})
    if not isinstance(wait, dict):
        raise ConversationProtocolError('wait must be an object')
    mode = wait.get('mode')
    fields = {'for': {'mode', 'duration_seconds'}, 'until': {'mode', 'time_seconds'},
              'until_event': {'mode', 'timeout_seconds', 'observation_path', 'equals'}}
    if mode not in fields or set(wait) != fields[mode]:
        raise ConversationProtocolError('invalid wait fields or mode')
    key = {'for': 'duration_seconds', 'until': 'time_seconds', 'until_event': 'timeout_seconds'}[mode]
    value = wait[key]
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ConversationProtocolError('wait time must be finite numeric seconds')
    duration = value - now if mode == 'until' else value
    if duration <= 0 or duration > 604800:
        raise ConversationProtocolError('wait duration must be in (0,604800]')
    if mode == 'until_event':
        path = wait['observation_path']
        if not isinstance(path, list) or not path or any(type(k) not in (str, int) or (type(k) is int and k < 0) for k in path):
            raise ConversationProtocolError('observation_path must be nonempty string keys / nonnegative indices')
        if isinstance(wait['equals'], (dict, list)):
            raise ConversationProtocolError('event equals must be a JSON scalar')
    # Native cadence is never enlarged. Round only the wake boundary upward.
    target = min(horizon, now + math.ceil(duration / cadence) * cadence)
    return deepcopy(document), deepcopy(wait), target


def event_matches(wait, observation):
    if wait['mode'] != 'until_event':
        return False
    value = observation
    try:
        for key in wait['observation_path']:
            value = value[key]
    except (KeyError, IndexError, TypeError):
        return False
    return canonical_json(value) == canonical_json(wait['equals'])


def held_action(route, requested, last_action, *, first, cadence):
    if route == 'd0_exogenous_context':
        return deepcopy(requested) if first and requested is not None else {'kind': 'wait'}
    if route == 'd1_discrete_device_fault':
        return deepcopy(requested) if first and requested is not None else {'kind': 'wait', 'mode': 'for', 'duration_seconds': cadence}
    action = requested if requested is not None else last_action
    if action is None:
        raise ConversationProtocolError('continuous-control wait requires a previously specified control or an explicit action')
    return deepcopy(action)


WAIT_PROTOCOL = {
    'response_format': 'Reply exactly <answer>{"action": NATIVE_ACTION_OR_NULL, "wait": WAIT_OBJECT}</answer>. Use one answer envelope only. No Markdown code fences or surrounding prose.',
    'wait_modes': {
        'for': {'mode': 'for', 'duration_seconds': 'positive seconds'},
        'until': {'mode': 'until', 'time_seconds': 'future elapsed simulation seconds, not wall clock'},
        'until_event': {'mode': 'until_event', 'timeout_seconds': 'positive seconds',
                        'observation_path': 'array of actual observation keys/indices, starting at the current observation root', 'equals': 'scalar target value'}},
    'semantics': [
        'Choose when to make your next decision; native cadence is not a required LLM call frequency.',
        'An explicit continuous control is held by reapplying its setpoint each native interval, including across disturbances. This is your requested control, not an automatic corrective policy.',
        'Discrete commands/rule operations execute once; remaining intervals are genuine no-command waits, not repeated commands.',
        'action:null waits without a new control; continuous routes require a prior explicit control.',
        'Wait duration includes the first action interval. Wake rounds up to a native boundary, capped by terminal time; maximum requested duration is seven days.',
        'until_event checks only the public observation at each completed native interval; it wakes when the selected value equals the target, or at timeout/terminal. Missing fields do not match. No implicit fault/safety wakeups.',
        'observation_path is relative to the current reconstructed observation itself. Do not prepend public, observation, initial_observation or any message-envelope name. Use only actual keys/indices visible in that observation.',
        'All intermediate public state deltas and public receipts return in action_result.microsteps at wake; raw native trajectory is retained locally for evaluation. Waiting never hides an intervening violation.',
        'The last microstep uses observation_source:wake_observation: its state is the current wake observation reconstructed from the enclosing observation_delta relative to the preceding wake. Earlier microsteps carry their own deltas in order. Do not apply the enclosing wake delta to an intermediate state.',
        'A native workflow wait action is still supported; avoid nesting a native wait inside an outer wait. Omitted outer wait means one native operation (legacy compatibility).']}
