import pytest
import json
from query_construction.mapping import compile_mapping, mapping_messages, parse_mapping_response
from unified_compiler.route_registry import PUBLIC_ROUTE_IDS


def proposal():
    return {'decision': 'candidate', 'atoms': [{'responsibility': 'air'}, {'responsibility': 'notify'}]}


def test_fence_is_only_syntax_wrapper_not_content_repair():
    assert parse_mapping_response('```json\n[]\n```') == ([], 'single_json_fence')
    assert parse_mapping_response('[]') == ([], 'none')
    for bad in ('Here is JSON: []', '```json\n[]\n``` extra', '[{"x":1,"x":2}]', '[NaN]'):
        with pytest.raises(ValueError): parse_mapping_response(bad)


def test_mapping_packet_covers_every_route_without_changing_query():
    p = proposal(); p['query'] = 'Keep the air fresh and notify me.'
    for route in PUBLIC_ROUTE_IDS:
        packet = mapping_messages(p, route, {}, {'example': 'synthetic'})
        data = json.loads(packet[1]['content'])
        assert data['query'] == p['query'] and data['atoms'] == p['atoms']
        assert data['route_id'] == route and data['quantities']
        assert data['legal_actions'] == {'example': 'synthetic'}


def test_arrival_mapping_guidance_discloses_observable_coverage():
    p = {'query': 'When someone enters, turn on the lights.',
         'atoms': [{'responsibility': 'arrival lighting'}]}
    packet = mapping_messages(p, 'd0_exogenous_context', {}, {})
    assert 'does not specify occupancy.count == 1' in packet[0]['content']
    assert 'empty-to-occupied arrival ONLY' in packet[0]['content']
    assert json.loads(packet[1]['content'])['query'] == p['query']


def unsupported(index):
    return {'atom_index': index, 'status': 'unsupported', 'reason': 'No verified interface', 'conditions': []}


def test_omission_cannot_be_silent():
    with pytest.raises(ValueError, match='every atom'):
        compile_mapping(proposal(), 'energyplus_iaq', [unsupported(0)], {'co2_ppm': 900})


def test_unsupported_atom_rejects_whole_query():
    result = compile_mapping(proposal(), 'energyplus_iaq', [unsupported(0), unsupported(1)], {})
    assert not result['compiled'] and not result['admitted']
    assert len(result['unsupported_atoms']) == 2


def test_mapped_without_conditions_and_duplicate_indices_rejected():
    a = unsupported(0); a['status'] = 'mapped'
    with pytest.raises(ValueError): compile_mapping(proposal(), 'energyplus_iaq', [a, unsupported(1)], {})
    with pytest.raises(ValueError): compile_mapping(proposal(), 'energyplus_iaq', [unsupported(0), unsupported(0)], {})


def test_success_compiles_public_conditions_without_admission():
    p = {'decision': 'candidate', 'atoms': [{'responsibility': 'air'}]}
    condition = dict(id='ceiling', kind='invariant', start_seconds=600, end_seconds=1200,
                     parameter_origin='synthetic test operationalization',
                     goal=dict(quantity='air.co2', unit='ppm', comparator='le', target=1200))
    assignment = dict(atom_index=0, status='mapped', reason='Explicit CO2 test quantity', conditions=[condition])
    result = compile_mapping(p, 'energyplus_iaq', [assignment], {'co2_ppm': 900})
    assert result['compiled'] and not result['admitted']
    assert result['contract']['clauses'][0].id == 'atom0/ceiling:0'
    assert condition['id'] == 'ceiling'


def test_conditional_invariant_cannot_silently_default_to_zero_latency():
    p = {'decision': 'candidate', 'atoms': [{'responsibility': 'light off while away'}]}
    c = dict(id='light', kind='invariant', start_seconds=0, end_seconds=720,
             parameter_origin='test',
             goal=dict(quantity='light.state', unit='category', comparator='eq', target='off'),
             active=dict(quantity='occupancy.count', unit='count', comparator='eq', target=0))
    a = dict(atom_index=0, status='mapped', reason='test', conditions=[c])
    obs = {'context': {'occupancy_count': 2}, 'devices': {'interior_lights': 'off'}}
    with pytest.raises(ValueError, match='explicit activation'):
        compile_mapping(p, 'd0_exogenous_context', [a], obs)
    c['activation_grace_seconds'] = 60
    assert compile_mapping(p, 'd0_exogenous_context', [a], obs)['compiled']
