def test_multiroom_compiler_identity():
    import compile_simuhome_multiroom_evening_episodes as c
    assert c.RID == "rd_split_b8457e559b4d"
    assert c.QUERY_ID == "si_split_b8457e559b4d"

def test_scan_candidates_frozen_diagnostics():
    import compile_simuhome_multiroom_evening_episodes as c
    from collections import Counter
    candidates, failures = c.scan_candidates()
    counts = Counter(row["code"] for row in failures)
    # 27 distinct initial multi-room opportunities are found before the
    # causal fail-closed gate; 17 contain a heat-pump-only room above target.
    assert len(candidates) == 10
    assert counts["NOT_EVENING"] == 480
    assert counts["FEWER_THAN_TWO_ELIGIBLE_ROOMS"] == 82
    assert counts["DUPLICATE_SOURCE_CONFIG"] == 11
    assert counts["HEAT_PUMP_CANNOT_IMPROVE_ABOVE_TARGET"] == 17
    assert len(candidates) + sum(counts.values()) == 600
    assert all(len(row["room_ids"]) >= 2 for row in candidates)
    assert all(row["room_ids"] == sorted(row["room_ids"]) for row in candidates)

def test_public_leakage_check_fails_on_nested_gold():
    import compile_simuhome_multiroom_evening_episodes as c
    assert c.public_payload_safe({"episode_id":"ok","metadata":{"safe":True}})
    assert not c.public_payload_safe({"episode_id":"bad","metadata":{"gold_actions":[]}})
