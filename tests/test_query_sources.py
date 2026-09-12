import pytest
from query_construction.sources import SourceRecord, partition, validate_span


def record(id, person='p', text='Notify me when laundry finishes.', corpus='study'):
    return SourceRecord(id, corpus, 'source-file:row:'+id, text, person, 'participant_authored_study', 'unknown')


def test_exact_span_does_not_claim_semantic_entailment():
    r = record('1', text='洗衣结束就通知我。')
    result = validate_span(r, {'source_id':'1', 'start':0, 'end':4, 'quote':'洗衣结束'})
    assert result['traceable'] and result['semantic_entailment'].startswith('not_')


@pytest.mark.parametrize('change', [{'source_id':'other'}, {'start':True}, {'end':999}, {'quote':'keep washing forever'}, {'extra':1}])
def test_model_cannot_invent_a_source_span(change):
    r=record('1'); span={'source_id':'1','start':0,'end':6,'quote':'Notify'}; span.update(change)
    with pytest.raises(ValueError): validate_span(r, span)


def test_participant_and_cross_corpus_duplicate_transitive_grouping():
    rows=[record('a','p1','Turn off lights'),record('b','p1','Close door'),
          record('c','p2',' CLOSE   DOOR ',corpus='other'),record('d','p2','Other need',corpus='other')]
    result=partition(rows, development_ids={'a'}, salt='frozen-v1')
    assert result['groups']==[{'record_ids':['a','b','c','d'],'split':'development','previously_seen':True}]


def test_partition_is_independent_of_input_order():
    rows=[record(str(i),str(i),f'distinct need {i}') for i in range(50)]
    a=partition(rows,development_ids=set(),salt='frozen-v1')
    assert a==partition(list(reversed(rows)),development_ids=set(),salt='frozen-v1')
    assert {g['split'] for g in a['groups']}=={'development','holdout'}


def test_unknown_seen_record_and_duplicate_ids_rejected():
    with pytest.raises(ValueError): partition([record('1')],development_ids={'missing'},salt='v1')
    with pytest.raises(ValueError): partition([record('1'),record('1')],development_ids=set(),salt='v1')
