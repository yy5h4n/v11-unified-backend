"""Resolve capability paths against public observations, never hidden state.

Resolution establishes measurability only. It does not establish control,
semantic entailment, feasibility, or admission. Wildcards bind every observed
member; callers must retain the returned concrete paths in the public contract.
"""
import fnmatch

from query_construction.capabilities import CARDS
from query_construction.temporal import Predicate


def resolve(pattern, observation):
    """Return concrete typed paths. Dotted native dictionary keys stay atomic."""
    nodes = [((), observation)]
    for component in pattern:
        next_nodes = []
        for path, value in nodes:
            before = len(next_nodes)
            if isinstance(value, dict):
                for key in sorted(value):
                    if isinstance(key, str) and fnmatch.fnmatchcase(key, component):
                        next_nodes.append((path + (key,), value[key]))
            elif isinstance(value, list) and component == '*':
                next_nodes.extend((path + (i,), item) for i, item in enumerate(value))
            if len(next_nodes) == before:
                return []
        nodes = next_nodes
    return [path for path, _ in nodes]


def bind_predicates(route_id, quantity, unit, comparator, target, observation):
    """Bind all advertised members or reject; never silently drop a missing zone."""
    if route_id not in CARDS:
        raise ValueError('unknown formal route')
    if not isinstance(observation, dict):
        raise ValueError('public observation object required')
    card = CARDS[route_id][1].get(quantity)
    if card is None:
        return {'bound': False, 'reason': 'quantity_unsupported', 'admitted': False}
    if card.unit != unit:
        return {'bound': False, 'reason': 'unit_mismatch', 'admitted': False}
    if card.allowed_values is not None and not any(type(target) is type(v) and target == v for v in card.allowed_values):
        return {'bound': False, 'reason': 'target_outside_native_domain',
                'allowed_values': list(card.allowed_values), 'admitted': False}
    predicates = []
    for pattern in card.paths:
        paths = resolve(pattern, observation)
        if not paths:
            return {'bound': False, 'reason': 'public_path_missing',
                    'pattern': list(pattern), 'admitted': False}
        for path in paths:
            predicate = Predicate(path, comparator, target)
            if predicate.read(observation) is None:
                return {'bound': False, 'reason': 'public_value_unusable',
                        'path': list(path), 'admitted': False}
            if predicate not in predicates:
                predicates.append(predicate)
    return {'bound': True, 'predicates': predicates, 'unit': unit,
            'scope': 'observed_members_only', 'admitted': False}
