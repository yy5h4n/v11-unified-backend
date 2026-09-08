from copy import deepcopy

import pytest

from test_formal_release_validator_trust import _make_release, _rewrite_private
from validate_formal_dataset_release import ReleaseValidationError, validate_release


def test_release_validator_accepts_exact_trusted_threshold(tmp_path, monkeypatch):
    _make_release(tmp_path, monkeypatch)
    result = validate_release(tmp_path, minimum_responsibilities=1, minimum_episodes=1)
    assert result["status"] == "PASS"
    assert result["unique_process_count"] == 1
    assert result["unique_noop_environment_count"] == 1


def test_release_validator_rejects_seed_or_config_identity_instead_of_causal_process(tmp_path, monkeypatch):
    _, private = _make_release(tmp_path, monkeypatch)
    attacked = deepcopy(private)
    attacked["process_digest"] = "0" * 64
    _rewrite_private(tmp_path, attacked)
    with pytest.raises(ReleaseValidationError, match="trusted no-op causal environment"):
        validate_release(tmp_path, minimum_responsibilities=1, minimum_episodes=1)


def test_release_validator_enforces_30_by_300_default(tmp_path, monkeypatch):
    _make_release(tmp_path, monkeypatch)
    with pytest.raises(ReleaseValidationError, match="at least 30"):
        validate_release(tmp_path)
