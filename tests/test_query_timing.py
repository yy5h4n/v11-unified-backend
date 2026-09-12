import pytest
from query_construction.timing import check_response_budget, audit_reactive_contract


def check(deadline, commands=1, parallel=1):
    return check_response_budget(deadline_seconds=deadline, command_count=commands,
                                 commands_per_interval=parallel, cadence_seconds=60,
                                 evidence='synthetic test: one discrete command per interval')


def test_no_instant_response_and_no_silent_rounding():
    assert not check(0)['timing_compatible']
    assert not check(59)['timing_compatible']
    assert check(60)['timing_compatible']


def test_multiple_commands_use_actual_parallel_capacity():
    assert not check(60, commands=2)['timing_compatible']
    assert check(120, commands=2)['timing_compatible']
    assert check(60, commands=2, parallel=2)['timing_compatible']


def test_timing_pass_does_not_prove_feasibility():
    r = check(120)
    assert not r['admitted'] and r['feasibility'] == 'not_established'
    with pytest.raises(ValueError): check(60, commands=True)


def test_actual_public_contract_zero_grace_is_flagged_not_rewritten():
    from query_construction.contracts import compile_contract
    c = dict(id='lights', kind='invariant', start_seconds=0, end_seconds=720,
             parameter_origin='test', activation_grace_seconds=0,
             goal=dict(quantity='light.state', unit='category', comparator='eq', target='off'),
             active=dict(quantity='occupancy.count', unit='count', comparator='eq', target=0))
    obs = {'context': {'occupancy_count': 2}, 'devices': {'interior_lights': 'off'}}
    public = compile_contract('d0_exogenous_context', [c], obs)['public_contract']
    audit = audit_reactive_contract(public)
    assert audit['reactive_deadline_rejected'] and not audit['admitted']
    assert public['conditions'][0]['compiled_clause']['activation_grace_seconds'] == 0
