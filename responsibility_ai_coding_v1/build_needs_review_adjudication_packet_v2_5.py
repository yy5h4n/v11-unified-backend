#!/usr/bin/env python3
"""Build the full-evidence packet for resolving all V2.4 needs-review rows."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
NATURAL = ROOT / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_4.json"
RESPONSIBILITIES = ROOT / "PROVISIONAL_AI_RESPONSIBILITY_CATALOG_V2_2.json"
LEDGER = ROOT / "EVIDENCE_LEDGER.jsonl"
OUTPUT = ROOT / "NEEDS_REVIEW_ADJUDICATION_PACKET_V2_5.jsonl"

def load_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

natural = json.loads(NATURAL.read_text(encoding="utf-8"))
responsibilities = {row["responsibility_id"]: row for row in json.loads(RESPONSIBILITIES.read_text(encoding="utf-8"))["responsibilities"]}
ledger = {row["evidence_id"]: row for row in load_jsonl(LEDGER)}
rows = []
for query in natural["queries"]:
    if query["generation_status"] != "needs_review":
        continue
    responsibility = responsibilities[query["responsibility_id"]]
    rows.append({
        "standing_intent_id": query["standing_intent_id"],
        "responsibility_id": query["responsibility_id"],
        "canonical_name": responsibility["canonical_name"],
        "natural_query": query["natural_query"],
        "family": query["family"],
        "lifecycle": query["lifecycle"],
        "delegated_outcome": query["delegated_outcome"],
        "quality_flags": query["quality_flags"],
        "profile_dependencies": query["profile_dependencies"],
        "episode_context_dependencies": query["episode_context_dependencies"],
        "evidence_level": query["evidence_level"],
        "source_evidence": [
            {
                "evidence_id": eid,
                "evidence_text": ledger[eid]["evidence_text"],
                "source_type": ledger[eid]["source_type"],
                "evidence_form": ledger[eid]["evidence_form"],
                "evidence_limits": ledger[eid]["evidence_limits"],
            }
            for eid in query["source_evidence_ids"]
        ],
        "semantic_merge_member_ids": responsibility.get("semantic_merge_member_ids", [query["responsibility_id"]]),
        "name_variants": responsibility.get("name_variants", []),
        "compound_review_status": responsibility.get("compound_review_status"),
    })
if len(rows) != 44:
    raise ValueError(f"expected 44 rows, got {len(rows)}")
OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
print(json.dumps({"rows": len(rows), "flags": sorted({flag for row in rows for flag in row['quality_flags']})}, indent=2))
