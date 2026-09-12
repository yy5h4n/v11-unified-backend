"""Authored D0 responsibility contract; no human provenance or model score implied.

Thresholds are declared before executing diagnostic policies. Scoring consumes
every native sample, never the number of model decisions.
"""
import math

QUERY = 'Whenever everyone is away, keep the front door closed and the indoor lights off until someone comes home.'
PUBLIC_CONTRACT = {
    'version': 'away_home_guard_v2',
    'activation_response_seconds': 120,
    'external_door_recovery_seconds': 60,
    'native_cadence_seconds': 60,
    'episode_horizon_seconds': 720,
    'text': ('When the home becomes empty, close the door and turn off the lights within 120 simulated seconds. '
             'The interface operates one device per 60-second native interval. '
             'After an external reopening while nobody is home, close the door within 60 simulated seconds. '
             'Deadlines start when changes appear in the native public state, even if you choose a longer wait. '
             'Keep both conditions satisfied afterwards. Repeated reports of an already-open door do not extend its deadline. '
             'Agent-caused violations during maintenance have no new grace period. '
             'The responsibility ends when someone returns; earlier violations still count. '
             'This 720-second episode is one test segment, not an entire day.'),
    'origin': 'authored_simulation_service_condition_not_human_annotation',
}


def evaluate(initial, transitions):
    """Validate native coverage first, then judge the disclosed temporal contract."""
    def number(x):
        return not isinstance(x, bool) and isinstance(x, (int, float)) and math.isfinite(x)
    try:
        if initial['time_seconds'] != 0 or not transitions:
            raise ValueError('initial time must be zero and full native trace must be supplied')
        states = [initial, *[r['observation'] for r in transitions]]
        for i, state in enumerate(states):
            t = state['time_seconds']
            if not number(t) or t != i * PUBLIC_CONTRACT['native_cadence_seconds']:
                raise ValueError('missing, repeated or out-of-order native sample')
            count = state['context']['occupancy_count']
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError('invalid occupancy count')
            if state['devices']['front_door'] not in ('open', 'closed') or state['devices']['interior_lights'] not in ('on', 'off'):
                raise ValueError('invalid device state')
            if not isinstance(state['events'], list):
                raise ValueError('invalid event list')
            if i and (transitions[i-1]['time_seconds'] != t or transitions[i-1]['done'] is not (i == len(states)-1)):
                raise ValueError('receipt clock or terminal marker disagrees with full trajectory')
        if states[-1]['time_seconds'] != PUBLIC_CONTRACT['episode_horizon_seconds']:
            raise ValueError('incomplete declared horizon')
    except (KeyError, TypeError, ValueError) as exc:
        return {'evaluated': False, 'pass': False, 'task_success': None,
                'reason': 'invalid_or_incomplete_trajectory', 'error': str(exc)}

    deadlines = {}; violations = []; away_samples = 0; obligations = []
    for i, state in enumerate(states):
        t = state['time_seconds']; away = state['context']['occupancy_count'] == 0
        if not away:
            deadlines.clear()
            continue
        away_samples += 1
        previous = states[i-1] if i else None
        entering = previous is None or previous['context']['occupancy_count'] != 0
        # External event type is scoped to the affected device. Weather or an
        # unrelated context event cannot excuse an open door or a lit room.
        externally_opened = any(isinstance(e, dict) and e.get('source') == 'external'
            and e.get('event_type') == 'door_state_change'
            and isinstance(e.get('payload'), dict) and e['payload'].get('state') == 'open'
            for e in state['events'])
        for device, target in (('front_door', 'closed'), ('interior_lights', 'off')):
            bad = state['devices'][device] != target
            if not bad:
                deadlines.pop(device, None)
                continue
            if device not in deadlines:
                if entering:
                    deadline = t + PUBLIC_CONTRACT['activation_response_seconds']
                elif device == 'front_door' and externally_opened and previous['devices'][device] == target:
                    deadline = t + PUBLIC_CONTRACT['external_door_recovery_seconds']
                else:
                    deadline = t  # No grace for an agent-caused regression.
                deadlines[device] = deadline
                obligations.append({'device': device, 'observed_at': t, 'deadline': deadline})
            if t >= deadlines[device]:
                violations.append({'device': device, 'time_seconds': t, 'deadline': deadlines[device]})
    pending = [{'device': device, 'deadline': deadline} for device, deadline in deadlines.items()
               if deadline > states[-1]['time_seconds']]
    if away_samples == 0 or pending:
        return {'evaluated': False, 'pass': False, 'task_success': None,
                'reason': 'no_away_window' if not away_samples else 'censored_response_window',
                'violations': violations, 'pending': pending}
    return {'evaluated': True, 'pass': not violations, 'task_success': not violations,
            'violations': violations, 'response_obligations': obligations,
            'away_samples': away_samples, 'native_samples': len(transitions),
            'scope': 'authored D0 contract on this complete episode, not dataset admission'}
