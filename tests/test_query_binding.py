from query_construction.binding import bind_predicates, resolve


def test_dotted_keys_are_not_nested_paths():
    assert resolve(('*.indoor_dry_bulb_temperature',), {
        'b1.indoor_dry_bulb_temperature': 22,
        'b2.indoor_dry_bulb_temperature': 23,
        'hidden': {'indoor_dry_bulb_temperature': 24},
    }) == [('b1.indoor_dry_bulb_temperature',), ('b2.indoor_dry_bulb_temperature',)]


def test_list_indices_remain_typed():
    result = bind_predicates('d3_ev2gym_electric_competition', 'vehicle.soc',
                             'fraction', 'ge', .8, {'ports': [{'soc': .7}, {'soc': .9}]})
    assert result['bound'] and not result['admitted']
    assert [p.path for p in result['predicates']] == [('ports', 0, 'soc'), ('ports', 1, 'soc')]


def test_missing_explicit_zone_rejects_entire_binding():
    result = bind_predicates('d3_energyplus_shared_ventilation', 'air.co2',
                             'ppm', 'le', 1200, {'zone_a_co2_ppm': 900})
    assert not result['bound'] and result['reason'] == 'public_path_missing'


def test_missing_wildcard_member_is_not_silently_dropped():
    result = bind_predicates('d3_ev2gym_electric_competition', 'vehicle.soc',
                             'fraction', 'ge', .8, {'ports': [{'soc': .9}, {}]})
    assert not result['bound']


def test_invalid_or_empty_observation_is_not_pass():
    for obs in ({}, {'co2_ppm': None}, {'co2_ppm': True}, {'co2_ppm': float('nan')}):
        assert not bind_predicates('energyplus_iaq', 'air.co2', 'ppm', 'le', 1200, obs)['bound']


def test_units_not_converted_and_false_goal_still_binds():
    assert not bind_predicates('energyplus_iaq', 'air.co2', 'percent', 'le', 1,
                               {'co2_ppm': 900})['bound']
    result = bind_predicates('energyplus_iaq', 'air.co2', 'ppm', 'le', 1200, {'co2_ppm': 1500})
    assert result['bound']
    assert result['predicates'][0].read({'co2_ppm': 1500}) is False


def test_category_target_cannot_invent_light_colour():
    obs = {'devices': {'interior_lights': 'off'}}
    for value in ('bright white', 'dim', 1, True):
        result = bind_predicates('d0_exogenous_context', 'light.state', 'category', 'eq', value, obs)
        assert not result['bound'] and result['reason'] == 'target_outside_native_domain'
    assert bind_predicates('d0_exogenous_context', 'light.state', 'category', 'eq', 'on', obs)['bound']
