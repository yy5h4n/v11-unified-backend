"""Build the non-self-referential, hash-bound conformance release manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from conformance_v1 import hashing


PACKAGE_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = PACKAGE_DIR / "MANIFEST.json"
SPEC_PATH = PACKAGE_DIR.parent / "DATASET_CONSTRUCTION_PIPELINE_V1.md"


def raw_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normative_text_without_section_15(text: str) -> str:
    start = text.index("## 15. Non-normative current project mapping")
    end = text.index("## 16. Implementation rule")
    return text[:start] + text[end:]


def normative_artifacts() -> list[Path]:
    paths: list[Path] = []
    for path in PACKAGE_DIR.rglob("*"):
        if not path.is_file() or path == MANIFEST_PATH:
            continue
        rel = path.relative_to(PACKAGE_DIR)
        if "__pycache__" in rel.parts or path.suffix == ".pyc":
            continue
        paths.append(path)
    return sorted(paths, key=lambda p: p.relative_to(PACKAGE_DIR).as_posix())


def build() -> dict:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    spec_text = SPEC_PATH.read_text(encoding="utf-8")
    full_hash = raw_sha256(SPEC_PATH)
    normative_hash = hashlib.sha256(
        normative_text_without_section_15(spec_text).encode("utf-8")
    ).hexdigest()
    artifact_hashes = {
        path.relative_to(PACKAGE_DIR).as_posix(): raw_sha256(path)
        for path in normative_artifacts()
    }
    manifest["normative_document"]["full_sha256"] = full_hash
    manifest["normative_document"]["normative_sha256_excluding_section_15"] = normative_hash
    manifest["hash_algorithm"] = "SHA-256(raw artifact bytes); RFC 8785 for release tuple"
    manifest["artifact_sha256"] = artifact_hashes
    tuple_payload = {
        "spec_version": manifest["spec_version"],
        "package_version": manifest["package_version"],
        "normative_document": manifest["normative_document"],
        "artifact_sha256": artifact_hashes,
    }
    manifest["release_tuple_sha256"] = hashing.sha256_hex(hashing.canonical_bytes(tuple_payload))
    MANIFEST_PATH.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    built = build()
    print(built["release_tuple_sha256"])
