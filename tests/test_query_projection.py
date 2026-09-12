import pytest
from query_construction.projection import project_atoms


def test_projection_preserves_parent_and_unselected_responsibility():
    p = dict(source_id='s', decision='candidate', query='Lights off and shutters closed when away.',
             atoms=[{'responsibility': 'lights off when away'}, {'responsibility': 'shutters closed when away'}])
    r = project_atoms(p, [0], query='Keep lights off when away.', justification='Separate outcomes; shared absence condition retained.')
    assert len(p['atoms']) == 2 and len(r['proposal']['atoms']) == 1
    assert r['derivation']['unselected_atoms'][0]['index'] == 1
    assert not r['derivation']['fulfills_complete_parent'] and not r['admitted']
    with pytest.raises(ValueError): project_atoms(p, [0, 0], query='x', justification='x')
