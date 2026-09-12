import json
import sys
import pytest
from tools import local_preflight as preflight


@pytest.mark.parametrize('profile', ['legacy-release', 'autonomous-interaction'])
@pytest.mark.parametrize('evidence_passed', [True, False])
def test_explicit_profile_does_not_ignore_evidence_failure(monkeypatch, capsys, profile, evidence_passed):
    monkeypatch.setattr(sys, 'argv', ['preflight', '--strict', '--profile', profile])
    monkeypatch.setattr(preflight, '_check_file', lambda *a, **k: (True, 'fixture file'))
    monkeypatch.setattr(preflight, '_check_dir', lambda *a, **k: (True, 'fixture dir'))
    commands = []
    def run(label, command, **kwargs):
        commands.append(command)
        return (True, 'compile') if label == 'compile' else (evidence_passed, 'evidence')
    monkeypatch.setattr(preflight, '_run', run)
    assert preflight.main() == (0 if evidence_passed else 1)
    report = json.loads(capsys.readouterr().out)
    assert report['profile'] == profile
    if profile == 'autonomous-interaction':
        assert 'tools/check_backend_delivery.py' in commands[-1]
        assert '--native-dir' in commands[-1]
        assert 'not task evaluation' in report['scope']
    else:
        assert 'tools/backend_acceptance_runner.py' in commands[-1]
        assert '--check' in commands[-1]
