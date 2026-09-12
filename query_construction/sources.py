"""Traceability and leakage-resistant source grouping. No model self-certification."""
from dataclasses import dataclass
import hashlib
import unicodedata


@dataclass(frozen=True)
class SourceRecord:
    id: str
    corpus: str
    locator: str
    text: str
    participant: str
    collection_kind: str
    license_status: str

    def __post_init__(self):
        for name in ('id', 'corpus', 'locator', 'text', 'participant', 'collection_kind', 'license_status'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f'missing source {name}; do not fabricate it')


def validate_span(source, span):
    """Span offsets use Python Unicode code points, not byte/JS UTF-16 offsets."""
    if not isinstance(span, dict) or set(span) != {'source_id', 'start', 'end', 'quote'}:
        raise ValueError('span requires exact source_id/start/end/quote fields')
    start, end = span['start'], span['end']
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)):
        raise ValueError('span offsets must be integers')
    if span['source_id'] != source.id or not 0 <= start < end <= len(source.text):
        raise ValueError('invalid source locator or span bounds')
    if not isinstance(span['quote'], str) or not span['quote'].strip() or source.text[start:end] != span['quote']:
        raise ValueError('quoted evidence is not an exact source substring')
    return {'traceable': True, 'semantic_entailment': 'not_established_by_span_matching'}


def normalized_text(text):
    return ' '.join(unicodedata.normalize('NFKC', text).casefold().split())


def partition(records, *, development_ids, salt, holdout_fraction=0.25, related_groups=None):
    """Union participant and duplicate-text groups before assigning splits.

    Requires genuine participant/group keys from upstream. Unknown identity must
    be assigned a conservative corpus-level group upstream, never fake people.
    Freeze source inventory and split output before interpreting held-out text.
    This is exact normalized duplication control, not paraphrase deduplication.
    """
    if not salt or isinstance(holdout_fraction, bool) or not 0 < holdout_fraction < 1:
        raise ValueError('explicit salt and fraction in (0,1) required')
    by_id = {r.id: r for r in records}
    if len(by_id) != len(records) or not set(development_ids) <= set(by_id):
        raise ValueError('duplicate IDs or unknown previously-seen records')
    related_groups = related_groups or []
    for group in related_groups:
        if not group or not set(group) <= set(by_id):
            raise ValueError('empty related group or unknown record ID')
    parent = {r.id: r.id for r in records}
    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    def union(a, b):
        a, b = root(a), root(b)
        parent[max(a, b)] = min(a, b)
    owners = {}; texts = {}
    for r in sorted(records, key=lambda x: x.id):
        owner = (r.corpus, r.participant)
        text = normalized_text(r.text)
        if owner in owners: union(r.id, owners[owner])
        if text in texts: union(r.id, texts[text])
        owners[owner] = r.id; texts[text] = r.id
    for group in related_groups:
        for identifier in group[1:]:
            union(group[0], identifier)
    groups = {}
    for r in records: groups.setdefault(root(r.id), []).append(r.id)
    result = []
    for ids in sorted(groups.values(), key=lambda x: min(x)):
        ids = sorted(ids)
        prior = bool(set(ids) & set(development_ids))
        score = int.from_bytes(hashlib.sha256((salt+'\0'+'\0'.join(ids)).encode()).digest()[:8], 'big') / 2**64
        split = 'development' if prior or score >= holdout_fraction else 'holdout'
        result.append({'record_ids': ids, 'split': split, 'previously_seen': prior})
    return {'groups': result, 'salt': salt, 'holdout_fraction': holdout_fraction,
            'scope': 'participant, supplied relationship groups and normalized exact-text isolation; not semantic-near-duplicate certification',
            'requires_frozen_inventory': True}
