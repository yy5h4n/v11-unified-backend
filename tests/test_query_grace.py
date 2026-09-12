from query_construction.temporal import Clause, Predicate, evaluate


def run(states, grace=1):
    c = Clause('c', 'invariant', Predicate(('light',), 'eq', 'off'), 0, len(states)-1,
               active=Predicate(('away',), 'eq', True), activation_grace_seconds=grace)
    samples = [{'time_seconds': i, 'observation': {'away': a, 'light': b}} for i, (a,b) in enumerate(states)]
    return evaluate([c], samples, cadence_seconds=1, horizon_seconds=len(states)-1)


def test_one_response_interval_then_maintain():
    assert run([(False,'on'), (True,'on'), (True,'off'), (True,'off')])['task_success'] is True
    assert run([(False,'on'), (True,'on'), (True,'off'), (True,'on')])['task_success'] is False


def test_repeated_activation_does_not_reset_grace():
    assert run([(False,'on'), (True,'on'), (True,'on'), (True,'off')])['task_success'] is False


def test_unknown_does_not_pause_and_late_activation_is_censored():
    assert run([(True,'on'), (None,'on'), (True,'on')], grace=2)['task_success'] is False
    assert run([(False,'on'), (False,'on'), (True,'on')])['task_success'] is None
