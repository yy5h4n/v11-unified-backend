"""Build a frozen, non-copying view of the V11 exogenous-change Episodes.

The formal workflow release remains byte-for-byte unchanged.  This builder emits
an index and manifest that preserve the 26 responsibilities / 260 Episodes whose
responsibility conditions include a non-Agent-caused external change.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
SOURCE_DIR = ROOT / "generated/formal_workflow_release_v1"
SOURCE_PUBLIC = SOURCE_DIR / "intervention_required/episodes_public.jsonl"
SOURCE_PRIVATE = SOURCE_DIR / "intervention_required/episodes_private.jsonl"
SOURCE_PACKAGE_MANIFEST = SOURCE_DIR / "PACKAGE_MANIFEST.json"
SOURCE_CATALOG = ROOT / "responsibility_ai_coding_v1/NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
OUTPUT_DIR = ROOT / "generated/exogenous_event_context_change_v1"


CATEGORY_BY_SCENARIO = {
    # An external event or human/context transition creates the obligation.
    "unattended_stove_guard": "EXOGENOUS_TRIGGER",
    "departure_lockdown": "EXOGENOUS_TRIGGER",
    "away_intercom_notification": "EXOGENOUS_TRIGGER",
    "shower_music_availability": "EXOGENOUS_TRIGGER",
    "full_bin_collection_notice": "EXOGENOUS_TRIGGER",
    "mail_arrival_notification": "EXOGENOUS_TRIGGER",
    "away_visitor_monitoring": "EXOGENOUS_TRIGGER",
    "departure_key_reminder": "EXOGENOUS_TRIGGER",
    "failed_entry_notification": "EXOGENOUS_TRIGGER",
    "post_use_toilet_flush": "EXOGENOUS_TRIGGER",
    "toilet_paper_depletion_notice": "EXOGENOUS_TRIGGER",
    "unoccupied_visitor_acknowledgement": "EXOGENOUS_TRIGGER",
    "shower_news_delivery": "EXOGENOUS_TRIGGER",
    "laundry_backlog_management": "EXOGENOUS_TRIGGER",
    "bathroom_occupancy_indicator": "EXOGENOUS_TRIGGER",
    # A later external event changes or challenges an active responsibility.
    "television_curfew": "EXOGENOUS_INTERFERENCE",
    "keyless_resident_entry": "EXOGENOUS_INTERFERENCE",
    "unoccupied_home_security": "EXOGENOUS_INTERFERENCE",
    "authorized_vehicle_gate_entry": "EXOGENOUS_INTERFERENCE",
    "expected_delivery_gate_and_notice": "EXOGENOUS_INTERFERENCE",
    "remote_visitor_gate_access": "EXOGENOUS_INTERFERENCE",
    "post_parking_garage_closure": "EXOGENOUS_INTERFERENCE",
    "supported_emergency_call": "EXOGENOUS_INTERFERENCE",
    # A non-Agent process evolves over time, but with simplified dynamics.
    "fridge_door_left_open": "AUTONOMOUS_SIMPLE_DYNAMICS",
    "pellet_supply_guard": "AUTONOMOUS_SIMPLE_DYNAMICS",
    "beer_dispenser_stock": "AUTONOMOUS_SIMPLE_DYNAMICS",
}


EXCLUDED_BASELINE_SCENARIOS = {
    "laundry_completion_notification": "normal device completion caused by a pre-existing workflow",
    "weekly_floor_cleaning": "known calendar recurrence rather than an external environment change",
    "intended_wake_alarm": "known clock deadline rather than an external environment change",
    "coffee_ready_at_wake": "known clock deadline plus expected device completion",
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _row_sha256(row: dict[str, Any]) -> str:
    payload = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return _sha256_bytes(payload)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _verify_source_hashes(package: dict[str, Any]) -> None:
    expected_public = package["artifacts"]["public_episodes"]["sha256"]
    expected_private = package["artifacts"]["private_episodes"]["sha256"]
    actual_public = _sha256_bytes(SOURCE_PUBLIC.read_bytes())
    actual_private = _sha256_bytes(SOURCE_PRIVATE.read_bytes())
    if actual_public != expected_public or actual_private != expected_private:
        raise RuntimeError(
            json.dumps(
                {
                    "source_release_hash_mismatch": {
                        "public": {"expected": expected_public, "actual": actual_public},
                        "private": {"expected": expected_private, "actual": actual_private},
                    }
                },
                indent=2,
            )
        )


def _class_card(manifest: dict[str, Any]) -> str:
    counts = manifest["statistics"]
    return f"""# Exogenous Event / Context Change — V11 Derived View v1

