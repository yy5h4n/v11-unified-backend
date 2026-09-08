"""Deterministically build generated/claim_backend_catalog_v1.json.

Serializes :func:`unified_compiler.claim_backend_catalog.catalog_document`
with a content hash over the canonical payload. No probing, no tests, no
constructor invocation, no timestamps: reruns are byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

V11_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(V11_ROOT))

from unified_compiler.claim_backend_catalog import CATALOG_ID, catalog_document

OUTPUT_PATH = V11_ROOT / "generated" / "claim_backend_catalog_v1.json"


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def build_document() -> dict:
    document = catalog_document()
    content_sha256 = hashlib.sha256(_canonical(document).encode("utf-8")).hexdigest()
    return {
        "catalog_id": CATALOG_ID,
        "content_sha256": content_sha256,
        "catalog": document,
    }


def main() -> int:
    document = build_document()
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH} (content_sha256={document['content_sha256']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
