from query_construction.capabilities import CARDS, match
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS


def candidates(quantities, mechanisms=()):
    return {r['route_id'] for r in match(quantities, mechanisms) if r['candidate']}


def test_all_routes_reported_even_when_none_match():
    rows=match({'laundry.service_completed':'boolean'})
    assert set(CARDS)==set(PUBLIC_ROUTE_IDS)=={r['route_id'] for r in rows}
    assert not any(r['candidate'] or r['admitted'] for r in rows)


def test_same_atomic_air_requirement_can_bind_multiple_backends():
    assert candidates({'air.co2':'ppm'})=={'energyplus_iaq','d3_energyplus_shared_ventilation'}
    assert candidates({'air.co2':'ppm'},['physical_allocation'])=={'d3_energyplus_shared_ventilation'}


def test_route_name_does_not_prove_d3():
    assert 'd3_citylearn_multi_system' not in candidates({'room.temperature':'degC'},['physical_allocation'])
    assert 'd3_citylearn_multibuilding_competition' not in candidates({'room.temperature':'degC'},['physical_allocation'])


def test_units_are_not_silently_converted():
    assert not candidates({'battery.soc':'percent'})
    assert not candidates({'electricity.budget_headroom':'kW'})
    assert candidates({'electricity.budget_headroom':'kWh/interval'})=={'d3_citylearn_multibuilding_competition'}


def test_physical_proxies_do_not_become_household_services():
    assert not candidates({'battery.reserve_energy':'kWh'})
    assert not candidates({'shower.delivered_water':'m3'})
    assert not candidates({'occupancy.count':'count'},['actuator_fault','continuous_dynamics'])
