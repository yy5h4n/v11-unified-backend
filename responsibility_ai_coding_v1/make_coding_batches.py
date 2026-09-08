import json
from pathlib import Path

here = Path(__file__).resolve().parent
rows = [line for line in (here / "CODING_PACKET.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
batch_size = 45
for index, start in enumerate(range(0, len(rows), batch_size), start=1):
    batch = rows[start:start + batch_size]
    (here / f"CODING_PACKET_BATCH_{index:02d}.jsonl").write_text("\n".join(batch) + "\n", encoding="utf-8")
    print(json.dumps({"batch": index, "rows": len(batch), "first": json.loads(batch[0])["evidence_id"], "last": json.loads(batch[-1])["evidence_id"]}))
