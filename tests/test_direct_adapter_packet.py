import json

from build_direct_adapter_packet import PACKET_STATUS, build


def test_direct_packet_has_all_candidates_and_identity() -> None:
    artifact = build()
    assert artifact["candidate_count"] == 25
    rows = artifact["candidates"]
    assert len({r["responsibility_id"] for r in rows}) == 25
    catalog = json.loads(__import__("pathlib").Path("responsibility_ai_coding_v1/NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json").read_text())
    source = {r["responsibility_id"]: r for r in catalog["queries"]}
    for r in rows:
        s = source[r["responsibility_id"]]
        assert (r["responsibility"], r["query"], r["lifecycle"]) == (s["delegated_outcome"], s["natural_query"], s["lifecycle"])
        for key in ("physical_topology", "required_observations", "required_actions", "dynamics_clauses", "evaluator_clauses", "opportunity_predicate"):
            assert r[key]
        assert r["status"] == PACKET_STATUS


def test_backend_semantics_are_separated() -> None:
    for r in build()["candidates"]:
        text = (r["query"] + " " + r["responsibility"]).lower()
        if any(x in text for x in ("humidity", "healthy", "ventilat", "air inside")):
            assert "BOPTEST" not in r["primary_backends"]
        if any(x in text for x in ("lighting", "lit", "bright", "blind", "daylight")):
            assert "BOPTEST" not in r["primary_backends"]


def test_packet_makes_no_replay_or_gold_action_claims() -> None:
    raw = json.dumps(build(), ensure_ascii=False).lower()
    assert "episode" not in raw
    assert "gold action" not in raw


def test_probe_readiness_is_fail_closed() -> None:
    rows = {r["responsibility_id"]: r for r in build()["candidates"]}
    assert sum(r["adapter_readiness"] == "DATA_SOURCE_PROBE_ELIGIBLE" for r in rows.values()) == 23
    assert rows["rd_f0bc2c668699"]["adapter_readiness"] == "MODEL_VARIANT_REQUIRED"
    assert rows["rd_split_39d32cc7fd22"]["adapter_readiness"] == "CONTRACT_UNDERSPECIFIED"
    assert rows["rd_split_39d32cc7fd22"]["primary_backends"] == []
