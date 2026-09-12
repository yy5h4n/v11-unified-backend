import json
from pathlib import Path
import pytest
from unified_compiler.observation_contracts import observation_contract, validate_public_observation
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS
from unified_compiler.llm_conversation import validate_canonical_conversation


@pytest.mark.parametrize('route', PUBLIC_ROUTE_IDS)
def test_all_recorded_native_states_have_reviewed_roots(route):
    root = Path(__file__).resolve().parents[1]
    path = root/'generated/public_receipt_audit_v2'/(route+'.json')
    if route in {'d3_citylearn_multi_system', 'd3_citylearn_multibuilding_competition'}:
        path = root/'generated/citylearn_reset_messages_v2'/(route+'.json')
    data = json.loads(path.read_text())
    assert data['passed']
    for state in validate_canonical_conversation(data['messages']):
        assert validate_public_observation(route, state) == state


def test_unknown_future_root_fails_closed():
    with pytest.raises(ValueError, match='private diagnostic'):
        validate_public_observation('energyplus_iaq', {'co2_ppm': 500, 'future_faults': [6]})


def test_private_nested_field_is_rejected_even_under_workflow_devices():
    with pytest.raises(ValueError, match='private diagnostic'):
        validate_public_observation('d1_discrete_device_fault', {'devices': {'garage_door.main': {'private_config': {'future': 5}}}})


def test_new_nested_health_field_requires_review():
    with pytest.raises(ValueError, match='unreviewed nested'):
        validate_public_observation('d1_citylearn_battery_fault', {'battery_health': {'active': False, 'next_failure_step': 7}})


def test_future_vehicle_not_exposed_before_connection():
    port = {'connected': False, 'soc': None, 'power_kw': 0, 'max_power_kw': 12,
            'scheduled_departure_step': 70, 'battery_capacity_kwh': None, 'required_departure_kwh': None}
    with pytest.raises(ValueError, match='disconnected'):
        validate_public_observation('d3_ev2gym_electric_competition', {'ports': [port, dict(port)]})


def test_all_routes_document_units_and_information_condition():
    for route in PUBLIC_ROUTE_IDS:
        c = observation_contract(route)
        assert c['root_field_patterns'] and c['semantics'] and c['information_condition']


def test_citylearn_signed_hvac_input_is_explicit_not_a_setpoint():
    from unified_compiler.native_action_conversation import ACTION_SEMANTICS
    assert 'negative requests cooling' in ACTION_SEMANTICS['d3_citylearn_multi_system']
    assert 'positive requests heating' in ACTION_SEMANTICS['d3_citylearn_multi_system']
    assert 'not a temperature setpoint' in ACTION_SEMANTICS['d3_citylearn_multi_system']
