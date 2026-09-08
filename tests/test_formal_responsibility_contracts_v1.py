from build_formal_responsibility_contracts_v1 import build


def test_route_has_more_than_30_distinct_source_responsibilities():
    result = build()
    routes = result["routes"]
    assert result["responsibility_count"] >= 30
    assert len({row["responsibility_id"] for row in routes}) == len(routes)
    assert all(row["source_evidence_ids"] for row in routes)


def test_every_route_has_complete_operational_contract_and_full_claims_are_workflow_only():
    result = build()
    required = {
        "required_observations", "required_actions", "required_dynamics", "required_events",
        "required_evaluator_primitives", "resource_semantics", "authorization", "lifecycle",
        "beneficiary", "delegated_outcome", "release_condition_required",
    }
    for row in result["routes"]:
        assert required <= set(row["contract"])
        assert all(row["contract"][key] for key in required - {"release_condition_required"})
        if row["backend_route"] == "household_workflow_t2":
            assert row["support_status"] == "FULL"
            assert row["contract_status"] == "MACHINE_CONTRACT_V2"
        else:
            assert row["support_status"] == "PARTIAL"
            assert row["contract_status"] == "FROZEN_PENDING_EXECUTABLE_EVIDENCE"


def test_routes_cover_multiple_backends_and_fidelity_tiers():
    result = build()
    assert len({row["backend_id"] for row in result["routes"]}) == 4
    assert {row["backend_fidelity_tier"] for row in result["routes"]} == {"T1", "T2"}
