import json
from pathlib import Path

import pytest

from build_formal_dataset_package_v1 import PACKAGE, build


@pytest.fixture(scope="module")
def package_result():
    return build()


def test_formal_package_builds_and_records_hard_targets(package_result):
    result = package_result
    assert result["status"] == "PASS"
    assert result["responsibility_count"] == 30
    assert result["episode_count"] == 300
    assert result["unique_process_count"] == 300
    assert result["unique_query_count"] == 120
    report = json.loads((PACKAGE / "ACCEPTANCE_REPORT.json").read_text(encoding="utf-8"))
    assert all(report["checks"].values())
    assert report["statistics"]["catalog_coverage"] == {"FULL": 30, "PARTIAL": 24, "UNSUPPORTED": 75}


def test_formal_package_has_documentation_and_hash_manifest(package_result):
    assert (PACKAGE / "DATASET_CARD.md").is_file()
    manifest = json.loads((PACKAGE / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert manifest["dataset_tracks"]["restraint_required"] is None
    assert manifest["evaluation"]["primary_metric"] == "responsibility_success_rate"
    assert all(len(item["sha256"]) == 64 for item in manifest["artifacts"].values())
    baselines = json.loads((PACKAGE / "REFERENCE_BASELINES.json").read_text(encoding="utf-8"))
    assert baselines["overall"]["reference_responsibility_success_rate"] == 1.0
    assert baselines["overall"]["noop_responsibility_success_rate"] == 0.0
    assert baselines["overall"]["query_deleted_responsibility_success_rate"] == 0.0
