#!/usr/bin/env python3
"""Build a paired workflow release excluding notification-only responsibilities."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE = ROOT / "generated/formal_workflow_release_v1"
DEFAULT_OUTPUT = ROOT / "generated/formal_workflow_release_non_notification_v1"
EXPECTED_SOURCE_RESPONSIBILITIES = 30
EXPECTED_SOURCE_EPISODES = 300
EXPECTED_OUTPUT_RESPONSIBILITIES = 21
EXPECTED_OUTPUT_EPISODES = 210
NOTIFICATION_ONLY_ACTIONS = ["notification.send"]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSONL at {path}:{line_number}") from exc
    return rows


def canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def jsonl_bytes(rows: list[dict[str, Any]]) -> bytes:
    return b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        for row in rows
    )


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def classify_responsibilities(private_rows: list[dict[str, Any]]) -> tuple[set[str], dict[str, int]]:
    classifications: dict[str, set[bool]] = defaultdict(set)
    counts: dict[str, int] = defaultdict(int)
    for row in private_rows:
        responsibility_id = row.get("responsibility_id")
        required_actions = row.get("contract", {}).get("required_actions")
        if not isinstance(responsibility_id, str) or not responsibility_id:
            raise ValueError("private episode missing responsibility_id")
        if not isinstance(required_actions, list):
            raise ValueError(f"{responsibility_id} missing contract.required_actions")
        classifications[responsibility_id].add(required_actions == NOTIFICATION_ONLY_ACTIONS)
        counts[responsibility_id] += 1

    mixed = sorted(key for key, values in classifications.items() if len(values) != 1)
    if mixed:
        raise ValueError(f"inconsistent notification-only classification: {mixed}")
    excluded = {key for key, values in classifications.items() if values == {True}}
    return excluded, dict(counts)


def validate_pairs(public_rows: list[dict[str, Any]], private_rows: list[dict[str, Any]]) -> None:
    public_ids = [row.get("episode_id") for row in public_rows]
    private_ids = [row.get("episode_id") for row in private_rows]
    if len(public_ids) != len(set(public_ids)) or len(private_ids) != len(set(private_ids)):
        raise ValueError("duplicate episode_id")
    if public_ids != private_ids:
        raise ValueError("public/private episode pairing or order mismatch")


def filter_evaluation(source_path: Path, included_ids: set[str], included_responsibilities: set[str]) -> dict[str, Any]:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    rows = [row for row in data["episode_results"] if row["episode_id"] in included_ids]
    by_responsibility = {
        key: value for key, value in data["by_responsibility"].items()
        if key in included_responsibilities
    }
    successes = [row for row in rows if row["success"]]
    data["episode_results"] = rows
    data["by_responsibility"] = by_responsibility
    data["main_metrics"] = {
        "eligible_episode_count": len(rows),
        "protocol_invalid_count": sum(row["run_status"] == "protocol_invalid" for row in rows),
        "resource_cost_unit": data["main_metrics"]["resource_cost_unit"],
        "responsibility_success_rate": sum(row["success"] for row in rows) / len(rows),
        "success_conditioned_resource_cost": (
            sum(row["action_cost"] for row in successes) / len(successes) if successes else None
        ),
    }
    return data


def filter_baselines(source_path: Path, included_responsibilities: set[str]) -> dict[str, Any]:
    data = json.loads(source_path.read_text(encoding="utf-8"))
    by_responsibility = {
        key: value for key, value in data["by_responsibility"].items()
        if key in included_responsibilities
    }
    episode_count = sum(row["episode_count"] for row in by_responsibility.values())

    def weighted_rate(field: str) -> float:
        return sum(row[field] * row["episode_count"] for row in by_responsibility.values()) / episode_count

    def weighted_success_cost(rate_field: str, cost_field: str) -> float | None:
        weighted_successes = [
            (row["episode_count"] * row[rate_field], row[cost_field])
            for row in by_responsibility.values() if row[cost_field] is not None
        ]
        denominator = sum(count for count, _ in weighted_successes)
        return sum(count * cost for count, cost in weighted_successes) / denominator if denominator else None

    data["by_responsibility"] = by_responsibility
    data["overall"] = {
        "episode_count": episode_count,
        "noop_responsibility_success_rate": weighted_rate("noop_responsibility_success_rate"),
        "noop_success_conditioned_resource_cost": weighted_success_cost(
            "noop_responsibility_success_rate", "noop_success_conditioned_resource_cost"
        ),
        "query_deleted_responsibility_success_rate": weighted_rate(
            "query_deleted_responsibility_success_rate"
        ),
        "reference_responsibility_success_rate": weighted_rate(
            "reference_responsibility_success_rate"
        ),
        "reference_success_conditioned_resource_cost": weighted_success_cost(
            "reference_responsibility_success_rate", "reference_success_conditioned_resource_cost"
        ),
        "resource_cost_unit": data["overall"]["resource_cost_unit"],
    }
    return data


def build_release(
    source: Path,
    output: Path,
    expected_source_responsibilities: int = EXPECTED_SOURCE_RESPONSIBILITIES,
    expected_source_episodes: int = EXPECTED_SOURCE_EPISODES,
    expected_output_responsibilities: int = EXPECTED_OUTPUT_RESPONSIBILITIES,
    expected_output_episodes: int = EXPECTED_OUTPUT_EPISODES,
) -> dict[str, Any]:
    source_track = source / "intervention_required"
    public_path = source_track / "episodes_public.jsonl"
    private_path = source_track / "episodes_private.jsonl"
    public_rows = read_jsonl(public_path)
    private_rows = read_jsonl(private_path)
    validate_pairs(public_rows, private_rows)

    excluded, source_counts = classify_responsibilities(private_rows)
    if len(source_counts) != expected_source_responsibilities or len(private_rows) != expected_source_episodes:
        raise ValueError(
            f"unexpected source counts: {len(source_counts)} responsibilities/{len(private_rows)} episodes"
        )

    included_private = [row for row in private_rows if row["responsibility_id"] not in excluded]
    included_ids = {row["episode_id"] for row in included_private}
    included_public = [row for row in public_rows if row["episode_id"] in included_ids]
    validate_pairs(included_public, included_private)
    included_responsibilities = {row["responsibility_id"] for row in included_private}
    if len(included_responsibilities) != expected_output_responsibilities or len(included_private) != expected_output_episodes:
        raise ValueError(
            f"unexpected output counts: {len(included_responsibilities)} responsibilities/{len(included_private)} episodes"
        )

    output_track = output / "intervention_required"
    evaluations_dir = output / "evaluations"
    output_track.mkdir(parents=True, exist_ok=True)
    evaluations_dir.mkdir(parents=True, exist_ok=True)

    public_bytes = jsonl_bytes(included_public)
    private_bytes = jsonl_bytes(included_private)
    (output_track / "episodes_public.jsonl").write_bytes(public_bytes)
    (output_track / "episodes_private.jsonl").write_bytes(private_bytes)

    for policy in ("noop", "reference"):
        filtered = filter_evaluation(
            source / "evaluations" / f"{policy}.json", included_ids, included_responsibilities
        )
        (evaluations_dir / f"{policy}.json").write_bytes(canonical_json_bytes(filtered))
    baselines = filter_baselines(source / "REFERENCE_BASELINES.json", included_responsibilities)
    (output / "REFERENCE_BASELINES.json").write_bytes(canonical_json_bytes(baselines))

    excluded_counts = {key: source_counts[key] for key in sorted(excluded)}
    manifest = {
        "schema_version": "formal-workflow-non-notification-release-v1",
        "status": "DERIVED_AND_VERIFIED",
        "source_release": str(source.relative_to(ROOT)) if source.is_relative_to(ROOT) else str(source),
        "scope_rule": {
            "exclude_if": {"contract.required_actions": NOTIFICATION_ONLY_ACTIONS},
            "unit": "entire responsibility",
            "mixed_responsibility_policy": "fail_closed",
        },
        "source": {
            "responsibility_count": len(source_counts),
            "episode_count": len(private_rows),
            "public_sha256": sha256_path(public_path),
            "private_sha256": sha256_path(private_path),
        },
        "output": {
            "responsibility_count": len(included_responsibilities),
            "episode_count": len(included_private),
            "public_sha256": sha256_bytes(public_bytes),
            "private_sha256": sha256_bytes(private_bytes),
        },
        "excluded": {
            "responsibility_count": len(excluded),
            "episode_count": sum(excluded_counts.values()),
            "responsibility_episode_counts": excluded_counts,
        },
    }
    (output / "DERIVATION_MANIFEST.json").write_bytes(canonical_json_bytes(manifest))
    (output_track / "manifest.json").write_bytes(canonical_json_bytes(manifest))
    card = f"""# Formal Workflow Release — Non-notification Scope v1

This is a lossless derived view of `generated/formal_workflow_release_v1`, not a replacement for the historical release.

- Included: {len(included_responsibilities)} responsibilities / {len(included_private)} paired Episodes.
- Excluded: {len(excluded)} notification-only responsibilities / {sum(excluded_counts.values())} Episodes.
- Exact rule: exclude the entire responsibility iff every private Contract has `required_actions == [\"notification.send\"]`.
- Mixed responsibilities with independently evaluated physical/device actions remain included.
- Public/private order and Episode IDs are preserved; source and output hashes are in `DERIVATION_MANIFEST.json`.
- This filtering does not upgrade the T2 workflow backend into a physical or strategy-adaptation benchmark.
"""
    (output / "DATASET_CARD.md").write_text(card, encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    manifest = build_release(args.source.resolve(), args.output.resolve())
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
