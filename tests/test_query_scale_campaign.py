import json
from pathlib import Path

import pytest

from tools.generate_evidence_query_variants import validate as validate_variants


ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / 'generated' / 'query_construction_v3'


def load(path):
    return json.loads(path.read_text())


def test_scale_batches_are_disjoint_and_exact_size():
    first = {r['source']['id'] for r in load(GEN / 'batch50_source_inventory_v1.json')['records']}
    second = {r['source']['id'] for r in load(GEN / 'batch50_source_inventory_v2.json')['records']}
    assert len(first) == len(second) == 50
    assert not first & second


def test_scale_batches_select_only_original_development_ids():
    selected = set()
    for name in ('batch50_source_inventory_v1.json', 'batch50_source_inventory_v2.json'):
        selected.update(r['source']['id'] for r in load(GEN / name)['records'])
    allowed = set()
    for name in ('crowdre_v2/inventory.json', 'hiis_development_v2/source_inventory.json'):
        inventory = load(ROOT / 'generated' / 'query_construction_v1' / name)
        allowed.update(sid for group in inventory['partition']['groups'] if group['split'] == 'development'
                       for sid in group['record_ids'])
    assert selected <= allowed


def test_variant_validator_rejects_new_numeric_requirement():
    item = {'id': 'anchor', 'query': 'Keep both rooms warm.'}
    variants = {'item_id': 'anchor', 'variants': [
        {'id': f'anchor-p{i}', 'query': query, 'change_log': ['surface rewrite'],
         'semantic_status': 'requires_external_check'}
        for i, query in enumerate(('Keep the two rooms at 22 degrees.',
                                   'Maintain warmth in both rooms.',
                                   'Ensure both rooms stay warm.'), 1)
    ]}
    with pytest.raises(ValueError, match='numeric'):
        validate_variants(item, json.dumps(variants), 3)


def test_campaign_usage_stays_below_authorized_cap_and_keeps_unknown_separate():
    campaign = load(GEN / 'query_scale_campaign_v1.json')
    assert campaign['selected_source_records'] == 100
    assert campaign['usage']['total_tokens'] == 972587
    assert 0 < campaign['usage']['known_remaining_tokens']
    assert len(campaign['usage']['unknown_transport_events']) == 1


def test_successful_anchor_has_complete_native_trajectory_and_all_clauses_pass():
    result = load(GEN / 'core_transfer_deepseek_v4_flash_run8000_v1' / 'result.json')
    assert result['status'] == 'native_terminal_reached'
    assert result['declared_horizon_reached'] is True
    assert result['state_reconstruction_verified'] is True
    assert len(result['transitions']) == 60
    assert result['contract_evaluation']['task_success'] is True
    assert all(clause['status'] == 'passed' and len(clause['violations']) == 0
               for clause in result['contract_evaluation']['clauses'])
