from query_construction.sources import SourceRecord
from query_construction.semantic_checks import surface_review


def test_changed_number_and_scope_are_flagged():
    s = SourceRecord('s', 'test', 'fixture', 'After 15 minutes open it.', 'p', 'participant_rule', 'test')
    p = dict(decision='candidate', query='After 20 minutes open it.',
             atoms=[{'kind': 'one_shot'}], assumptions=[])
    codes = {i['code'] for i in surface_review(s, p)['issues']}
    assert codes == {'numeric_condition_missing_or_reexpressed',
                     'numeric_condition_added_or_reexpressed',
                     'rule_scope_classified_one_shot_requires_evidence'}


def test_no_alarm_never_certifies_semantics():
    s = SourceRecord('s', 'test', 'fixture', 'Open it.', 'p', 'test', 'test')
    p = dict(decision='candidate', query='Close it.', atoms=[{'kind': 'respond'}], assumptions=[])
    result = surface_review(s, p)
    assert result['issues'] == []
    assert result['semantic_status'] == 'unresolved' and not result['admitted']


def test_workflow_in_final_query_not_hidden_by_natural_atom():
    s = SourceRecord('s', 'test', 'fixture', 'Turn on lights when I arrive.', 'p', 'test', 'test')
    p = dict(decision='candidate', query='Set up a rule: turn on lights when I arrive.',
             atoms=[{'kind': 'respond', 'responsibility': s.text}], assumptions=[])
    assert surface_review(s, p)['issues'][0]['code'] == 'query_requests_workflow_instead_of_delegation'
