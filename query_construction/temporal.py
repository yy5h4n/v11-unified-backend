"""Native-sample temporal primitives, independent of routes and model decisions.

No synthetic dynamics, action execution, hidden-field lookup or paused deadlines.
These primitives do not establish source entailment, feasibility or task admission.
"""
from dataclasses import dataclass
import math
import operator


def finite(value):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


@dataclass(frozen=True)
class Predicate:
    path: tuple
    comparator: str
    value: object

    def __post_init__(self):
        if not isinstance(self.path, tuple) or not self.path or any(
                not isinstance(k, (str, int)) or isinstance(k, bool) or (isinstance(k, int) and k < 0) for k in self.path):
            raise ValueError('predicate requires a nonempty typed public observation path')
        if self.comparator not in {'eq', 'ne', 'lt', 'le', 'gt', 'ge'}:
            raise ValueError('unsupported comparator')
        if not (isinstance(self.value, (str, bool)) or finite(self.value)):
            raise ValueError('predicate target must be a finite scalar')
        if self.comparator not in {'eq', 'ne'} and not finite(self.value):
            raise ValueError('ordered predicate target must be numeric')

    def read(self, observation):
        value = observation
        try:
            for key in self.path:
                if isinstance(value, dict) and isinstance(key, str): value = value[key]
                elif isinstance(value, list) and isinstance(key, int): value = value[key]
                else: return None
        except (KeyError, IndexError):
            return None
        if finite(self.value):
            if not finite(value): return None
        elif type(value) is not type(self.value):
            return None
        return {'eq': operator.eq, 'ne': operator.ne, 'lt': operator.lt,
                'le': operator.le, 'gt': operator.gt, 'ge': operator.ge}[self.comparator](value, self.value)


@dataclass(frozen=True)
class Clause:
    id: str
    kind: str
    goal: Predicate
    start_seconds: float
    end_seconds: float
    active: Predicate | None = None
    trigger: Predicate | None = None
    within_seconds: float | None = None
    activation_grace_seconds: float = 0
    trigger_at_window_entry: bool = True

    def __post_init__(self):
        if type(self.trigger_at_window_entry) is not bool:
            raise ValueError('trigger_at_window_entry must be boolean')
        if self.kind != 'response' and not self.trigger_at_window_entry:
            raise ValueError('entry trigger policy requires response')
        if not isinstance(self.id, str) or not self.id or self.kind not in {'invariant', 'response'}:
            raise ValueError('named invariant or response clause required')
        if not all(finite(t) for t in (self.start_seconds, self.end_seconds)) or not 0 <= self.start_seconds <= self.end_seconds:
            raise ValueError('invalid public evaluation window')
        if not isinstance(self.goal, Predicate) or any(p is not None and not isinstance(p, Predicate) for p in (self.active, self.trigger)):
            raise ValueError('typed predicates required')
        if self.kind == 'response':
            if self.trigger is None or not finite(self.within_seconds) or self.within_seconds < 0:
                raise ValueError('response requires trigger and finite nonnegative deadline')
        elif self.trigger is not None or self.within_seconds is not None:
            raise ValueError('invariant cannot silently ignore response fields')
        if not finite(self.activation_grace_seconds) or self.activation_grace_seconds < 0:
            raise ValueError('activation grace must be finite and nonnegative')
        if self.activation_grace_seconds and (self.kind != 'invariant' or self.active is None):
            raise ValueError('activation grace requires conditional invariant')


