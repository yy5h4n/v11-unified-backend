import pytest
from query_construction.import_hiis import parse_table, build


def test_import_preserves_multiline_quotes_and_participant_identity():
    rows, rejected = parse_table('user;rule;nl\n7;notify;"Laundry is done;\nnotify me."\n'.encode())
    assert not rejected and len(rows) == 1
    assert rows[0]['source']['text'] == 'Laundry is done;\nnotify me.'
    assert rows[0]['source']['participant'] == '7'
    assert rows[0]['source']['license_status'] == 'unresolved'
    assert rows[0]['record_number'] == 1


def test_missing_person_or_text_is_rejected_not_imputed():
    rows, rejected = parse_table(b'user;rule;nl\n;name;text\n7;name;\n')
    assert not rows and len(rejected) == 2


def test_malformed_row_remains_accounted_for():
    rows, rejected = parse_table(b'user;rule;nl\n7;name;text;extra\n8;name;valid\n')
    assert len(rows) == len(rejected) == 1
    assert rows[0]['record_number'] == 2


def test_header_and_source_versions_are_not_guessed():
    with pytest.raises(ValueError): parse_table(b'user,text\n7,hello\n')
    with pytest.raises(ValueError): build(b'altered source', {'families': []})


def test_pinned_encoding_preserves_temperature_and_quotes():
    rows, rejected = parse_table('user;rule;nl\n7;heat;Keep it at 20°C – don’t overheat\n'.encode('cp1252'))
    assert not rejected
    assert rows[0]['source']['text'] == 'Keep it at 20°C – don’t overheat'
