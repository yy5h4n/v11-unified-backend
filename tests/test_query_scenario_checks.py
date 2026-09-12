from query_construction.scenario_checks import ev_overlap, check_episode_identity


def sample(t, states):
    return {'time_seconds': t, 'observation': {'ports': [{'connected': s} for s in states]}}


def test_sequential_cars_are_not_competition():
    r = ev_overlap([sample(0, [True, False]), sample(1, [False, True])])
    assert r['overlap_observed'] is False and not r['competition_verified']


def test_overlap_does_not_prove_competing_demand():
    r = ev_overlap([sample(0, [True, True])])
    assert r['overlap_observed'] is True and not r['competition_verified']


def test_missing_or_invalid_data_is_unknown():
    for samples in ([], [sample(0, [None])], [sample(0, [True]), sample(0, [True])]):
        assert ev_overlap(samples)['overlap_observed'] is None


def test_other_seed_cannot_supply_task_evidence():
    snapshot = {'route_id': 'ev', 'seed': 0, 'initial': sample(0, [False, False])}
    trajectory = dict(snapshot, seed=3)
    assert 'mismatched_seed' in check_episode_identity(snapshot, trajectory)['issues']


def test_same_seed_with_different_initial_state_rejected():
    snapshot = {'route_id': 'ev', 'seed': 0, 'initial': sample(0, [False, False])}
    trajectory = dict(snapshot, initial=sample(0, [True, False]))
    assert not check_episode_identity(snapshot, trajectory)['compatible']
    result = check_episode_identity(snapshot, snapshot)
    assert result['compatible'] and not result['admitted']


def test_missing_seed_is_not_assumed_zero():
    snapshot = {'route_id': 'ev', 'initial': sample(0, [False])}
    assert not check_episode_identity(snapshot, snapshot)['compatible']
