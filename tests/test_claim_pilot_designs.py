from copy import deepcopy
import pytest

from backend_casebook_v0.claim_designs import DESIGNS
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
from tools.run_backend_casebook_llm_pilot import parse_action
from tools.run_backend_casebook_pilot_v1 import evaluate


def test_all_backends_remain_visible_once_including_unavailable_candidate():
    routes = [d['route'] for d in DESIGNS]
    assert len(routes) == len(set(routes))
    assert set(routes) == set(PUBLIC_ROUTE_IDS) | {'boptest'}
    for d in DESIGNS:
        assert all(d[k] for k in ['query','claim','bind','outcome','contrast','evidence','missing','cost'])


@pytest.mark.parametrize('raw', ['{"action":NaN}', '{"action":1e999}', '{"action":0,"action":1}', 'prose {"action":0}'])
def test_ambiguous_or_nonfinite_responses_are_rejected(raw):
    with pytest.raises(ValueError): parse_action(raw)


def test_late_recovery_does_not_erase_intermediate_air_quality_violation():
    obs = {'zone_a_co2_ppm': 1000., 'zone_b_co2_ppm': 1000.,
           'zone_a_temperature_c': 20., 'zone_b_temperature_c': 20.}
    run = {'ok':True,'initial_observation':deepcopy(obs), 'frames':[
        {'receipt':{'observation':deepcopy(obs), 'time_seconds': (i+1)*900}} for i in range(23)]}
    run['frames'][5]['receipt']['observation']['zone_b_co2_ppm']=1500.
    result=evaluate({'route_id':'d3_energyplus_shared_ventilation'},run)
    assert not result['pass']
    assert result['details']['co2_violation_steps_after_90_minutes']==[5]
