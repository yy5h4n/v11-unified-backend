from query_construction.import_hiis import parse_table


def test_embedded_record_quarantined_not_split_or_reattributed():
    raw = b'user;rule;nl\n16;feeding;"Fill the bowl.\n17;stairs;Activate lift."\n18;light;Turn on light.\n'
    rows, rejected = parse_table(raw)
    assert len(rows) == 1 and rows[0]['source']['participant'] == '18'
    assert len(rejected) == 1 and rejected[0]['record_number'] == 1
    assert '17;stairs;' in rejected[0]['raw_fields']['nl']


def test_ordinary_multiline_text_not_quarantined():
    rows, rejected = parse_table(b'user;rule;nl\n16;feeding;"Fill the bowl.\nThen turn on the light."\n')
    assert len(rows) == 1 and not rejected
