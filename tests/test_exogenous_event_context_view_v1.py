from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VIEW = ROOT / "generated/exogenous_event_context_change_v1"


def test_exogenous_event_context_view_is_complete_and_hash_bound() -> None:
    manifest = json.loads((VIEW / "COVERAGE_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "PASS"
    assert manifest["statistics"]["responsibility_count"] == 26
    assert manifest["statistics"]["episode_count"] == 260
    assert manifest["statistics"]["category_counts"] == {
        "AUTONOMOUS_SIMPLE_DYNAMICS": {"episodes": 30, "responsibilities": 3},
        "EXOGENOUS_INTERFERENCE": {"episodes": 80, "responsibilities": 8},
        "EXOGENOUS_TRIGGER": {"episodes": 150, "responsibilities": 15},
    }
    index_path = ROOT / manifest["artifacts"]["episodes_index"]["path"]
    index_bytes = index_path.read_bytes()
    assert len([line for line in index_bytes.splitlines() if line]) == 260
    assert hashlib.sha256(index_bytes).hexdigest() == manifest["artifacts"]["episodes_index"]["sha256"]
    assert len({row["episode_id"] for row in map(json.loads, index_bytes.splitlines())}) == 260


def test_source_release_is_still_byte_identical() -> None:
    manifest = json.loads((VIEW / "COVERAGE_MANIFEST.json").read_text(encoding="utf-8"))
    source = manifest["source_release"]
    assert hashlib.sha256((ROOT / source["public_path"]).read_bytes()).hexdigest() == source["public_sha256"]
    assert hashlib.sha256((ROOT / source["private_path"]).read_bytes()).hexdigest() == source["private_sha256"]
