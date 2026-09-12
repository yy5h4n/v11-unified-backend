import json
import pytest
from query_construction.review import DIMENSIONS, parse_review


def payload():
    return {'source_id': 's', 'checks': {d: {'verdict': 'preserved', 'reason': 'No discrepancy found.'}
                                        for d in DIMENSIONS}}


def test_model_unanimity_never_admits():
    result = parse_review('s', json.dumps(payload()))
    assert result['model_review_clear'] and not result['admitted']


def test_uncertainty_prevents_clear_review():
    p = payload(); p['checks']['conditions']['verdict'] = 'uncertain'
    assert not parse_review('s', json.dumps(p))['model_review_clear']


def test_missing_dimension_and_unknown_decision_rejected():
    p = payload(); del p['checks']['entities']
    with pytest.raises(ValueError): parse_review('s', json.dumps(p))
    p = payload(); p['admitted'] = True
    with pytest.raises(ValueError): parse_review('s', json.dumps(p))
