import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "generated/query_construction_v2"


def test_human_core_probe_preserves_frozen_order_and_negative_result():
    freeze = json.loads((DIR / "human_core_transfer_freeze_v1.json").read_text())
    result = json.loads((DIR / "human_core_transfer_result_v1.json").read_text())
    assert result["freeze_id"] == freeze["freeze_id"]
    assert [row["order"] for row in result["searches"]] == list(range(1, 7))
    assert [row["target"] for row in result["searches"]] == freeze["search_order"]
    assert result["outcome"] == "no_qualifying_source"
    assert result["source_replacement"] is False
    assert result["items_added_to_batch"] == 0


def test_human_core_probe_does_not_reuse_existing_batch_urls():
    existing_urls = set()
    for name in ("development_batch_v1.json", "validation_batch_v1.json"):
        batch = json.loads((DIR / name).read_text())
        existing_urls.update(source["url"] for source in batch["sources"])
    result = json.loads((DIR / "human_core_transfer_result_v1.json").read_text())
    reported = set()
    for row in result["searches"]:
        if "url" in row:
            reported.add(row["url"])
        reported.update(row.get("urls", []))
    assert reported
    assert reported.isdisjoint(existing_urls)
