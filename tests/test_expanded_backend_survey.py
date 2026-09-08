from build_expanded_backend_survey import build


def test_expanded_backend_survey_is_complete_and_claim_bounded() -> None:
    artifact, _report = build()
    mappings = artifact["mappings"]
    assert len(mappings) == len({x["responsibility_id"] for x in mappings}) == 129
    assert artifact["summary"]["current_v11_verified_responsibilities"] == 2
    assert artifact["summary"]["potential_direct_adapter_responsibilities"] == 25
    assert artifact["summary"]["potential_composite_or_extension_responsibilities"] == 33
    assert artifact["summary"]["potential_physical_path_total"] == 58
    assert artifact["summary"]["event_or_logic_only_not_satisfied"] == 71
    assert artifact["research_status"] == "SEARCH_EVIDENCE_ONLY_NOT_INSTALLED_OR_REPLAY_VERIFIED"
    assert all(x["primary_backends"] for x in mappings if not x["not_backend_satisfied"])
    assert all(not x["primary_backends"] and x["not_backend_satisfied"] for x in mappings if x["potential_status"] == "EVENT_OR_LOGIC_ONLY_NOT_PHYSICALLY_SATISFIED")
    evidence = {x["backend"] for x in artifact["candidate_backend_evidence"]}
    assert {"EnergyPlus", "BOPTEST", "GridLAB-D", "FDS", "EPANET", "BEHAVIOR-1K/OmniGibson"} <= evidence
