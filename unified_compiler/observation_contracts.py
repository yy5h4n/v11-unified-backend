"""Public field semantics. These describe interface meaning, not task targets."""
from copy import deepcopy
from fnmatch import fnmatchcase
from collections.abc import Mapping
from .llm_conversation import ConversationProtocolError, canonical_json
from .route_registry import PUBLIC_ROUTE_IDS


ROOT_FIELDS = {
    'd0_exogenous_context': 'context devices events step terminal time_seconds',
    'd1_citylearn_battery_fault': 'battery_health battery_soc non_shiftable_load_kwh solar_generation_kwh source_row',
    'd1_discrete_device_fault': 'active_device_faults active_rule_ids devices episode_id events household inventory local_time metrics public_context semantic_observations step terminal_reason tick_seconds time workflow',
    'd1_ev2gym_fault': 'charge_price_eur_per_kwh charger_max_power_kw declared_departure_time household_load_kw household_power_limit_kw required_departure_soc time vehicle_connected vehicle_soc',
    'd1_sustaingym_fault': 'actuator_health clock episode_done occupancy sensor_health step_index weather zone_temperatures_c',
    'd3_citylearn_multi_system': 'cooling_demand cooling_electricity_consumption electrical_storage_electricity_consumption electrical_storage_soc heating_demand heating_electricity_consumption hour indoor_dry_bulb_temperature indoor_dry_bulb_temperature_cooling_set_point indoor_dry_bulb_temperature_heating_set_point month net_electricity_consumption non_shiftable_load occupant_count outdoor_dry_bulb_temperature solar_generation',
    'd3_citylearn_multibuilding_competition': 'district_net_kwh district_peak_kwh shared_meter_headroom_kwh *.electrical_storage_soc *.feasible_headroom_kwh *.hour *.indoor_dry_bulb_temperature *.net_electricity_consumption *.occupant_count *.outdoor_dry_bulb_temperature',
    'd3_energyplus_shared_ventilation': 'zone_a_actual_airflow_m3_s zone_a_co2_ppm zone_a_relative_humidity_pct zone_a_temperature_c zone_b_actual_airflow_m3_s zone_b_co2_ppm zone_b_relative_humidity_pct zone_b_temperature_c',
    'd3_ev2gym_electric_competition': 'native_observation_mask ports time_seconds time_step transformer',
    'd3_modelica_shared_heat': 'allocated_dhw_heat_w allocated_space_heat_w dhw_temperature_c room_a_temperature_c room_b_temperature_c service_shortfall_w shared_heat_pump_capacity_used_w',
    'd3_wntr_water_competition': 'flow_laundry_m3_s flow_shower_m3_s flow_source_fill_m3_s pressure_laundry_m pressure_shower_m served_laundry_m3_s served_shower_m3_s tank_level_m',
    'energyplus_iaq': 'co2_ppm generic_contaminant_ppm relative_humidity_pct zone_temperature_c',
    'fds_smoke_fire': 'room_b_temperature_c room_b_velocity_mps room_b_visibility_m',
    'modelica_buildings_aixlib': 'heater_heat_flow_w room_a_relative_humidity_pct room_a_temperature_c room_b_relative_humidity_pct room_b_temperature_c',
    'wntr_residential_water': 'flow_house_isolation_m3_s flow_source_fill_m3_s leak_bathroom_m3_s leak_kitchen_m3_s leak_total_m3_s pressure_bathroom_m pressure_kitchen_m tank_level_m',
}

