"""Backend facts for construction, not a table of hand-authored queries.

Unit and mechanism distinctions are intentional: measured flow is not a
calibrated completed service, SOC is not energy, and budget is not allocation.
"""
from dataclasses import dataclass
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS, route_metadata


@dataclass(frozen=True)
class Quantity:
    unit: str
    paths: tuple
    allowed_values: tuple | None = None


def q(unit, *paths, allowed_values=None):
    return Quantity(unit, tuple(tuple(p.split('/')) for p in paths), allowed_values)


CARDS = {
 'd0_exogenous_context': ({'exogenous_context'}, {
     'occupancy.count':q('count','context/occupancy_count'), 'door.state':q('category','devices/front_door'),
     'light.state':q('category','devices/interior_lights', allowed_values=('on', 'off'))}, 'One device command per native interval; no future schedule access. Interior lights only have on/off states, not brightness or colour.'),
 'd1_sustaingym_fault': ({'actuator_fault','continuous_dynamics'}, {
     'room.temperature':q('degC','zone_temperatures_c/*')}, 'Cooling scenario feasibility must be checked; occupancy heat gain is not a people sensor.'),
 'd1_citylearn_battery_fault': ({'actuator_fault','continuous_dynamics'}, {
     'battery.soc':q('fraction','battery_soc')}, 'SOC alone does not provide usable reserve kWh under changing capacity.'),
 'd1_ev2gym_fault': ({'actuator_fault','continuous_dynamics'}, {
     'vehicle.soc':q('fraction','vehicle_soc'), 'vehicle.connected':q('boolean','vehicle_connected'),
     'electricity.price':q('EUR/kWh','charge_price_eur_per_kwh'),
     'electricity.household_limit':q('kW','household_power_limit_kw')}, 'Departure must be evaluated from actual terminal event feedback; SOC samples alone are insufficient.'),
 'd1_discrete_device_fault': ({'actuator_fault','exogenous_context'}, {
     'garage.state':q('category','devices/garage_door.main/state')}, 'Current garage state is not evidence of a parking trigger; binding must disclose task scope.'),
 'energyplus_iaq': ({'continuous_dynamics'}, {
     'air.co2':q('ppm','co2_ppm'), 'room.temperature':q('degC','zone_temperature_c'),
     'room.humidity':q('percent','relative_humidity_pct')}, 'No assumed occupancy window; configured tracer is not a calibrated health hazard.'),
 'wntr_residential_water': ({'continuous_dynamics'}, {
     'water.pressure':q('m_water_head','pressure_bathroom_m','pressure_kitchen_m'),
     'water.leak_rate':q('m3/s','leak_total_m3_s'), 'water.tank_level':q('m','tank_level_m')}, 'Household demand/service calibration is not established.'),
 'fds_smoke_fire': ({'continuous_dynamics'}, {
     'room.temperature':q('degC','room_b_temperature_c'), 'air.visibility':q('m','room_b_visibility_m')}, 'Prefix replay only; measured visibility is not proof of an effective smoke-control task.'),
 'modelica_buildings_aixlib': ({'continuous_dynamics'}, {
     'room.temperature':q('degC','room_a_temperature_c','room_b_temperature_c'),
     'heat.delivery_rate':q('W','heater_heat_flow_w')}, 'One hour episode; cannot certify overnight coverage.'),
 'd3_citylearn_multi_system': ({'continuous_dynamics'}, {
     'room.temperature':q('degC','indoor_dry_bulb_temperature'), 'battery.soc':q('fraction','electrical_storage_soc'),
     'electricity.net_energy':q('kWh/interval','net_electricity_consumption')}, 'No verified cross-channel resource competition despite D3 route name.'),
 'd3_citylearn_multibuilding_competition': ({'continuous_dynamics','shared_budget'}, {
     'room.temperature':q('degC','*.indoor_dry_bulb_temperature'),
     'electricity.net_energy':q('kWh/interval','district_net_kwh'),
     'electricity.budget_headroom':q('kWh/interval','shared_meter_headroom_kwh')}, 'External shared-meter budget, not native power clipping; interval energy is not kW.'),
 'd3_wntr_water_competition': ({'continuous_dynamics','delayed_hydraulic_coupling'}, {
     'water.pressure':q('m_water_head','pressure_shower_m','pressure_laundry_m'),
     'water.flow_proxy':q('m3/s','served_shower_m3_s','served_laundry_m3_s'),
     'water.tank_level':q('m','tank_level_m')}, 'Coupling is observed after delay; emitter proxies are not calibrated shower/laundry completion.'),
 'd3_modelica_shared_heat': ({'continuous_dynamics','physical_allocation'}, {
     'room.temperature':q('degC','room_a_temperature_c','room_b_temperature_c'),
     'hot_water.temperature':q('degC','dhw_temperature_c'),
     'heat.delivery_rate':q('W','allocated_space_heat_w','allocated_dhw_heat_w')}, 'Thermal allocation is physical; temperature alone is not arbitrary delivered hot-water volume.'),
 'd3_ev2gym_electric_competition': ({'continuous_dynamics','shared_budget'}, {
     'vehicle.soc':q('fraction','ports/*/soc'),
     'electricity.transformer_power':q('kW','transformer/power_kw'),
     'electricity.transformer_headroom':q('kW','transformer/remaining_capacity_kw')}, 'Shared transformer constraint, no automatic native allocation; use departure receipts for per-vehicle delivery.'),
 'd3_energyplus_shared_ventilation': ({'continuous_dynamics','physical_allocation'}, {
     'air.co2':q('ppm','zone_a_co2_ppm','zone_b_co2_ppm'),
     'room.temperature':q('degC','zone_a_temperature_c','zone_b_temperature_c'),
     'air.flow_rate':q('m3/s','zone_a_actual_airflow_m3_s','zone_b_actual_airflow_m3_s')}, 'Recovery deadline and whole-window feasibility are separate task checks.'),
}

assert set(CARDS) == set(PUBLIC_ROUTE_IDS)


def match(required_quantities, required_mechanisms=()):
    """Return every route including rejections; no semantic inference from words.

    Candidates require native scenario/control feasibility and evidence checks
    afterwards. Measurability alone is not controllability or task validity.
    """
    if not isinstance(required_quantities, dict) or not required_quantities:
        raise ValueError('explicit nonempty quantity/unit requirements needed')
    if any(not isinstance(k,str) or not isinstance(v,str) or not k or not v for k,v in required_quantities.items()):
        raise ValueError('quantity and unit names must be nonempty strings')
    if isinstance(required_mechanisms, str): raise ValueError('mechanisms must be a collection, not a string')
    rows=[]
    for route in PUBLIC_ROUTE_IDS:
        mechanisms, quantities, limit = CARDS[route]; reasons=[]
        for name, unit in required_quantities.items():
            if name not in quantities: reasons.append({'code':'quantity_unsupported','quantity':name})
            elif quantities[name].unit != unit:
                reasons.append({'code':'unit_mismatch','quantity':name,'requested':unit,'native':quantities[name].unit})
        for mechanism in sorted(set(required_mechanisms)-mechanisms):
            reasons.append({'code':'mechanism_not_verified','mechanism':mechanism})
        rows.append({'route_id':route,'candidate':not reasons,'reasons':reasons,'limit':limit,
                     'cadence_seconds':route_metadata(route)['cadence_seconds'],
                     'horizon_seconds':route_metadata(route)['horizon_seconds'],
                     'admitted':False})
    return rows
