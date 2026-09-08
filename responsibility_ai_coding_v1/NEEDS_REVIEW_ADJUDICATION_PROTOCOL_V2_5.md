# Needs-Review Adjudication Protocol v2.5

Review the responsibility against the verbatim evidence, not against backend capability and not against a desired catalog size. Resolve every row into exactly one decision:

- `RELEASE_AS_IS`: evidence supports one standing responsibility and the current natural query.
- `REVISE_AND_RELEASE`: evidence supports one standing responsibility, but its name/query/outcome must be corrected without adding semantics.
- `SPLIT_AND_RELEASE`: evidence contains multiple independently delegable outcomes that must become separate standing intents; supply complete children.
- `QUARANTINE`: evidence is malformed, too ambiguous, merely descriptive, or insufficient to support a defensible standing responsibility.

Device, room, exact threshold, schedule, and notification channel are configuration variants unless they change the human outcome, beneficiary, lifecycle, or failure meaning. Do not treat fluency as evidence. Do not force release. Backend support is irrelevant.

One output JSONL row per input, same order, with:

- `standing_intent_id`, `responsibility_id`
- `decision`
- `resolved_canonical_name` (null unless released)
- `resolved_natural_query` (null unless released)
- `resolved_delegated_outcome` (null unless released)
- `split_children` (empty unless split; each child contains name, query, outcome, supporting evidence IDs)
- `evidence_supports_standing_responsibility` (boolean)
- `resolved_flags` (must be empty for released items)
- `rationale`
- `confidence` (`high`, `medium`, `low`)

All released queries must remain natural, outcome-level, device-independent, and free of action recipes or evaluator answers. A quarantined row is a resolved outcome, not an unfinished review.
