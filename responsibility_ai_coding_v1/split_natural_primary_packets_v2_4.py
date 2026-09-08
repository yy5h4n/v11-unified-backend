#!/usr/bin/env python3
import json
from pathlib import Path

root = Path(__file__).resolve().parent
rows = []
for i in range(2):
    rows.extend(json.loads(line) for line in (root / f"SURFACE_QUERY_PACKET_V2_4_{i:02d}.jsonl").read_text().splitlines() if line)
sizes = [36, 36, 35, 35]
offset = 0
for i, size in enumerate(sizes):
    batch = rows[offset:offset + size]
    offset += size
    (root / f"NATURAL_PRIMARY_QUERY_PACKET_V2_4_{i:02d}.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in batch), encoding="utf-8"
    )
print(json.dumps({"rows": len(rows), "sizes": sizes, "unique": len({r['standing_intent_id'] for r in rows}) == len(rows)}))
