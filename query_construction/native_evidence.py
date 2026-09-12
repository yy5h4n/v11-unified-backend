"""Recheck saved native outcomes before presenting construction progress.

Passing sampled outcomes is not semantic certification or claim admission.
Policy names describe diagnostics, not proof of how those policies executed.
"""
import json
from .contracts import compile_contract
from .temporal import evaluate


def canonical(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def recheck(public, samples):
    if not samples:
        raise ValueError('native samples required')
    conditions = {}
    for row in public['conditions']:
        condition = row['condition']
        key = condition['id']
        if key in conditions and conditions[key] != condition:
            raise ValueError('conflicting conditions')
        conditions[key] = condition
    compiled = compile_contract(public['route_id'], list(conditions.values()), samples[0]['observation'])
    if canonical(compiled['public_contract']) != canonical(public):
        raise ValueError('contract needs explicit migration before current-code scoring')
    return evaluate(compiled['clauses'], samples,
                    cadence_seconds=public['cadence_seconds'],
                    horizon_seconds=public['horizon_seconds'])


def summarize_native(native, public, query, diagnostics=None):
    if native.get('user_responsibility') != query or canonical(native.get('public_contract')) != canonical(public):
        raise ValueError('native run belongs to another query or contract')
    if native.get('route_id') != public['route_id']:
        raise ValueError('native route mismatch')
    if native.get('episode_identity', {}).get('compatible') is not True:
        raise ValueError('native episode identity not established')
    samples = [native['initial']] + native['transitions']
    evaluation = recheck(public, samples)
    if canonical(evaluation) != canonical(native.get('contract_evaluation')):
        raise ValueError('saved native score differs from current trajectory evaluation')
    rows = []
    if diagnostics is not None:
        if diagnostics.get('route_id') != native['route_id'] or diagnostics.get('seed') != native['seed'] or canonical(diagnostics.get('public_contract')) != canonical(public):
            raise ValueError('diagnostic scenario or contract mismatch')
        names = set()
        for run in diagnostics['runs']:
            name = run['policy']
            if name in names:
                raise ValueError('duplicate diagnostic policy')
            names.add(name)
            if not run['samples'] or canonical(run['samples'][0]) != canonical(native['initial']):
                raise ValueError('diagnostic initial state mismatch')
            score = recheck(public, run['samples'])
            if canonical(score) != canonical(run['evaluation']):
                raise ValueError('saved diagnostic score differs from trajectory')
            rows.append({'policy': name, 'evaluation': score})
    constant_pass = any(r['policy'] in {'idle', 'always_on'} and
                        r['evaluation'].get('task_success') is True for r in rows)
    return {'evaluation': evaluation, 'diagnostics': rows,
            'classification': ('calibration_only_constant_baseline_passes' if constant_pass
                               else 'claim_validation_pending'),
            'model_usage': native.get('model_usage'), 'admitted': False,
            'scope': 'Recomputed sampled outcomes; baseline implementation, source semantics and mechanism necessity are not certified'}