## Scope

This is a non-copying, hash-bound view over the frozen `continuous-household-responsibility-workflow-v1`
release. It preserves Episodes in which a responsibility condition includes a state transition that is
not caused by the evaluated Agent's current action.

## Contents

- {counts['responsibility_count']} responsibilities and {counts['episode_count']} Episodes.
- {counts['category_counts']['EXOGENOUS_TRIGGER']['responsibilities']} / {counts['category_counts']['EXOGENOUS_TRIGGER']['episodes']} trigger Episodes.
- {counts['category_counts']['EXOGENOUS_INTERFERENCE']['responsibilities']} / {counts['category_counts']['EXOGENOUS_INTERFERENCE']['episodes']} interference Episodes.
- {counts['category_counts']['AUTONOMOUS_SIMPLE_DYNAMICS']['responsibilities']} / {counts['category_counts']['AUTONOMOUS_SIMPLE_DYNAMICS']['episodes']} simplified autonomous-dynamics Episodes.

The original public/private JSONL files are not duplicated or modified. `episodes_index.jsonl` points to
the exact one-based source line and binds each source row by SHA-256.

## Inclusion rule

Include a responsibility only when the environment supplies a non-Agent-caused event, context transition,
or autonomous state evolution that changes when or how the standing responsibility must be fulfilled.
Expected device completion caused by an Agent workflow and known clock/calendar deadlines alone are excluded.

## Claim boundary

