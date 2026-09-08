from __future__ import annotations

import json
from pathlib import Path

import pytest

from build_non_notification_release_v1 import (
    build_release,
    classify_responsibilities,
    validate_pairs,
)


def row(episode_id: str, responsibility_id: str, actions: list[str]) -> dict:
    return {
        "episode_id": episode_id,
        "responsibility_id": responsibility_id,
        "contract": {"required_actions": actions},
    }


def test_classification_excludes_only_exact_notification_action() -> None:
    rows = [
        row("e1", "notify", ["notification.send"]),
        row("e2", "notify", ["notification.send"]),
        row("e3", "mixed", ["gate.open", "notification.send"]),
    ]
    excluded, counts = classify_responsibilities(rows)
    assert excluded == {"notify"}
    assert counts == {"notify": 2, "mixed": 1}


def test_pair_mismatch_fails_closed() -> None:
    with pytest.raises(ValueError, match="pairing or order mismatch"):
        validate_pairs([{"episode_id": "a"}], [{"episode_id": "b"}])


def test_mixed_responsibility_classification_fails_closed() -> None:
    rows = [
        row("e1", "same", ["notification.send"]),
        row("e2", "same", ["gate.open", "notification.send"]),
    ]
    with pytest.raises(ValueError, match="inconsistent notification-only classification"):
        classify_responsibilities(rows)


def test_real_release_builds_expected_filtered_view(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    source = root / "generated/formal_workflow_release_v1"
    output = tmp_path / "release"
    manifest = build_release(source, output)
    assert manifest["output"]["responsibility_count"] == 21
    assert manifest["output"]["episode_count"] == 210
    assert manifest["excluded"]["responsibility_count"] == 9
    assert manifest["excluded"]["episode_count"] == 90
    public = (output / "intervention_required/episodes_public.jsonl").read_text().splitlines()
    private = (output / "intervention_required/episodes_private.jsonl").read_text().splitlines()
    assert len(public) == len(private) == 210
    assert [json.loads(line)["episode_id"] for line in public] == [
        json.loads(line)["episode_id"] for line in private
    ]
