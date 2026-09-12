import csv
import io
import zipfile

from query_construction.import_crowdre import HEADER, import_archive


def test_import_preserves_conditions_and_team_isolation(tmp_path):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    writer.writerows([
        ['1', '1', 'u1', 'g1', 'night', 'movement', 'lights on', '', '1', '', ''],
        ['2', '2', 'u2', 'g1', 'day', 'absence', 'lights off', '', '1', '', ''],
        ['3', '3', 'u3', 'g2', 'other', 'other', 'lights off', '', '1', '', ''],
        ['4', '4', 'u4', 'g3', '', '', 'bad', '', '1', 'unexpected', ''],
    ])
    path = tmp_path / 'source.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('all_requirements.csv', buffer.getvalue())
    result = import_archive(path)
    assert len(result['records']) == 3
    assert len(result['quarantined']) == 1
    assert result['records'][0]['source']['text'] == 'Context: night\nStimuli: movement\nResponse: lights on'
    assert len(result['partition']['groups']) == 1  # team plus duplicate transitivity
    assert result['admitted'] is False
