"""Build and verify a content-addressed V2 query-construction release."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from tools.summarize_evidence_query_release import summarize


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _resolve(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("release paths must be nonempty and repository-relative")
    root = root.resolve()
    path = (root / relative).resolve()
    if path != root and root not in path.parents:
        raise ValueError("release path escapes repository root")
    if not path.is_file():
        raise ValueError(f"release file missing: {relative}")
    return path


def build_release(root: Path, config: dict[str, Any]) -> dict[str, Any]:
    required = {"schema", "release_id", "frozen_at", "batches", "files", "claim_boundary"}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError("release config has missing or unexpected fields")
    if config["schema"] != "evidence-query-release-config.v1":
        raise ValueError("unsupported release config schema")
    for key in ("release_id", "frozen_at", "claim_boundary"):
        if not isinstance(config[key], str) or not config[key].strip():
            raise ValueError(f"config.{key} must be nonempty text")
    for key in ("batches", "files"):
        if not isinstance(config[key], list) or not config[key] or any(not isinstance(x, str) for x in config[key]):
            raise ValueError(f"config.{key} must be a nonempty text list")
        if len(config[key]) != len(set(config[key])):
            raise ValueError(f"config.{key} contains duplicates")
    if not set(config["batches"]) <= set(config["files"]):
        raise ValueError("every release batch must be content-addressed in files")

    paths = {relative: _resolve(root, relative) for relative in config["files"]}
    summary = summarize([_resolve(root, relative) for relative in config["batches"]])
    return {
        "schema": "evidence-query-release-manifest.v1",
        "release_id": config["release_id"],
        "frozen_at": config["frozen_at"],
        "config_sha256": sha256((_canonical(config) + "\n").encode()).hexdigest(),
        "files": {
            relative: {"sha256": sha256(path.read_bytes()).hexdigest(), "bytes": path.stat().st_size}
            for relative, path in paths.items()
        },
        "summary": summary,
        "claim_boundary": config["claim_boundary"],
    }


def verify_release(root: Path, config: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    rebuilt = build_release(root, config)
    if _canonical(rebuilt) != _canonical(manifest):
        changed = []
        expected_files = manifest.get("files", {}) if isinstance(manifest, dict) else {}
        for relative, current in rebuilt["files"].items():
            if expected_files.get(relative) != current:
                changed.append(relative)
        raise ValueError(f"release manifest is stale; changed files: {changed}")
    return {
        "release_id": rebuilt["release_id"],
        "verified": True,
        "file_count": len(rebuilt["files"]),
        "attempted_items": rebuilt["summary"]["combined_descriptive_counts"]["attempted_items"],
    }
