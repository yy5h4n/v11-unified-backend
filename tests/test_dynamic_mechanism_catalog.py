from __future__ import annotations

from build_dynamic_mechanism_catalog import BACKEND_REPLAY_VERIFIED, build_catalog


def test_d3_routes_are_independently_gated_with_explicit_evidence_layers() -> None:
    catalog = build_catalog()
    d3 = next(item for item in catalog["mechanisms"] if item["mechanism_id"].startswith("D3_"))
    routes = d3["routes"]
    assert [route["route_id"] for route in routes] == [
        "citylearn_multi_system",
        "citylearn_multibuilding_competition",
        "wntr_water_competition",
        "modelica_shared_heat",
        "ev2gym_electric_competition",
        "energyplus_shared_ventilation",
    ]
    assert all(route["mechanism_status"] == BACKEND_REPLAY_VERIFIED for route in routes)
    assert all(route["replay"]["verified"] for route in routes)
    assert all(route["agent"]["verified"] for route in routes)
    assert all(route["strong_coupling"]["verified"] for route in routes)
    assert d3["summary"] == {
        "route_count": 6,
        "verified_route_count": 6,
        "pending_route_count": 0,
        "route_ids": [route["route_id"] for route in routes],
    }
    assert d3["boundary"]["pending_routes_do_not_inherit"] is True
