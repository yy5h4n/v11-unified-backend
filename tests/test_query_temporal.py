import pytest
from query_construction.temporal import Clause, Predicate, evaluate


def samples(values):
    return [{'time_seconds':i*60, 'observation':v} for i,v in enumerate(values)]


def score(c, values):
    return evaluate([c],samples(values),cadence_seconds=60,horizon_seconds=(len(values)-1)*60)


def response(end=180, within=60):
    return Clause('notify','response',Predicate(('notified',),'eq',True),0,end,
                  trigger=Predicate(('finished',),'eq',True),within_seconds=within)


def test_intermediate_failure_survives_recovery():
    c=Clause('air','invariant',Predicate(('co2',),'le',1200),0,120)
    assert score(c,[{'co2':1000},{'co2':1500},{'co2':1000}])['task_success'] is False


def test_exact_deadline_success_and_late_failure():
    base=[{'finished':False,'notified':False},{'finished':True,'notified':False},
          {'finished':True,'notified':True},{'finished':True,'notified':True}]
    assert score(response(),base)['task_success'] is True
    base[2]['notified']=False
    assert score(response(),base)['task_success'] is False


def test_unknown_does_not_pause_deadline():
    r=score(response(),[{'finished':False,'notified':False},{'finished':True,'notified':False},
                        {'finished':True},{'finished':True,'notified':True}])
    assert r['task_success'] is None
    assert r['clauses'][0]['unknown'][0]['time_seconds']==120


def test_repeated_true_trigger_does_not_extend_deadline():
    r=score(response(),[{'finished':False,'notified':False}]+[{'finished':True,'notified':False}]*3)
    assert r['task_success'] is False and r['clauses'][0]['opportunities']==1


def test_new_edge_creates_another_obligation():
    values=[{'finished':True,'notified':True},{'finished':False,'notified':False},
            {'finished':True,'notified':False},{'finished':True,'notified':False}]
    r=score(response(),values)
    assert r['task_success'] is False and r['clauses'][0]['opportunities']==2


def test_pending_beyond_horizon_is_censored():
    r=score(response(),[{'finished':False,'notified':False}]*3+[{'finished':True,'notified':False}])
    assert r['task_success'] is None and r['clauses'][0]['status']=='censored'


def test_missing_native_sample_is_not_task_failure():
    r=evaluate([response()],samples([{}, {}, {}, {}])[::2],cadence_seconds=60,horizon_seconds=180)
    assert not r['evaluated'] and r['task_success'] is None


def test_numeric_bool_and_missing_paths_are_unknown():
    p=Predicate(('x',),'le',1)
    assert p.read({'x':True}) is None and p.read({}) is None
    assert p.read({'x':float('nan')}) is None


def test_no_trigger_is_not_evidence_of_responsibility_success():
    assert score(response(),[{'finished':False,'notified':False}]*4)['task_success'] is None


def test_deadline_between_native_samples_is_not_guessed():
    r=score(response(within=30),[{'finished':False,'notified':False},{'finished':True,'notified':False},
                               {'finished':True,'notified':True},{'finished':True,'notified':True}])
    assert r['task_success'] is None


def test_invalid_contract_does_not_silently_drop_fields():
    with pytest.raises(ValueError): Clause('a','invariant',Predicate(('x',),'eq',1),0,60,within_seconds=1)
    with pytest.raises(ValueError): Predicate(('x',),'le',True)


def test_release_is_not_a_fulfilled_obligation():
    c=Clause('r','response',Predicate(('done',),'eq',True),0,120,
             active=Predicate(('active',),'eq',True),trigger=Predicate(('trigger',),'eq',True),within_seconds=60)
    r=score(c,[{'active':True,'trigger':True,'done':False},
               {'active':False,'trigger':False,'done':False},{'active':False,'trigger':False,'done':False}])
    assert r['task_success'] is None and r['clauses'][0]['status']=='released_only'


def test_missing_active_state_does_not_extend_response_deadline():
    c=Clause('r','response',Predicate(('done',),'eq',True),0,120,
             active=Predicate(('active',),'eq',True),trigger=Predicate(('trigger',),'eq',True),within_seconds=60)
    r=score(c,[{'active':True,'trigger':True,'done':False},
               {'trigger':True,'done':False},{'active':True,'trigger':True,'done':True}])
    assert r['task_success'] is None
    assert not r['clauses'][0]['pending']
