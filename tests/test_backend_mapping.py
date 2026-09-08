from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

from build_backend_mapping import STATUSES, build, resolve_ref, serialized_outputs
from build_responsibility_capability_contracts import (
    build as build_contracts,
    serialized_output as serialized_contract_output,
    validate_source_refs,
)
from responsibility_backend_matcher import (
    BackendProfile,
    CapabilityVector,
    ResponsibilityRequirement,
    compile_requirement,
    default_backend_profiles,
    match_requirement,
)


ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "responsibility_ai_coding_v1" / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"


def catalog_rows():
    return json.loads(CATALOG.read_text())["queries"]


def contract_rows():
    return build_contracts()["contracts"]


def test_contract_catalog_and_mapping_preserve_all_identities() -> None:
    source_ids = [row["responsibility_id"] for row in catalog_rows()]
    contracts = contract_rows()
    _inventory, mapping, _report = build()
    assert len(source_ids) == len(set(source_ids)) == 129
    assert [row["responsibility_id"] for row in contracts] == source_ids
    assert [row["responsibility_id"] for row in mapping["mappings"]] == source_ids
    assert set(mapping["summary"]["status_counts"]) == STATUSES == {"FULL", "PARTIAL", "UNSUPPORTED"}


def test_full_requires_reviewed_contract_and_is_limited_to_instance_proven_scope() -> None:
    _inventory, mapping, _report = build()
    full = [row for row in mapping["mappings"] if row["support_status"] == "FULL"]
    assert {row["natural_query"] for row in full} == {
        "Keep the kitchen warm in the evening.",
        "Keep the rooms comfortably warm in the evening.",
    }
    assert {row["contract_status"] for row in full} == {"REVIEWED"}
    assert {row["selected_backend"] for row in full} == {"simuhome_thermal_harness_v2"}


def test_generic_house_scope_stays_partial_until_scope_contract_is_reviewed() -> None:
    _inventory, mapping, _report = build()
    row = next(item for item in mapping["mappings"] if item["delegated_outcome"] == "comfortable indoor temperature")
    assert row["support_status"] == "PARTIAL"
    assert row["contract_status"] == "PROVISIONAL_AI_STRUCTURED"
    assert "spatial:scope.unresolved" in row["missing_capabilities"]
    assert "readiness:reviewed_responsibility_contract" in row["missing_capabilities"]


def test_matcher_is_invariant_to_query_and_outcome_paraphrases() -> None:
    rows = catalog_rows()
    contracts = {row["responsibility_id"]: row for row in contract_rows()}
    row = next(item for item in rows if item["delegated_outcome"] == "comfortable indoor temperature")
    changed = {**row, "natural_query": "Could it feel pleasant in here?", "delegated_outcome": "pleasant indoor climate"}
    assert compile_requirement(row, contracts[row["responsibility_id"]]) == compile_requirement(changed, contracts[row["responsibility_id"]])


def test_surface_words_lit_and_visibility_cannot_create_lighting_support() -> None:
    _inventory, mapping, _report = build()
    by_outcome = {row["delegated_outcome"]: row for row in mapping["mappings"]}
    for outcome in (
        "Ensure a lit unattended stove is shut off promptly",
        "visibility of the infant during possible distress",
    ):
        assert by_outcome[outcome]["support_status"] == "UNSUPPORTED"
        assert by_outcome[outcome]["compiled_domain"] is None


def test_dynamic_context_dependencies_enter_backend_requirements() -> None:
    contracts = {row["responsibility_id"]: row for row in contract_rows()}
    row = next(item for item in catalog_rows() if item["delegated_outcome"] == "adequate entrance lighting for evening arrival")
    contract = contracts[row["responsibility_id"]]
    assert "occupancy" in contract["capabilities"]["observations"]
    assert "occupancy.change" in contract["capabilities"]["events"]


def test_partial_requires_overlap_in_every_causal_core() -> None:
    weak = ResponsibilityRequirement(
        responsibility_id="synthetic",
        domain="thermal_temperature",
        capabilities=CapabilityVector(
            observations=frozenset({"clock", "unknown.state"}), actions=frozenset({"unknown.action"}),
            dynamics=frozenset({"unknown.dynamics"}), evaluators=frozenset({"unknown.evaluator"}),
            events=frozenset({"clock.window"}), spatial=frozenset({"room.addressable"}), temporal=frozenset({"closed_loop"}),
        ),
        episode_inputs=(), contract_status="PROVISIONAL_AI_STRUCTURED", source_refs=("test",),
    )
    assert match_requirement(weak, default_backend_profiles())["status"] == "UNSUPPORTED"


def test_exact_capabilities_upgrade_only_when_contract_and_backend_are_reviewed() -> None:
    requirement = ResponsibilityRequirement(
        responsibility_id="synthetic", domain="synthetic_domain",
        capabilities=CapabilityVector(
            observations=frozenset({"state"}), actions=frozenset({"act"}), dynamics=frozenset({"transition"}),
            events=frozenset({"event"}), evaluators=frozenset({"score"}), spatial=frozenset({"entity"}), temporal=frozenset({"closed_loop"}),
        ),
        episode_inputs=(), contract_status="REVIEWED", source_refs=("test",),
    )
    backend = BackendProfile(
        backend_id="exact", domains=frozenset({"synthetic_domain"}), capabilities=requirement.capabilities,
        verification_status="TEST_REVIEWED", harness_v2_verified=True, evidence_refs=("project://tests/test_backend_mapping.py",),
    )
    assert match_requirement(requirement, [backend])["status"] == "FULL"
    assert match_requirement(replace(requirement, contract_status="PROVISIONAL_AI_STRUCTURED"), [backend])["status"] == "PARTIAL"
    assert match_requirement(requirement, [replace(backend, harness_v2_verified=False)])["status"] == "PARTIAL"


def test_backend_evidence_exists_and_process_counts_are_recomputed() -> None:
    inventory, _mapping, _report = build()
    by_id = {row["backend_id"]: row for row in inventory["backends"]}
    for row in inventory["backends"]:
        assert all(resolve_ref(reference).is_file() for reference in row["evidence_refs"])
    assert by_id["energyplus_residential_thermal_legacy"]["process_count"] == 30
    assert by_id["energyplus_residential_thermal_legacy"]["process_count_source"] == (
        "project://generated/energyplus_responsibility_release_v1/replay_gate.json",
        "unique_physical_process_id",
    )
    assert by_id["citylearn_battery_pv_v1"]["process_count"] == 48
    assert all("ev2gym" not in row["backend_id"] for row in inventory["backends"])


def test_matcher_source_does_not_parse_language_fields() -> None:
    source = (ROOT / "responsibility_backend_matcher.py").read_text()
    assert "natural_query" not in source
    assert "delegated_outcome" not in source


def test_outputs_are_deterministic_and_current() -> None:
    first = serialized_outputs()
    second = serialized_outputs()
    assert first == second
    for path, content in first.items():
        assert path.read_text(encoding="utf-8") == content


def test_contract_source_refs_exist_and_artifact_is_current() -> None:
    artifact = build_contracts()
    validate_source_refs(artifact)
    path = ROOT / "responsibility_ai_coding_v1" / "RESPONSIBILITY_CAPABILITY_CONTRACTS_V1.json"
    assert path.read_text(encoding="utf-8") == serialized_contract_output()
