#!/usr/bin/env python3
"""Build isolated, deterministic Kimi review batches for Luna query drafts."""

from __future__ import annotations

import json
import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "kimi_isolated_standing_query_review_v2_3"
PACKETS = [ROOT / f"STANDING_QUERY_PACKET_V2_3_{i:02d}.jsonl" for i in range(2)]
DRAFTS = [ROOT / f"STANDING_QUERY_DRAFT_LUNA_V2_3_{i:02d}.jsonl" for i in range(2)]
N_BATCHES = 4


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    packet_rows = [row for path in PACKETS for row in load_jsonl(path)]
    draft_rows = [row for path in DRAFTS for row in load_jsonl(path)]
    packets = {row["responsibility_id"]: row for row in packet_rows}
    if len(packets) != len(packet_rows) or len(draft_rows) != 142:
        raise ValueError("unexpected packet/draft coverage")
    combined = []
    for draft in draft_rows:
        rid = draft["responsibility_id"]
        packet = packets[rid]
        if draft["source_evidence_ids"] != packet["evidence_ids"]:
            raise ValueError(f"evidence mismatch for {rid}")
        combined.append({"draft": draft, "source": packet})

    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    instructions = """You are a conservative reviewer, not a generator. Review every input Luna draft against its full source evidence and the standing-query protocol. Output exactly one JSONL row per input in the same order to OUTPUT.jsonl, with exactly these fields: responsibility_id, decision (PASS or REVISE), revised_query (null for PASS; a complete natural first-person standing-intent query for REVISE), quality_flags (array drawn from operational_leakage, oracle_leakage, unsupported_invention, ambiguous_outcome, missing_boundary, compound_responsibility, canonical_name_mismatch, needs_source_review), and rationale. REVISE if the query is operational, leaks a policy/answer, invents an obligation, loses the core outcome, is unnatural/telegraphic, or conflicts with evidence. Do not broaden the responsibility. Device-specific actions belong outside the query unless the device state is itself the human outcome. A natural 'when/while' boundary is allowed; trigger-action recipes are not. Never edit INPUT.jsonl. Do not read or modify files outside this batch directory. Use low reasoning effort and finish the full batch."""
    base, extra = divmod(len(combined), N_BATCHES)
    offset = 0
    manifest = []
    for index in range(N_BATCHES):
        size = base + (index < extra)
        batch = combined[offset:offset + size]
        offset += size
        directory = OUT / f"batch_{index:02d}"
        directory.mkdir()
        (directory / "INSTRUCTIONS.txt").write_text(instructions, encoding="utf-8")
        (directory / "INPUT.jsonl").write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in batch), encoding="utf-8"
        )
        manifest.append({"batch": index, "rows": len(batch), "directory": str(directory)})
    (OUT / "MANIFEST.json").write_text(json.dumps({"rows": 142, "batches": manifest}, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": 142, "batch_sizes": [row["rows"] for row in manifest]}, indent=2))


if __name__ == "__main__":
    main()
