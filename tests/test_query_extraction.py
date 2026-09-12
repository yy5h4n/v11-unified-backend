import json
import pytest
from query_construction.sources import SourceRecord
from query_construction.extraction import messages, validate_proposal, locate_quote


def fixture():
    source = SourceRecord('s', 'test', 'fixture', '洗完衣服时提醒我。', 'p', 'synthetic_test', 'test')
    proposal = dict(source_id='s', decision='candidate', reason='Recurring condition.',
                    atoms=[dict(kind='respond', responsibility='Notify after laundry finishes.',
                                evidence=[dict(source_id='s', quote=source.text)])],
                    query='Let me know whenever the washing machine finishes.', assumptions=[])
    return source, proposal


def test_valid_span_is_not_semantic_or_admission_certificate():
    source, proposal = fixture()
    result = validate_proposal(source, json.dumps(proposal))
    assert result['structurally_valid'] and not result['admitted']
    assert result['semantic_entailment'] == 'not_established'
    assert result['proposal']['atoms'][0]['evidence'][0]['end'] == len(source.text)


def test_ambiguous_quote_rejected_and_unicode_offsets_computed():
    source = SourceRecord('s', 'test', 'fixture', '🙂开灯，然后开灯。', 'p', 'synthetic_test', 'test')
    with pytest.raises(ValueError, match='ambiguous'):
        locate_quote(source, {'source_id': 's', 'quote': '开灯'})
    assert locate_quote(source, {'source_id': 's', 'quote': '然后开灯'})['start'] == 4


@pytest.mark.parametrize('mutation', ['quote', 'source', 'unknown_field', 'empty_atoms'])
def test_malformed_proposal_rejected(mutation):
    source, p = fixture()
    if mutation == 'quote': p['atoms'][0]['evidence'][0]['quote'] = 'invented'
    if mutation == 'source': p['source_id'] = 'other'
    if mutation == 'unknown_field': p['human_validated'] = True
    if mutation == 'empty_atoms': p['atoms'] = []
    with pytest.raises(ValueError): validate_proposal(source, json.dumps(p))


def test_duplicate_keys_rejected():
    source, p = fixture()
    raw = json.dumps(p)
    with pytest.raises(ValueError):
        validate_proposal(source, raw[:-1] + ', "decision": "reject"}')


def test_source_stays_data_and_prompt_has_no_route_inventory():
    source, _ = fixture()
    packet = messages(source)
    assert json.loads(packet[1]['content'])['source_text'] == source.text
    assert 'd3_' not in packet[0]['content']


def test_reject_and_added_assumption_remain_visible():
    source, p = fixture()
    p['assumptions'] = ['Changed one-shot scope to recurring.']
    assert validate_proposal(source, json.dumps(p))['needs_assumption_review']
    p.update(decision='reject', atoms=[], query=None)
    assert not validate_proposal(source, json.dumps(p))['admitted']
