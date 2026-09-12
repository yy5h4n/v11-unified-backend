import json
import pytest
from query_construction.capabilities import CARDS
from query_construction.routing import parse_routing


def packet():
    return {'source_id': 's', 'routes': [dict(route_id=r, status='unsupported',
            reason='Requested trigger unavailable in catalogue', missing_requirements=['trigger']) for r in CARDS]}


def test_full_catalogue_never_admits():
    result = parse_routing('s', json.dumps(packet()))
    assert len(result['routes']) == 15
    assert not result['admitted']


@pytest.mark.parametrize('mutation', ['omit', 'duplicate', 'candidate_missing'])
def test_incomplete_or_contradictory_screen_rejected(mutation):
    value = packet()
    if mutation == 'omit':
        value['routes'].pop()
    elif mutation == 'duplicate':
        value['routes'][-1] = value['routes'][0]
    else:
        value['routes'][0]['status'] = 'candidate'
    with pytest.raises(ValueError):
        parse_routing('s', json.dumps(value))
