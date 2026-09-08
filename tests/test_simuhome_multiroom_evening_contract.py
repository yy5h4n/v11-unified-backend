import json
from pathlib import Path

def test_multiroom_contract_frozen():
    c = json.loads((Path(__file__).parents[1]/"contracts/simuhome_multiroom_evening_warmth_v1.json").read_text())
    assert c["responsibility_id"] == "rd_split_b8457e559b4d"
    assert c["natural_query"] == "Keep the rooms comfortably warm in the evening."
    assert c["profile"]["target_c"] == 22.0
    assert c["profile"]["temperature_band_c"] == {"lower": 20.0, "upper": 24.0}
    assert c["lifecycle"] == "MAINTAIN"
    assert c["query_id"] == "si_split_b8457e559b4d"
    assert "worst-room" in c["soft_metric"]
    assert c["opportunity_predicate"]["minimum_worst_room_mae_delta_c"] == 0.1
    assert c["opportunity_predicate"]["exact_replicates_per_certification_policy"] == 2
    assert c["scope"]["zone_must_be_explicit"] is True
