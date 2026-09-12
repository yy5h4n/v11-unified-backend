"""Explicit atomic derivation, never a claim to fulfill the complete source."""
from copy import deepcopy


def project_atoms(proposal, indices, *, query, justification):
    atoms = proposal.get('atoms')
    if proposal.get('decision') != 'candidate' or not isinstance(atoms, list) or not atoms:
        raise ValueError('candidate atoms required')
    if not isinstance(indices, list) or not indices or any(
            isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < len(atoms) for i in indices):
        raise ValueError('explicit nonempty atom selection required')
    if len(set(indices)) != len(indices): raise ValueError('duplicate atom index')
    if not all(isinstance(x, str) and x.strip() for x in (query, justification)):
        raise ValueError('derived query and decomposition justification required')
    result = deepcopy(proposal)
    result.update(query=query, atoms=[deepcopy(atoms[i]) for i in indices])
    return {'proposal': result, 'derivation': {
        'type': 'explicit_atomic_projection', 'parent_source_id': proposal['source_id'],
        'parent_query': proposal['query'], 'selected_atom_indices': list(indices),
        'unselected_atoms': [{'index': i, 'atom': deepcopy(a)} for i, a in enumerate(atoms) if i not in indices],
        'justification': justification, 'fulfills_complete_parent': False,
        'semantic_independence': 'must_check_shared_conditions_exceptions_and_coupling',
    }, 'admitted': False}
