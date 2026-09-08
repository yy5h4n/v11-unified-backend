#!/usr/bin/env python3
"""Fetch one content-addressed CityLearn battery/PV asset bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request


ROOT = Path(__file__).resolve().parent
TAG = "v2.5.0"
TAG_COMMIT = "29062af6d077409e1c37a3e53a6cac30fd4d02bc"
REPOSITORY = "citylearn-project/CityLearn"
API_ROOT = f"https://api.github.com/repos/{REPOSITORY}/contents"
LICENSE_URL = f"https://github.com/{REPOSITORY}/blob/{TAG}/LICENSE"
DEFAULT_OUTPUT = ROOT / "shared_assets" / "citylearn_v2.5.0"
ASSETS = (
    ("data/misc/battery_choices.yaml", "misc/battery_choices.yaml"),
    (
        "data/misc/lbl-tracking_the_sun-res-pv.csv",
        "misc/lbl-tracking_the_sun-res-pv.csv",
    ),
    (
        "data/datasets/ca_alameda_county_neighborhood/weather.epw",
        "dataset/weather.epw",
    ),
)


def fetch_bytes(url: str) -> bytes:
    """Fetch with verified TLS, using system curl only as a trust-store fallback."""
    python_error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=90) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status} for {url}")
                return response.read()
        except Exception as error:  # urllib exposes several transport exception types.
            python_error = error
            if attempt < 3:
                time.sleep(1.0 + attempt)

    curl_error: Exception | None = None
    for attempt in range(4):
        try:
            return subprocess.run(
                [
                    "curl",
                    "--http1.1",
                    "--fail",
                    "--location",
                    "--silent",
                    "--show-error",
                    "--connect-timeout",
                    "30",
                    "--max-time",
                    "180",
                    url,
                ],
                check=True,
                capture_output=True,
            ).stdout
        except (OSError, subprocess.CalledProcessError) as error:
            curl_error = error
            if attempt < 3:
                time.sleep(1.0 + attempt)
    raise RuntimeError(
        f"failed to fetch {url} with verified urllib ({python_error}) and curl"
    ) from curl_error


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def github_item(source_path: str) -> dict[str, object]:
    url = f"{API_ROOT}/{source_path}?ref={TAG}"
    try:
        item = json.loads(fetch_bytes(url))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"GitHub API returned invalid JSON for {source_path}") from exc
    if not isinstance(item, dict) or item.get("type") != "file":
        raise RuntimeError(f"GitHub API did not resolve a file: {source_path}")
    required = ("name", "sha", "size", "download_url", "git_url")
    if any(not item.get(key) for key in required):
        raise RuntimeError(f"GitHub API metadata incomplete for {source_path}")
    return item


def load_manifest(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid existing manifest: {path}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"existing manifest is not an object: {path}")
    return value


def prior_record(manifest: dict[str, object] | None, relative_path: str) -> dict[str, object] | None:
    records = manifest.get("files") if manifest else None
    if not isinstance(records, list):
        return None
    for record in records:
        if isinstance(record, dict) and record.get("relative_path") == relative_path:
            return record
    return None


def write_atomic(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def materialize_asset(
    output_dir: Path,
    source_path: str,
    relative_path: str,
    existing_manifest: dict[str, object] | None,
    refresh: bool,
) -> dict[str, object]:
    item = github_item(source_path)
    target = output_dir / relative_path
    old = prior_record(existing_manifest, relative_path)

    if target.is_file() and not refresh:
        actual_size = target.stat().st_size
        actual_sha256 = file_sha256(target)
        reusable = (
            old is not None
            and old.get("git_sha") == item["sha"]
            and old.get("bytes") == actual_size
            and old.get("sha256") == actual_sha256
        )
        if not reusable:
            raise RuntimeError(
                f"existing asset does not match its manifest: {target}; use --refresh"
            )
        payload_size, payload_sha256 = actual_size, actual_sha256
    else:
        payload = fetch_bytes(str(item["download_url"]))
        if len(payload) != int(item["size"]):
            raise RuntimeError(
                f"size mismatch for {source_path}: expected {item['size']}, got {len(payload)}"
            )
        write_atomic(target, payload)
        payload_size, payload_sha256 = len(payload), sha256(payload)

    return {
        "relative_path": relative_path,
        "source_path": source_path,
        "source_url": item["download_url"],
        "git_blob_url": item["git_url"],
        "git_sha": item["sha"],
        "bytes": payload_size,
        "sha256": payload_sha256,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch the shared CityLearn v2.5.0 battery/PV assets with provenance."
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Replace existing assets after re-resolving the pinned Git tag.",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    manifest_path = output_dir / "asset_manifest.json"
    existing_manifest = load_manifest(manifest_path)
    records = [
        materialize_asset(
            output_dir,
            source_path,
            relative_path,
            existing_manifest,
            args.refresh,
        )
        for source_path, relative_path in ASSETS
    ]
    manifest = {
        "source": "CityLearn official GitHub release assets",
        "repository": REPOSITORY,
        "tag": TAG,
        "tag_commit": TAG_COMMIT,
        "license_url": LICENSE_URL,
        "files": records,
        "total_bytes": sum(int(record["bytes"]) for record in records),
    }
    write_atomic(
        manifest_path,
        (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(f"verified {len(records)} assets ({manifest['total_bytes']} bytes) in {output_dir}")


if __name__ == "__main__":
    main()
