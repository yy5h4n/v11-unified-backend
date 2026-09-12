"""Mechanism opportunity checks, separate from user-task success."""
from query_construction.temporal import finite


def check_episode_identity(snapshot, trajectory):
    """Necessary association check, never a complete configuration fingerprint.

Exact public initial state is compared as well as route/seed, since identical
seeds do not establish identical runtime configuration. Unexposed differences
still need explicit configuration provenance before admission.
"""
    issues = []
    for field in ('route_id', 'seed'):
        if field not in snapshot or field not in trajectory:
            issues.append('missing_'+field)
        elif type(snapshot[field]) is not type(trajectory[field]) or snapshot[field] != trajectory[field]:
            issues.append('mismatched_'+field)
    for document in (snapshot, trajectory):
        seed = document.get('seed')
        if isinstance(seed, bool) or not isinstance(seed, int):
            issues.append('invalid_seed'); break
    left = snapshot.get('initial', {})
    right = trajectory.get('initial', {})
    if not isinstance(left, dict) or not isinstance(right, dict):
        issues.append('invalid_initial')
    elif not isinstance(left.get('observation'), dict) or not isinstance(right.get('observation'), dict):
        issues.append('missing_initial_observation')
    elif left['observation'] != right['observation']:
        issues.append('mismatched_initial_observation')
    if isinstance(left, dict) and isinstance(right, dict):
        if not finite(left.get('time_seconds')) or not finite(right.get('time_seconds')):
            issues.append('missing_initial_clock')
        elif left['time_seconds'] != right['time_seconds']:
            issues.append('mismatched_initial_clock')
    return {'compatible': not issues, 'issues': issues, 'admitted': False,
            'scope': 'route/seed/public initial equality only; full configuration provenance still required'}


def ev_overlap(samples):
    """Concurrent connected ports are necessary, not sufficient, for competition.

Observed power limits are not proof of simultaneous energy demand; neither
overlap nor overload alone certifies a meaningful responsibility tradeoff.
"""
    overlap = []; invalid = []; previous = None
    for index, sample in enumerate(samples):
        t = sample.get('time_seconds')
        ports = sample.get('observation', {}).get('ports')
        if (not finite(t) or (previous is not None and t <= previous)
                or not isinstance(ports, list) or not ports
                or any(not isinstance(p, dict) or type(p.get('connected')) is not bool for p in ports)):
            invalid.append(index); continue
        previous = t
        connected = [i for i, p in enumerate(ports) if p['connected']]
        if len(connected) >= 2:
            overlap.append({'time_seconds': t, 'ports': connected})
    return {'overlap_observed': bool(overlap) if samples and not invalid else None,
            'overlap_samples': overlap, 'invalid_sample_indices': invalid,
            'competition_verified': False,
            'reason': ('invalid_or_empty_samples' if invalid or not samples else
                       'no_concurrent_vehicles_observed' if not overlap else
                       'overlap_only_joint_demand_and_constraint_effect_unverified'),
            'task_success': None}
