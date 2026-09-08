import hashlib
import json
from pathlib import Path

from unified_compiler import (
    CapabilityStatus,
    ClauseKind,
    PhysicalTopology,
    ResponsibilityLifecycle,
)
from unified_compiler.capability_catalog import (
    CITYLEARN_DHW_STORAGE,
    CITYLEARN_ELECTRICAL_STORAGE,
    CITYLEARN_SOLAR_GENERATION,
    STATIC_CAPABILITIES,
    UNVERIFIED_DOMAIN_STATUS,
)
from unified_compiler.claim_backend_catalog import catalog_document

ROOT = Path(__file__).resolve().parent.parent

LEGACY_LIFECYCLE_NAMES = (
    "LifecycleType",
    "lifecycle_type",
    "STORAGE_CYCLE",
    "THERMAL_REGULATION",
    "SCHEDULED_MOBILITY",
    "CONTINUOUS_QUALITY",
    "ON_OFF_CYCLE",
    "SPATIAL_COVERAGE",
)

UNIVERSAL_DEVICE_INVARIANT_LITERALS = {0.0, 1.0}

API_ONLY_OR_UNVERIFIED = {
    CapabilityStatus.API_VERIFIED_NOT_DATA_PROBED.value,
    CapabilityStatus.CAPABILITY_UNVERIFIED.value,
}


def load_catalog() -> dict:
    return json.loads((ROOT / "scenario_catalog.json").read_text())


def load_claim_backend_catalog() -> dict:
    return json.loads((ROOT / "generated" / "claim_backend_catalog_v1.json").read_text())


def test_citylearn_capabilities_marked_api_verified_not_data_probed():
    for cap in (CITYLEARN_DHW_STORAGE, CITYLEARN_ELECTRICAL_STORAGE, CITYLEARN_SOLAR_GENERATION):
        assert cap.status is CapabilityStatus.API_VERIFIED_NOT_DATA_PROBED
        assert cap.evidence
        assert all(e.startswith("external://citylearn@2.5.0/") for e in cap.evidence)


def test_unverified_domains_marked_unverified():
    assert UNVERIFIED_DOMAIN_STATUS
    assert all(s is CapabilityStatus.CAPABILITY_UNVERIFIED for s in UNVERIFIED_DOMAIN_STATUS.values())


def test_scenario_catalog_well_formed():
    catalog = load_catalog()
    scenarios = catalog["scenarios"]
    assert len(scenarios) >= 10
    required_keys = {
        "scenario_id", "domain", "supported_responsibility_lifecycles",
        "physical_topology", "state_variables",
        "exogenous_events", "legal_action_types", "evaluator_clause_templates",
        "required_backend_capabilities", "implementation_status", "evidence",
    }
    ids = set()
    for sc in scenarios:
        assert required_keys <= set(sc), sc["scenario_id"]
        assert "lifecycle_type" not in sc
        assert sc["supported_responsibility_lifecycles"]
        for lifecycle in sc["supported_responsibility_lifecycles"]:
            ResponsibilityLifecycle(lifecycle)
        PhysicalTopology(sc["physical_topology"])
        CapabilityStatus(sc["implementation_status"])
        for clause in sc["evaluator_clause_templates"]:
            ClauseKind(clause["kind"])
        if sc["implementation_status"] == "CAPABILITY_UNVERIFIED":
            pass
        else:
            assert sc["evidence"], f"{sc['scenario_id']} claims status without evidence"
        ids.add(sc["scenario_id"])
    assert len(ids) == len(scenarios)


def test_responsibility_lifecycle_and_physical_topology_are_separate_taxonomies():
    lifecycle_values = {e.value for e in ResponsibilityLifecycle}
    topology_values = {e.value for e in PhysicalTopology}
    assert lifecycle_values.isdisjoint(topology_values)
    for sc in load_catalog()["scenarios"]:
        assert set(sc["supported_responsibility_lifecycles"]) <= lifecycle_values
        assert sc["physical_topology"] in topology_values
        assert sc["physical_topology"] not in lifecycle_values
        assert not set(sc["supported_responsibility_lifecycles"]) & topology_values


def test_no_legacy_lifecycle_names_anywhere():
    files = [
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and p.suffix in {".py", ".md", ".json"}
        and "__pycache__" not in p.parts
        and ".pytest_cache" not in p.parts
        and p.name != "test_catalogs.py"
    ]
    for f in files:
        text = f.read_text()
        for legacy in LEGACY_LIFECYCLE_NAMES:
            assert legacy not in text, f"{f.name} still references legacy {legacy!r}"


