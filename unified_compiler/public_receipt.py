"""Versioned, recursive public feedback projection. Raw receipts stay local."""
from collections.abc import Mapping
from copy import deepcopy
from .route_registry import PUBLIC_ROUTE_IDS
from .llm_conversation import ConversationProtocolError


def fields(names):
    return {name: None for name in names.split()}


HEALTH = fields('active availability mode step_index dropout_active gain')
BATTERY = fields('battery_capacity_kwh battery_degraded_capacity_kwh battery_soc net_electricity_kwh non_shiftable_load_kwh solar_generation_kwh storage_electricity_kwh')
TRANSFORMER = fields('id capacity_kw power_kw remaining_capacity_kw overloaded overload_kw')
POLICIES = {route: {} for route in PUBLIC_ROUTE_IDS}
POLICIES.update({
    'd0_exogenous_context': {'transition': {'step': None, 'events': [{
        **fields('event_type operation source step target'),
        'payload': fields('count status source state temperature_c humidity_pct key value device_id'),
    }]}},
    'd1_citylearn_battery_fault': {'effect': BATTERY},
    'd1_sustaingym_fault': {'d1': {'requested_action': [], 'effective_action': [],
        'fault': HEALTH, 'sensor_fault': fields('active mode')}},
    'd1_ev2gym_fault': {'d1_fault': {
        **fields('effective_charge_power_kw requested_charge_power_kw'), 'health': HEALTH,
        'requested_action': fields('type kw')}, 'effect': fields(
        'charge_cost_eur charged_energy_kwh delivered_charging_kwh departure_soc household_total_power_kw overloaded vehicle_soc_after vehicle_soc_before')},
    'd1_discrete_device_fault': {**fields('accepted error_code execution_status'), 'feedback': {
        **fields('status action_cost cost_unit rule_id'), 'wait': fields('mode'),
        'applied': []}},
    'd3_citylearn_multi_system': {'effect': {**BATTERY, **fields(
        'dhw_electricity_kwh hvac_electricity_kwh indoor_temperature_c net_energy_balance_error_kwh')}},
    'd3_ev2gym_electric_competition': {'effect': {
        **fields('transformer_power_kw remaining_capacity_kw overloaded overload_kw'),
        'port_power_kw': [], 'before': TRANSFORMER,
        'departures': [fields('port observed_at_step final_soc final_energy_kwh required_departure_kwh')]}},
})


def project(value, policy):
    if policy is None:
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ConversationProtocolError('public scalar feedback has unexpected nested data')
        return deepcopy(value)
    if isinstance(policy, list):
        if not isinstance(value, (list, tuple)):
            raise ConversationProtocolError('public feedback array required')
        return [project(v, policy[0] if policy else None) for v in value]
    if not isinstance(value, Mapping):
        raise ConversationProtocolError('public feedback object required')
    return {key: project(value[key], shape) for key, shape in policy.items() if key in value}


def public_native_result(route_id, receipt):
    if route_id not in POLICIES:
        raise ConversationProtocolError('unknown route for public receipt policy')
    result = project(receipt, fields('time_seconds delta_t_seconds done terminated truncated'))
    result['info'] = project(receipt.get('info', {}), POLICIES[route_id])
    return result
