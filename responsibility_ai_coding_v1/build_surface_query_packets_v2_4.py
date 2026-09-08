#!/usr/bin/env python3
"""Build two deterministic natural-surface generation packets from V2.3."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "STANDING_INTENT_QUERY_CATALOG_V2_3.json"
OUTPUTS = [ROOT / f"SURFACE_QUERY_PACKET_V2_4_{i:02d}.jsonl" for i in range(2)]


def main() -> None:
    catalog = json.loads(SOURCE.read_text(encoding="utf-8"))
    rows = catalog["queries"]
    if len(rows) != 142:
        raise ValueError("expected 142 standing intents")
    packet_rows = []
    for row in rows:
        packet_rows.append({
            "standing_intent_id": row["standing_intent_id"],
            "responsibility_id": row["responsibility_id"],
            "v2_3_canonical_query": row["canonical_query"],
            "family": row["family"],
            "beneficiary": row["beneficiary"],
            "lifecycle": row["lifecycle"],
            "delegated_outcome": row["delegated_outcome"],
            "duration_boundary": row["duration_boundary"],
            "profile_dependencies": row["profile_dependencies"],
            "episode_context_dependencies": row["episode_context_dependencies"],
            "evidence_grounded_constraints": row["evidence_grounded_constraints"],
            "source_evidence_ids": row["source_evidence_ids"],
            "inherited_quality_flags": row["quality_flags"],
            "inherited_generation_status": row["generation_status"],
        })
    for index, output in enumerate(OUTPUTS):
        batch = packet_rows[index * 71:(index + 1) * 71]
        output.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in batch), encoding="utf-8")
    print(json.dumps({"rows": 142, "packet_rows": [71, 71], "unique_ids": len({r['standing_intent_id'] for r in packet_rows}) == 142}, indent=2))


if __name__ == "__main__":
    main()
