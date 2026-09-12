from types import SimpleNamespace
from query_construction.semantic_checks import surface_review


def test_complaint_flags_both_bad_and_good_rephrase_without_certifying_either():
    source = SimpleNamespace(text='Lights flash, inconveniencing people trying to sleep.', collection_kind='study')
    for query in ('Keep flashing the lights.', 'Avoid disturbing sleep with flashing lights.'):
        result = surface_review(source, {'decision': 'candidate', 'query': query,
                                        'atoms': [], 'assumptions': []})
        assert any(r['code'] == 'reported_behavior_may_be_unwanted_review_intent_polarity' for r in result['issues'])
        assert not result['admitted']


def test_advice_question_is_not_a_direct_delegation():
    source = SimpleNamespace(text='Lights disturb sleep.', collection_kind='study')
    result = surface_review(source, {'decision': 'candidate',
        'query': 'How can I stop these lights?', 'atoms': [], 'assumptions': []})
    assert any(r['code'] == 'query_asks_for_advice_not_controller_delegation' for r in result['issues'])
