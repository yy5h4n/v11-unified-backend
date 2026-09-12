"""Conservative surface alarms, not a general natural-language entailment oracle.

Alarms require resolution; their absence cannot certify a paraphrase. Backend
binding and native feasibility must remain independent downstream checks.
"""
import re


def surface_review(source, proposal):
    issues = []
    if proposal['decision'] == 'reject':
        return {'issues': [], 'semantic_status': 'model_rejected', 'admitted': False}
    query = proposal['query']
    if re.search(r'^\s*(?:how\s+(?:can|do|should)\s+I\b|what\s+should\s+I\s+do\b)', query, re.I):
        issues.append({'code': 'query_asks_for_advice_not_controller_delegation'})
    complaint = re.search(r'\b(?:annoy\w*|inconvenien\w*|disturb\w*|unnecessary|wast(?:e|es|ing|eful))\b', source.text, re.I)
    if complaint:
        issues.append({'code': 'reported_behavior_may_be_unwanted_review_intent_polarity',
                       'quote': complaint.group(0)})
    workflow = re.search(r'\b(?:set\s+up|create|build|configure)\s+(?:(?:a|an|the)\s+)?(?:rule|workflow|automation)\b', query, re.I)
    if workflow:
        issues.append({'code': 'query_requests_workflow_instead_of_delegation',
                       'quote': workflow.group(0)})
    # Retain numeric literal spelling: transformations such as 15 minutes to a
    # quarter hour are flagged, not automatically accepted or declared wrong.
    numbers = lambda text: set(re.findall(r'(?<!\w)\d+(?:[.:]\d+)*', text))
    original, rewritten = numbers(source.text), numbers(query)
    if original - rewritten:
        issues.append({'code': 'numeric_condition_missing_or_reexpressed',
                       'values': sorted(original - rewritten)})
    if rewritten - original:
        issues.append({'code': 'numeric_condition_added_or_reexpressed',
                       'values': sorted(rewritten - original)})
    if 'rule' in source.collection_kind and any(a['kind'] == 'one_shot' for a in proposal['atoms']):
        issues.append({'code': 'rule_scope_classified_one_shot_requires_evidence'})
    if proposal['assumptions']:
        issues.append({'code': 'interpretations_require_semantic_review',
                       'count': len(proposal['assumptions'])})
    return {'issues': issues, 'semantic_status': 'unresolved', 'admitted': False,
            'scope': 'surface alarms only; no entailment certification'}
