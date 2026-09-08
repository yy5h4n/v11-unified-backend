"""Recompute fail-closed backend coverage for every catalog responsibility."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "responsibility_ai_coding_v1/NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
CONTRACTS = ROOT / "generated/formal_responsibility_contracts_v1.json"
WORKFLOW_RELEASE = ROOT / "generated/formal_workflow_release_v1/intervention_required"
OUTPUT = ROOT / "generated/responsibility_backend_coverage_v1.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build() -> dict[str, Any]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["queries"]
    contracts = json.loads(CONTRACTS.read_text(encoding="utf-8"))["routes"]
    routes = {row["responsibility_id"]: row for row in contracts}
    admitted = _read_jsonl(WORKFLOW_RELEASE / "episodes_private.jsonl")
    full_ids = {row["responsibility_id"] for row in admitted if row["support_status"] == "FULL"}
    admitted_counts = Counter(row["responsibility_id"] for row in admitted)
    rows = []
    for source in catalog:
        responsibility_id = source["responsibility_id"]
        route = routes.get(responsibility_id)
        if responsibility_id in full_ids and route is not None and route.get("support_status") == "FULL":
            status = "FULL"
            reason = "Executable T2 backend evidence: reference succeeds, no-op and Query deletion fail, actions change the trace, and deterministic replay matches."
            backend_route = route["backend_route"]
            backend_id = route["backend_id"]
            episode_count = admitted_counts[responsibility_id]
            evidence = {
                "release": str(WORKFLOW_RELEASE.relative_to(ROOT)),
                "manifest_sha256": _sha(WORKFLOW_RELEASE / "manifest.json"),
            }
        elif route is not None:
            status = "PARTIAL"
            reason = "A capability route and Contract exist, but no backend-specific formal Episode has passed the executable admission gates."
            backend_route = route["backend_route"]
            backend_id = route["backend_id"]
            episode_count = 0
            evidence = {"contract_digest": route["contract_digest"]}
        else:
            status = "UNSUPPORTED"
            reason = "No audited backend route currently covers the complete observation, action, dynamics, event, and evaluator requirements."
            backend_route = None
            backend_id = None
            episode_count = 0
            evidence = {}
        rows.append({
            "responsibility_id": responsibility_id,
            "standing_intent_id": source["standing_intent_id"],
            "family": source["family"],
            "delegated_outcome": source["delegated_outcome"],
            "support_status": status,
            "backend_route": backend_route,
            "backend_id": backend_id,
            "formal_episode_count": episode_count,
            "reason": reason,
            "evidence": evidence,
        })
    counts = Counter(row["support_status"] for row in rows)
    if len(rows) != 129 or sum(counts.values()) != 129:
        raise RuntimeError(f"unexpected coverage accounting: total={len(rows)}, statuses={dict(counts)}")
    result = {
        "schema_version": "responsibility-backend-coverage-v1",
        "policy": {
            "FULL": "Complete capability match plus admitted executable Episode evidence.",
            "PARTIAL": "Plausible capability route or frozen Contract without complete admitted executable evidence.",
            "UNSUPPORTED": "No complete audited route.",
            "fail_closed": True,
        },
        "source_catalog": str(CATALOG.relative_to(ROOT)),
        "source_catalog_sha256": _sha(CATALOG),
        "contract_route_sha256": _sha(CONTRACTS),
        "statistics": {
            "catalog_responsibility_count": len(rows),
            "support_status_counts": dict(sorted(counts.items())),
            "formal_episode_count": sum(row["formal_episode_count"] for row in rows),
            "full_responsibility_with_episodes": sum(row["formal_episode_count"] > 0 for row in rows),
        },
        "rows": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


def main() -> None:
    result = build()
    print(json.dumps({"output": str(OUTPUT), **result["statistics"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