def evaluate(clauses, samples, *, cadence_seconds, horizon_seconds):
    """Samples include t=0 and every native boundary through the declared horizon.

    Clause windows are closed. Responses trigger on a known false→true edge
    (or true at window entry when trigger_at_window_entry is enabled), not on every repeated true observation. An
    explicit inactive state releases pending responses before deadline checks.
    Unknown observations never stop elapsed time and prevent a success claim.
    """
    if not clauses or len({c.id for c in clauses}) != len(clauses):
        raise ValueError('nonempty unique clause IDs required')
    if not finite(cadence_seconds) or cadence_seconds <= 0 or not finite(horizon_seconds) or horizon_seconds <= 0:
        raise ValueError('positive finite native clock required')
    if not math.isclose(horizon_seconds/cadence_seconds, round(horizon_seconds/cadence_seconds)):
        raise ValueError('horizon must align with native cadence')
    if any(c.end_seconds > horizon_seconds for c in clauses):
        raise ValueError('clause window extends beyond declared evidence horizon')
    valid = (len(samples) == round(horizon_seconds/cadence_seconds)+1
             and all(isinstance(s, dict) and finite(s.get('time_seconds'))
                     and math.isclose(s['time_seconds'], i*cadence_seconds, abs_tol=1e-6)
                     and isinstance(s.get('observation'), dict) for i, s in enumerate(samples)))
    if not valid:
        return {'evaluated': False, 'task_success': None, 'reason': 'incomplete_or_invalid_native_samples'}
    rows = []
    for c in clauses:
        violations = []; unknown = []; pending = []; released = []; opportunities = 0
        prior_trigger = False
        active_since = None
        for sample in samples:
            t = sample['time_seconds']; obs = sample['observation']
            if not c.start_seconds <= t <= c.end_seconds: continue
            active = c.active.read(obs) if c.active else True
            if active is False:
                active_since = None
                released.extend(dict(p, released_at=t) for p in pending)
                pending.clear(); prior_trigger = False
                continue
            if active is None:
                unknown.append({'time_seconds': t, 'field': 'active'})
                # Unknown does not restart a known activation timer.
            goal = c.goal.read(obs)
            if c.kind == 'invariant':
                if active is not True: continue
                if active_since is None: active_since = t
                if t < active_since + c.activation_grace_seconds:
                    continue
                opportunities += 1
                if goal is False: violations.append({'time_seconds': t})
                elif goal is None: unknown.append({'time_seconds': t, 'field': 'goal'})
                continue
            trigger = c.trigger.read(obs)
            if t == c.start_seconds and not c.trigger_at_window_entry:
                # Establish the observed baseline, not a fabricated arrival.
                prior_trigger = trigger
            if active is True:
                if trigger is None or (trigger is True and prior_trigger is None):
                    unknown.append({'time_seconds': t, 'field': 'trigger_edge'})
                if trigger is True and prior_trigger is False:
                    pending.append({'triggered_at': t, 'deadline': t+c.within_seconds})
                    opportunities += 1
                prior_trigger = trigger
            else:
                prior_trigger = None
            remaining = []
            for obligation in pending:
                deadline = obligation['deadline']
                # A success sampled after the deadline cannot prove an on-time
                # response in the unobserved gap. Keep such cases unknown.
                if t > deadline:
                    unknown.append(dict(obligation, time_seconds=t, field='deadline_between_samples'))
                elif active is True and goal is True:
                    pass
                elif t == deadline:
                    target = violations if active is True and goal is False else unknown
                    target.append(dict(obligation, time_seconds=t, field='goal_at_deadline'))
                else:
                    remaining.append(obligation)
            pending = remaining
        if c.kind == 'invariant' and active_since is not None and active_since + c.activation_grace_seconds > c.end_seconds:
            pending.append({'triggered_at': active_since, 'deadline': active_since+c.activation_grace_seconds})
        status = ('failed' if violations else 'indeterminate' if unknown else 'censored' if pending
                  else 'not_exercised' if not opportunities else 'released_only' if len(released) == opportunities
                  else 'passed')
        rows.append({'clause_id': c.id, 'status': status, 'opportunities': opportunities,
                     'violations': violations, 'unknown': unknown, 'pending': pending, 'released': released})
    success = False if any(r['status'] == 'failed' for r in rows) else (
        True if all(r['status'] == 'passed' for r in rows) else None)
    return {'evaluated': True, 'task_success': success, 'clauses': rows,
            'scope': 'discrete native-sample outcome checks; no claim of continuous interpolation or dataset admission'}