NOTES = {
    'd0_exogenous_context': 'context contains current categorical household/weather state and occupancy count. devices contains current door/light state. events are already delivered events, not a future schedule. step counts 60-second ticks.',
    'd1_discrete_device_fault': 'devices/household/workflow describe current discrete state; *_seconds are durations, *_percent and inventory level are percentages/units as named. time/local_time are timestamps. public_context explicitly supplies user/service constraints (deadlines, allowlists, SLAs), not secret evaluator answers. Projected stockout and completion times are predictions from public rates/settings, not guaranteed future events. Current fault status is observable by design. metrics are native action units/runtime, not task scores. inventory is the public device interface, not a plan.',
    'd1_citylearn_battery_fault': 'battery_soc is a fraction 0..1 at the last completed native transition. *_kwh are energies; source_row/load/solar are the next interval inputs. battery_health is current explicitly observed device health, not future failures. Do not interpret SOC as percent 0..100.',
    'd1_ev2gym_fault': 'vehicle_soc/required_departure_soc are fractions 0..1. *_kw are instantaneous powers, charge_price_eur_per_kwh is EUR per kWh. time and declared_departure_time are public local times; departure and target are explicitly declared service conditions. vehicle_connected is current presence. Power availability can change with current charger health.',
    'd1_sustaingym_fault': 'zone_temperatures_c and weather temperatures are Celsius. Irradiance is the native normalized feature, not W/m2. occupancy.occupancy_power_kw is the native signed sensible-heat model output in kW and can be negative; it is not a people count or a presence sensor. Current actuator_health/sensor_health is explicitly exposed. clock seconds are episode elapsed time. All environmental readings retain native values; no clipping to comfortable ranges.',
    'd3_citylearn_multi_system': 'electrical_storage_soc is a 0..1 fraction; temperatures/setpoints are Celsius. Demand and electricity observations are interval kWh; occupant_count is a count, hour/month are calendar inputs. Controlled SOC/room temperature and energy reflect the completed native interval; weather/load/setpoints are upcoming decision inputs. Two action channels alone do not establish resource competition.',
    'd3_citylearn_multibuilding_competition': 'Keys prefixed with building IDs are independent building measurements. SOC is a fraction, temperature is Celsius, energy/headroom is interval kWh. Shared weather/time may appear only once. district_net_kwh equals completed building net energy summed. feasible_headroom_kwh is external meter budget minus OTHER buildings, not allocated power. district_peak_kwh is this episode peak, excluding warm-up. Reset observations can contain last warm-up output; do not charge it to episode actions.',
    'd3_energyplus_shared_ventilation': 'CO2 is ppm, relative humidity is percent 0..100, temperatures are Celsius. actual_airflow_m3_s is delivered volumetric airflow, not the requested fraction. States come from the completed native EnergyPlus step.',
    'd3_ev2gym_electric_competition': 'ports are ordered charger 0/1. SOC is 0..1, battery/required energy is kWh, power/capacity/headroom is kW. scheduled_departure_step is a disclosed departure commitment for a currently connected vehicle; future arrivals and disconnected vehicles are not disclosed. Null is unavailable/not connected, not zero. Native observation mask is not an action mask. Negative transformer headroom means shared overload; it does not imply automatic native clipping.',
    'd3_modelica_shared_heat': 'Temperatures are Celsius; heat flow/allocation/shortfall is W, not energy. capacity_used_w equals delivered space plus DHW heat. Native Modelica allocation is proportional under over-request; delivered heat is not equal to requested fraction. States evolve through native FMI substeps.',
    'd3_wntr_water_competition': 'pressure_*_m is metres of water head, not pascals. flow/served_*_m3_s is volumetric flow in m3/s; tank_level_m is metres. Signed pipe flow indicates native direction. served_* is a native emitter/leak proxy in this prototype, not yet calibrated household demand satisfaction. Coupling can emerge after more than one step.',
    'energyplus_iaq': 'CO2 and generic contaminant fields are ppm; relative humidity is percent; zone temperature is Celsius. Generic contaminant is the configured simulated tracer, not a medically calibrated hazard. These are completed native EnergyPlus states.',
    'fds_smoke_fire': 'Room B temperature is Celsius, velocity is m/s, visibility is metres. Values come from native FDS full-history prefix replay; it is not persistent online continuation. Thermal action effects are demonstrated; visibility sensitivity is not certified. Constant visibility alone does not prove a safe evacuation task.',
    'modelica_buildings_aixlib': 'Room temperatures are Celsius, relative humidity is percent, heater_heat_flow_w is W. These are native FMI outputs; heat flow is instantaneous rate, not cumulative energy.',
    'wntr_residential_water': 'pressure_*_m is metres of water head; tank_level_m is metres. All flow/leak values are m3/s; signed pipe flow encodes direction. leak_total_m3_s is bathroom plus kitchen leakage. Low pressure or closed valves does not freeze tank dynamics.',
}

