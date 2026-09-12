import pytest
from query_construction.extraction import locate_quote
from query_construction.sources import SourceRecord


def source(text):
    return SourceRecord('s', 'fixture', 'test', text, 'p', 'test', 'test')


def test_whitespace_alignment_preserves_original_and_model_quote():
    result = locate_quote(source('at night\n  lights on'), {'source_id': 's', 'quote': 'at night lights on'})
    assert result['quote'] == 'at night\n  lights on'
    assert result['model_quote'] == 'at night lights on'
    assert result['alignment'] == 'whitespace_only'


@pytest.mark.parametrize('text,quote', [('lights off', 'lights on'), ('after 15 minutes', 'after 10 minutes'),
                                      ('a\nb a  b', 'a b')])
def test_changed_words_numbers_or_ambiguous_alignment_rejected(text, quote):
    with pytest.raises(ValueError):
        locate_quote(source(text), {'source_id': 's', 'quote': quote})
