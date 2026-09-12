"""Compile public operational conditions and evaluator clauses together.

Inputs are explicit benchmark conditions, never claimed to be human quotations.
This compiler proves neither source entailment nor native control feasibility.
"""
from dataclasses import asdict
import math

from query_construction.binding import bind_predicates
from query_construction.temporal import Clause, finite
from unified_compiler.route_registry import route_metadata


def compile_contract(route_id, conditions, observation):
    clock = route_metadata(route_id)
    cadence, horizon = clock['cadence_seconds'], clock['horizon_seconds']
    if not isinstance(conditions, list) or not conditions:
        raise ValueError('nonempty explicit public conditions required')
    clauses, public, ids = [], [], set()
    def bind(spec):
        if not isinstance(spec, dict) or set(spec) != {'quantity', 'unit', 'comparator', 'target'}:
            raise ValueError('quantity predicate requires exact fields')
        result = bind_predicates(route_id, spec['quantity'], spec['unit'],
                                 spec['comparator'], spec['target'], observation)
        if not result['bound']:
            raise ValueError('predicate binding rejected: '+result['reason'])
        return result['predicates']
    for condition in conditions:
        required = {'id', 'kind', 'goal', 'start_seconds', 'end_seconds', 'parameter_origin'}
        optional = {'active', 'trigger', 'within_seconds', 'activation_grace_seconds', 'trigger_at_window_entry'}
        if not isinstance(condition, dict) or not required <= set(condition) or set(condition)-required-optional:
            raise ValueError('unexpected condition fields')
        identifier = condition['id']
        if not isinstance(identifier, str) or not identifier or identifier in ids:
            raise ValueError('unique condition IDs required')
        ids.add(identifier)
        if not isinstance(condition['parameter_origin'], str) or not condition['parameter_origin'].strip():
            raise ValueError('disclose origin of operational parameters')
        for key in ('start_seconds', 'end_seconds', 'within_seconds', 'activation_grace_seconds'):
            if key not in condition: continue
            value = condition[key]
            if not finite(value) or value < 0 or not math.isclose(value/cadence, round(value/cadence)):
                raise ValueError('condition time must align with native sample clock; no rounding')
        if condition['end_seconds'] > horizon:
            raise ValueError('condition exceeds native horizon')
        kwargs = {}
        if 'trigger_at_window_entry' in condition:
            kwargs['trigger_at_window_entry'] = condition['trigger_at_window_entry']
        for field in ('active', 'trigger'):
            if field in condition:
                values = bind(condition[field])
                if len(values) != 1:
                    raise ValueError('multi-entity trigger/activation needs explicit association')
                kwargs[field] = values[0]
        if 'within_seconds' in condition:
            kwargs['within_seconds'] = condition['within_seconds']
        if 'activation_grace_seconds' in condition:
            kwargs['activation_grace_seconds'] = condition['activation_grace_seconds']
        goals = bind(condition['goal'])
        for index, goal in enumerate(goals):
            clause = Clause(f'{identifier}:{index}', condition['kind'], goal,
                            condition['start_seconds'], condition['end_seconds'], **kwargs)
            clauses.append(clause)
            public.append({'condition': dict(condition), 'compiled_clause': asdict(clause)})
    return {'clauses': clauses, 'public_contract': {
        'route_id': route_id, 'cadence_seconds': cadence, 'horizon_seconds': horizon,
        'conditions': public, 'windows': 'closed; native samples only',
        'response_semantics': 'rising edge; true at window entry also triggers unless trigger_at_window_entry=false explicitly establishes baseline only; explicit inactive releases pending; unknown never pauses deadline',
        'activation_grace_semantics': 'explicit per-clause grace only at activation; maintain every sample from deadline onward; repeated active state does not reset grace',
        'entity_scope': 'concrete members observed at binding time',
        'missing_or_censored': 'not a success',
    }, 'admitted': False, 'feasibility': 'not_established'}