def _numeric_literals(node):
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield float(node)
    elif isinstance(node, dict):
        for v in node.values():
            yield from _numeric_literals(v)
    elif isinstance(node, (list, tuple)):
        for v in node:
            yield from _numeric_literals(v)


def test_api_only_and_unverified_scenarios_have_no_numeric_evaluator_thresholds():
    for sc in load_catalog()["scenarios"]:
        if sc["implementation_status"] not in API_ONLY_OR_UNVERIFIED:
            continue
        for clause in sc["evaluator_clause_templates"]:
            for literal in _numeric_literals(clause.get("params", {})):
                assert literal in UNIVERSAL_DEVICE_INVARIANT_LITERALS, (
                    f"{sc['scenario_id']}/{clause['clause_id']} asserts numeric "
                    f"threshold {literal}; only universal device invariants "
                    f"{sorted(UNIVERSAL_DEVICE_INVARIANT_LITERALS)} may be literal, "
                    "responsibility thresholds must stay symbolic"
                )


def test_no_runtime_or_data_directory():
    assert not (ROOT / "runtime").exists()
    assert not (ROOT / "data").exists()


def test_no_executable_status_without_verification():
    catalog = load_catalog()
    allowed = {s.value for s in CapabilityStatus}
    for sc in catalog["scenarios"]:
        assert sc["implementation_status"] in allowed
        assert sc["implementation_status"] != "EXECUTABLE"


def test_static_capabilities_unique_ids():
    ids = [c.capability_id for c in STATIC_CAPABILITIES]
    assert len(ids) == len(set(ids))


def test_claim_backend_catalog_is_a_deterministic_authoritative_projection():
    generated = load_claim_backend_catalog()
    assert generated["catalog_id"] == "claim_backend_catalog_v1"
    # JSON materializes tuple-valued catalog fields as lists.  Normalize the
    # authoritative in-memory projection through the same JSON boundary before
    # comparing it with the generated artifact.
    assert generated["catalog"] == json.loads(json.dumps(catalog_document()))
    canonical = json.dumps(
        generated["catalog"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert generated["content_sha256"] == hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def test_claim_backend_statuses_match_verified_evidence_and_fail_closed_scope():
    generated = load_claim_backend_catalog()["catalog"]
    routes = {
        route["route_id"]: route
        for backend in generated["backends"]
        for route in backend["routes"]
    }

    hvac = routes["citylearn_hvac_native_replay_verified"]
    assert hvac["status"] == "EXECUTABLE_REPLAY_VERIFIED"
    assert hvac["release_ready"] is True
    assert any("full_episode_on_pinned_runtime" in ref for ref in hvac["claim_gate"]["evidence_refs"])
    assert any("changed_runtime_asset" in ref for ref in hvac["claim_gate"]["evidence_refs"])
    assert "SHA-256" in hvac["claim_gate"]["semantics"] or any(
        "SHA-256" in check for check in hvac["claim_gate"]["required_checks"]
    )
    assert not any("V10RuntimeBridge" in check for check in hvac["claim_gate"]["required_checks"])

    sustain = routes["sustaingym_bounded_cooling_replay_verified"]
    assert sustain["status"] == "EXECUTABLE_REPLAY_VERIFIED"
    assert sustain["release_ready"] is True
    gate = json.loads(
        (ROOT / "generated" / "sustaingym_replay_gate_v1.json").read_text()
    )
    assert gate["verified"] is True
    assert gate["status"] == "EXECUTABLE_REPLAY_VERIFIED"
    assert gate["all_replays_completed"] is True
    assert gate["all_replays_deterministic"] is True
    assert set(gate["replays"]) == {"cooling_a", "cooling_b", "off_a", "off_b"}
    assert all(
        replay["temperature_evidence"]["finite"]
        and replay["temperature_evidence"]["physically_plausible"]
        for replay in gate["replays"].values()
    )
    assert "generated/sustaingym_replay_gate_v1.json" in sustain["claim_gate"]["evidence_refs"]
    assert any("production" in blocker for blocker in sustain["blockers"])

    boptest = routes["boptest_rest_api_verified"]
    assert boptest["status"] == "API_VERIFIED_NOT_DATA_PROBED"
    assert boptest["release_ready"] is False
    assert any("no live BOPTEST service probed" in blocker for blocker in boptest["blockers"])

    t2 = generated["t2_workflow"]
    assert t2["kind"] == "control_only_workflow"
    assert t2["primary_constructor"] is None
    assert "no physical backend runtime" in t2["semantics"]
