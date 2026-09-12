import json
import sys
import pytest
import probe_d1_fault_profiles as probe


def test_check_never_rewrites_stale_profile(tmp_path, monkeypatch):
    destination = tmp_path/'evidence'
    destination.mkdir()
    artifact = destination/'failed.json'
    artifact.write_text('{"verified": false}\n')
    before = artifact.read_bytes()
    monkeypatch.setattr(probe, 'ROOT', tmp_path)
    monkeypatch.setattr(probe, 'probe_profile', lambda _: {'verified': True, 'fault_trajectory_sha256': 'test-only'})
    monkeypatch.setattr(sys, 'argv', ['probe', '--check', '--profile', 'failed', '--output', str(destination)])
    with pytest.raises(SystemExit, match='missing or stale'):
        probe.main()
    assert artifact.read_bytes() == before


def test_check_missing_directory_does_not_create_it(tmp_path, monkeypatch):
    destination = tmp_path/'missing'
    monkeypatch.setattr(probe, 'ROOT', tmp_path)
    monkeypatch.setattr(probe, 'probe_profile', lambda _: {'verified': True, 'fault_trajectory_sha256': 'test-only'})
    monkeypatch.setattr(sys, 'argv', ['probe', '--check', '--profile', 'failed', '--output', str(destination)])
    with pytest.raises(SystemExit):
        probe.main()
    assert not destination.exists()