This class supports claims about exogenous semantic events, context changes, and simplified autonomous
state evolution. It must not be described as coverage of device failure/degradation, coupled thermal
propagation, weather forcing, grid/power constraints, or high-fidelity physical dynamics.
"""


def build(output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    package = json.loads(SOURCE_PACKAGE_MANIFEST.read_text(encoding="utf-8"))
    _verify_source_hashes(package)
    public_rows = _read_jsonl(SOURCE_PUBLIC)
    private_rows = _read_jsonl(SOURCE_PRIVATE)
    if len(public_rows) != 300 or len(private_rows) != 300:
        raise RuntimeError(f"expected 300 source rows, got public={len(public_rows)} private={len(private_rows)}")

    catalog = json.loads(SOURCE_CATALOG.read_text(encoding="utf-8"))
    catalog_by_id = {row["responsibility_id"]: row for row in catalog["queries"]}
    included_index: list[dict[str, Any]] = []
    included_by_responsibility: dict[str, dict[str, Any]] = {}
    excluded_by_scenario: dict[str, dict[str, Any]] = {}
    all_scenarios: Counter[str] = Counter()

    for line_number, (public, private) in enumerate(zip(public_rows, private_rows, strict=True), start=1):
        if public["episode_id"] != private["episode_id"]:
            raise RuntimeError(f"public/private mismatch at source line {line_number}")
        scenario = private["contract"]["scenario_type"]
        responsibility_id = private["responsibility_id"]
        all_scenarios[scenario] += 1
        if scenario in CATEGORY_BY_SCENARIO:
            category = CATEGORY_BY_SCENARIO[scenario]
            source = catalog_by_id[responsibility_id]
            included_index.append(
                {
                    "schema_version": "exogenous-event-context-index-v1",
                    "episode_id": public["episode_id"],
                    "responsibility_id": responsibility_id,
                    "scenario_type": scenario,
                    "change_category": category,
                    "source_line_number": line_number,
                    "source_public_row_sha256": _row_sha256(public),
                    "source_private_row_sha256": _row_sha256(private),
                    "query": public["query"],
                    "backend_id": private["backend_id"],
                    "backend_fidelity_tier": private["backend_fidelity_tier"],
                }
            )
            entry = included_by_responsibility.setdefault(
                responsibility_id,
                {
                    "responsibility_id": responsibility_id,
                    "scenario_type": scenario,
                    "change_category": category,
                    "natural_query": source["natural_query"],
                    "source_evidence_ids": source["source_evidence_ids"],
                    "episode_ids": [],
                },
            )
            entry["episode_ids"].append(public["episode_id"])
        elif scenario in EXCLUDED_BASELINE_SCENARIOS:
            excluded = excluded_by_scenario.setdefault(
                scenario,
                {
                    "scenario_type": scenario,
                    "reason": EXCLUDED_BASELINE_SCENARIOS[scenario],
                    "responsibility_id": responsibility_id,
                    "episode_ids": [],
                },
            )
            excluded["episode_ids"].append(public["episode_id"])
        else:
            raise RuntimeError(f"unclassified scenario: {scenario}")

    if set(all_scenarios) != set(CATEGORY_BY_SCENARIO) | set(EXCLUDED_BASELINE_SCENARIOS):
        raise RuntimeError("classification does not account for all source scenarios")
    if any(count != 10 for count in all_scenarios.values()):
        raise RuntimeError(f"expected ten Episodes per responsibility: {dict(all_scenarios)}")
    if len(included_by_responsibility) != 26 or len(included_index) != 260:
        raise RuntimeError(
            f"frozen coverage mismatch: responsibilities={len(included_by_responsibility)} Episodes={len(included_index)}"
        )

    category_responsibilities = Counter(row["change_category"] for row in included_by_responsibility.values())
    category_episodes = Counter(row["change_category"] for row in included_index)
    category_counts = {
        category: {
            "responsibilities": category_responsibilities[category],
            "episodes": category_episodes[category],
        }
        for category in sorted(set(CATEGORY_BY_SCENARIO.values()))
    }
    expected_categories = {
        "EXOGENOUS_TRIGGER": {"responsibilities": 15, "episodes": 150},
        "EXOGENOUS_INTERFERENCE": {"responsibilities": 8, "episodes": 80},
        "AUTONOMOUS_SIMPLE_DYNAMICS": {"responsibilities": 3, "episodes": 30},
    }
    if category_counts != expected_categories:
        raise RuntimeError(f"category count mismatch: {category_counts}")

    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "episodes_index.jsonl"
    index_text = "".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in included_index)
    index_path.write_text(index_text, encoding="utf-8")

    manifest: dict[str, Any] = {
        "schema_version": "exogenous-event-context-view-v1",
        "view_name": "v11-exogenous-event-context-change",
        "status": "PASS",
        "source_release": {
            "release_name": package["release_name"],
            "package_manifest_path": str(SOURCE_PACKAGE_MANIFEST.relative_to(ROOT)),
            "package_manifest_sha256": _sha256_bytes(SOURCE_PACKAGE_MANIFEST.read_bytes()),
            "public_path": str(SOURCE_PUBLIC.relative_to(ROOT)),
            "public_sha256": package["artifacts"]["public_episodes"]["sha256"],
            "private_path": str(SOURCE_PRIVATE.relative_to(ROOT)),
            "private_sha256": package["artifacts"]["private_episodes"]["sha256"],
        },
        "classification": {
            "include_definition": (
                "A non-Agent-caused external event, context transition, or autonomous state evolution "
                "changes when or how the standing responsibility must be fulfilled."
            ),
            "excluded_definition": (
                "Expected device completion caused by a workflow and known clock/calendar deadlines alone."
            ),
            "claim_limit": (
                "This view does not establish high-fidelity physical, reliability, weather, or grid dynamics."
            ),
        },
        "statistics": {
            "source_responsibility_count": 30,
            "source_episode_count": 300,
            "responsibility_count": 26,
            "episode_count": 260,
            "excluded_baseline_responsibility_count": 4,
            "excluded_baseline_episode_count": 40,
            "category_counts": category_counts,
        },
        "responsibilities": sorted(included_by_responsibility.values(), key=lambda row: row["responsibility_id"]),
        "excluded_baselines": sorted(excluded_by_scenario.values(), key=lambda row: row["responsibility_id"]),
        "artifacts": {
            "episodes_index": {
                "path": str(index_path.relative_to(ROOT)),
                "bytes": len(index_text.encode()),
                "sha256": _sha256_bytes(index_text.encode()),
            }
        },
    }
    card_path = output_dir / "DATASET_CLASS_CARD.md"
    card_text = _class_card(manifest)
    card_path.write_text(card_text, encoding="utf-8")
    manifest["artifacts"]["dataset_class_card"] = {
        "path": str(card_path.relative_to(ROOT)),
        "bytes": len(card_text.encode()),
        "sha256": _sha256_bytes(card_text.encode()),
    }
    manifest_path = output_dir / "COVERAGE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    manifest = build()
    print(json.dumps({"status": manifest["status"], **manifest["statistics"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
