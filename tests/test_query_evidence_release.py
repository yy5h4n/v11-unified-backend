import json
from pathlib import Path

import pytest

from query_construction.evidence_release import build_release, verify_release


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "generated/query_construction_v2/release_config_v1.json"
MANIFEST = ROOT / "generated/query_construction_v2/release_manifest_v1.json"


def test_frozen_release_manifest_matches_every_current_file():
    config = json.loads(CONFIG.read_text())
    manifest = json.loads(MANIFEST.read_text())
    result = verify_release(ROOT, config, manifest)
    assert result["verified"] is True
    assert result["attempted_items"] == 11
    assert result["file_count"] == 22


def test_release_rejects_path_escape():
    config = json.loads(CONFIG.read_text())
    config["files"][0] = "../outside.json"
    with pytest.raises(ValueError, match="escapes|missing"):
        build_release(ROOT, config)


def test_release_rejects_unhashed_batch():
    config = json.loads(CONFIG.read_text())
    config["files"].remove(config["batches"][0])
    with pytest.raises(ValueError, match="content-addressed"):
        build_release(ROOT, config)
