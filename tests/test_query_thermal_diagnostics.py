from tools.run_thermal_query_diagnostics import CONDITIONS, _action


def test_predeclared_contract_checks_both_bounds_for_all_rooms():
    assert {condition["goal"]["comparator"] for condition in CONDITIONS} == {"ge", "le"}
    assert all(condition["start_seconds"] == 60 and condition["end_seconds"] == 3600 for condition in CONDITIONS)
    assert all("product source supplies no numeric" in condition["parameter_origin"] for condition in CONDITIONS)


def test_counterpolicies_are_distinct_and_feedback_uses_observation():
    warm = {"room_a_temperature_c": 20.0, "room_b_temperature_c": 20.0}
    cold = {"room_a_temperature_c": 18.0, "room_b_temperature_c": 18.0}
    assert _action("idle", 0, warm) == 0
    assert _action("one_shot", 0, warm) == 0.5
    assert _action("one_shot", 1, warm) == 0
    assert _action("fixed_0_3", 20, cold) == 0.3
    assert _action("feedback", 20, cold) > _action("feedback", 20, warm)
