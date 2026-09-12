from copy import deepcopy
import pytest
from backend_casebook_v0.case_review import REVIEWS, review_for
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
from tools.run_backend_casebook_pilot_v1 import evaluate


def test_review_never_promotes_backend_success_to_task_admission():
    assert set(REVIEWS) == set(PUBLIC_ROUTE_IDS) | {'boptest'}
    for route in REVIEWS:
        r = review_for(route)
        assert r['admission_ready'] is False and r['task_success'] is None
        assert r['unresolved'] and r['provenance'].endswith('not_human_grounded')


def trace():
    obs = {'zone_a_co2_ppm': 1000., 'zone_b_co2_ppm': 1000.,
           'zone_a_temperature_c': 20., 'zone_b_temperature_c': 20.}
    return {'ok': True, 'initial_observation': deepcopy(obs), 'frames': [
        {'receipt': {'time_seconds': (i+1)*900, 'observation': deepcopy(obs)}} for i in range(23)]}


@pytest.mark.parametrize('damage', [None, 'short', 'missing_step', 'no_clock', 'nan', 'initial_unsafe', 'deadline', 'late_recovery'])
def test_ventilation_evaluation_requires_whole_native_trajectory(damage):
    run = trace()
    if damage == 'short': run['frames'] = run['frames'][:6]
    if damage == 'missing_step': del run['frames'][7]
    if damage == 'no_clock': del run['frames'][0]['receipt']['time_seconds']
    if damage == 'nan': run['frames'][0]['receipt']['observation']['zone_a_co2_ppm'] = float('nan')
    if damage == 'initial_unsafe': run['initial_observation']['zone_a_temperature_c'] = 40.
    if damage in {'deadline', 'late_recovery'}:
        run['frames'][5 if damage == 'deadline' else 12]['receipt']['observation']['zone_b_co2_ppm'] = 1400.
    result = evaluate({'route_id': 'd3_energyplus_shared_ventilation'}, run)
    assert result['pass'] is (damage is None)
    if damage in {'short', 'missing_step', 'no_clock', 'nan'}:
        assert result['evaluated'] is False and result['task_success'] is None


def test_initial_stale_air_may_recover_before_disclosed_deadline():
    run = trace()
    run['initial_observation']['zone_a_co2_ppm'] = 1800.
    run['frames'][4]['receipt']['observation']['zone_a_co2_ppm'] = 1500.
    assert evaluate({'route_id': 'd3_energyplus_shared_ventilation'}, run)['pass']
