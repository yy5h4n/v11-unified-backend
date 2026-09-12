from dataclasses import asdict
import pytest
from query_construction.contracts import compile_contract


def condition():
    return dict(id='air', kind='invariant',
                goal=dict(quantity='air.co2', unit='ppm', comparator='le', target=1200),
                start_seconds=600, end_seconds=1200,
                parameter_origin='synthetic unit-test condition, not a human threshold')


def test_public_and_executable_predicates_share_exact_values():
    result = compile_contract('energyplus_iaq', [condition()], {'co2_ppm': 1300})
    assert result['public_contract']['conditions'][0]['compiled_clause'] == asdict(result['clauses'][0])
    assert not result['admitted'] and result['feasibility'] == 'not_established'


@pytest.mark.parametrize('change', [{'end_seconds': 999999}, {'start_seconds': 1},
                                    {'parameter_origin': ''}, {'secret_threshold': 1000}])
def test_reject_unsupported_or_hidden_conditions(change):
    c = condition(); c.update(change)
    with pytest.raises(ValueError): compile_contract('energyplus_iaq', [c], {'co2_ppm': 900})


def test_all_explicit_zones_compile_not_only_first():
    c = condition(); c.update(start_seconds=5400, end_seconds=6300)
    result = compile_contract('d3_energyplus_shared_ventilation', [c],
                              {'zone_a_co2_ppm': 900, 'zone_b_co2_ppm': 1500})
    assert len(result['clauses']) == 2


def test_household_zero_count_is_legal_activation():
    c = dict(id='lights', kind='invariant', start_seconds=0, end_seconds=720,
             parameter_origin='test condition',
             goal=dict(quantity='light.state', unit='category', comparator='eq', target='off'),
             active=dict(quantity='occupancy.count', unit='count', comparator='eq', target=0))
    obs = {'context': {'occupancy_count': 2}, 'devices': {'interior_lights': 'off'}}
    r = compile_contract('d0_exogenous_context', [c], obs)
    assert r['clauses'][0].active.read(obs) is False
    assert r['clauses'][0].active.read({'context': {'occupancy_count': 0}}) is True