assert set(ROOT_FIELDS) == set(NOTES) == set(PUBLIC_ROUTE_IDS)

NESTED_FIELDS = {
    'd0_exogenous_context': {'context': 'occupancy_count occupancy_status time_context weather', 'devices': 'front_door interior_lights'},
    'd1_citylearn_battery_fault': {'battery_health': 'active factor mode step_index'},
    'd1_sustaingym_fault': {
        'clock': 'elapsed_seconds epoch_index hour_of_year seconds_per_step',
        'weather': 'outdoor_temperature_c ground_temperature_c global_horizontal_irradiance_normalized',
        'occupancy': 'occupancy_power_kw',
        'actuator_health': 'active mode step_index gain dropout_active',
        'sensor_health': 'active mode variable correction'},
    'd3_ev2gym_electric_competition': {'transformer': 'id power_kw capacity_kw remaining_capacity_kw overloaded overload_kw'},
}
PRIVATE_KEYS = {'private_state', 'private_config', 'future_faults', 'fault_schedule', 'sensor_schedule',
                'reference_policy', 'oracle', 'evaluator', 'native_reward', 'reward_breakdown', 'total_reward'}


def _reject_private_keys(value):
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key.lower() in PRIVATE_KEYS:
                raise ConversationProtocolError('private diagnostic field in public observation: '+key)
            _reject_private_keys(child)
    elif isinstance(value, list):
        for child in value:
            _reject_private_keys(child)


def observation_contract(route_id):
    if route_id not in ROOT_FIELDS:
        raise ConversationProtocolError('unknown public observation contract')
    return {'version': 'public-observation-semantics-v1',
            'root_field_patterns': ROOT_FIELDS[route_id].split(),
            'semantics': NOTES[route_id],
            'information_condition': 'Current device health and explicitly disclosed user/service schedules are public; hidden event/fault schedules and evaluator labels are not. Missing optional state is unknown, not zero.',
            'validation_scope': 'Root-field and selected nested-structure checks; not a universal physics or noninterference proof.'}


def validate_public_observation(route_id, observation):
    contract = observation_contract(route_id)
    if not isinstance(observation, Mapping):
        raise ConversationProtocolError('public observation must be an object')
    canonical_json(observation)
    _reject_private_keys(observation)
    unknown = [k for k in observation if not any(fnmatchcase(k, p) for p in contract['root_field_patterns'])]
    if unknown:
        raise ConversationProtocolError('unreviewed public observation root fields: '+', '.join(unknown))
    for root, names in NESTED_FIELDS.get(route_id, {}).items():
        if root not in observation:
            continue
        value = observation[root]
        if not isinstance(value, Mapping) or set(value) - set(names.split()):
            raise ConversationProtocolError('unreviewed nested public observation fields at '+root)
    if route_id == 'd3_ev2gym_electric_competition' and 'ports' in observation:
        if not isinstance(observation['ports'], list) or len(observation['ports']) != 2:
            raise ConversationProtocolError('two public EV ports required')
        allowed = {'connected', 'soc', 'power_kw', 'max_power_kw', 'scheduled_departure_step', 'battery_capacity_kwh', 'required_departure_kwh'}
        for port in observation['ports']:
            if not isinstance(port, Mapping) or set(port) != allowed:
                raise ConversationProtocolError('unreviewed or missing EV port fields')
            if port['connected'] is False and any(port[k] is not None for k in ('soc', 'scheduled_departure_step', 'battery_capacity_kwh', 'required_departure_kwh')):
                raise ConversationProtocolError('disconnected EV exposes unavailable vehicle state or future schedule')
    return deepcopy(observation)
