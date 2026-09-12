#!/usr/bin/env python3
"""Create or check a content-addressed evidence-query release manifest."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from query_construction.evidence_release import build_release, verify_release


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8"))
    if args.check:
        manifest = json.loads(args.output.read_text(encoding="utf-8"))
        print(json.dumps(verify_release(ROOT, config, manifest), ensure_ascii=False))
        return
    if args.output.exists():
        raise SystemExit(f"refusing to overwrite frozen manifest: {args.output}")
    manifest = build_release(ROOT, config)
    payload = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.write_text(payload, encoding="utf-8")
    print(json.dumps({
        "release_id": manifest["release_id"],
        "output": str(args.output),
        "sha256": __import__("hashlib").sha256(payload.encode()).hexdigest(),
        "file_count": len(manifest["files"]),
    }))


if __name__ == "__main__":
    main()
