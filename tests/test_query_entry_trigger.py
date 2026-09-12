import pytest
from query_construction.temporal import Clause, Predicate, evaluate


def score(entry, values):
    c = Clause('arrival', 'response', Predicate(('light',), 'eq', 'on'),
               0, len(values)-1, trigger=Predicate(('present',), 'eq', True),
               within_seconds=1, trigger_at_window_entry=entry)
    samples = [{'time_seconds': i, 'observation': {'present': p, 'light': light}}
               for i, (p, light) in enumerate(values)]
    return evaluate([c], samples, cadence_seconds=1, horizon_seconds=len(values)-1)


def test_event_only_does_not_invent_initial_arrival_but_scores_later_edge():
    values = [(True, 'off'), (False, 'off'), (True, 'off'), (True, 'on')]
    assert score(False, values)['task_success'] is True
    assert score(False, values)['clauses'][0]['opportunities'] == 1
    assert score(True, values)['task_success'] is False


def test_no_arrival_is_not_success_and_late_response_fails():
    assert score(False, [(True, 'on')]*4)['task_success'] is None
    assert score(False, [(False, 'off'), (True, 'off'), (True, 'off')])['task_success'] is False


def test_entry_policy_is_strict_boolean():
    with pytest.raises(ValueError, match='boolean'):
        score('false', [(False, 'off'), (True, 'on')])
