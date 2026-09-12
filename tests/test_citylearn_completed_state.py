import pytest
from unified_compiler.agent_interface import make_agent_backend


@pytest.mark.parametrize('route_id', ['d3_citylearn_multi_system', 'd3_citylearn_multibuilding_competition'])
def test_completed_physical_state_not_unwritten_next_row(route_id):
    route = make_agent_backend(route_id)
    try:
        initial = route.reset(seed=0)
        native = route.route
        for building in native.env.buildings:
            prefix = building.name + '.' if route_id.endswith('multibuilding_competition') else ''
            last = native.env.time_step - 1 if native.warmup_steps else native.env.time_step
            assert initial['observation'][prefix+'indoor_dry_bulb_temperature'] == float(building.indoor_dry_bulb_temperature[last])
            assert initial['observation'][prefix+'electrical_storage_soc'] == float(building.electrical_storage.soc[last])
        for rate in [0., 1., -.25, .5]:
            index = native.env.time_step
            action = {'battery_rate': rate, 'hvac_rate': 0.}
            if route_id.endswith('multibuilding_competition'):
                action = {b: dict(action) for b in native.building_ids}
            receipt = route.step(action)
            for building in native.env.buildings:
                prefix = building.name + '.' if route_id.endswith('multibuilding_competition') else ''
                obs = receipt['observation']
                assert obs[prefix+'electrical_storage_soc'] == float(building.electrical_storage.soc[index])
                assert obs[prefix+'indoor_dry_bulb_temperature'] == float(building.indoor_dry_bulb_temperature[index])
                assert obs[prefix+'net_electricity_consumption'] == float(building.net_electricity_consumption[index])
            assert route.observe() == receipt['observation']
            if route_id.endswith('multibuilding_competition'):
                assert receipt['observation']['district_net_kwh'] == pytest.approx(sum(
                    receipt['observation'][b+'.net_electricity_consumption'] for b in native.building_ids))
    finally:
        route.close()
