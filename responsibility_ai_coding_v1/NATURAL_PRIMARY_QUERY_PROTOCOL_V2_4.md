# Natural Primary Query Protocol v2.4

V2.4 replaces the templated V2.3 surface string with one natural resident utterance per standing intent. It does not create paraphrase variants, new responsibilities, or new Episodes. Paraphrase robustness is a separate future split.

## Writing standard

- Write one grammatical, conversational English sentence that an ordinary resident could plausibly say to a capable home assistant.
- Use the most natural form for the responsibility: a direct imperative, contextual request, desired-state statement, or question. Do not distribute forms by quota.
- Rewrite the semantic content rather than attaching a politeness prefix to the V2.3 string.
- Prefer everyday desired states: “Keep the house comfortable while we’re home” instead of “support maintenance of indoor thermal comfort.”
- Natural lifecycle clauses such as “while we’re away” or “if the baby cries at night” are allowed; do not specify devices, sensors, tool calls, action order, optimal timing, or evaluator thresholds.
- Preserve the V2.3 beneficiary, outcome, lifecycle, boundary, constraints, and profile dependencies. Do not add notifications, hazards, deadlines, people, escalation, or completion conditions.
- Inherit every V2.3 quality flag and generation status. Fluency cannot promote a `needs_review` responsibility to `ready`.

Forbidden wording includes “Please help me,” “I want you to,” “Given this context,” “For the situation described,” “My request is that,” “My household preference is that,” “Would you be willing to,” “What I would like you to do is,” “I ask you to,” and doubled control verbs such as “help me ensure,” “make sure you ensure,” or “help me support.”

Examples of the intended transformation:

- `I want you to maintain a comfortable indoor temperature.` → `Keep the house at a comfortable temperature.`
- `I want you to maintain the security of my home while it is unoccupied.` → `Look after the house whenever no one is home.`
- `Please help me ensure infant is soothed back to sleep during night crying.` → `If the baby cries at night, settle them back to sleep.`
- `Please help me support the children's bedtime at the scheduled time.` → `Make sure the kids stick to their bedtime.`
- `Please help me support safe mobility for an elderly resident on the stairs.` → `Help the elderly resident use the stairs safely.`

## Output

One JSONL row per input, in order, with exactly:

- `standing_intent_id`
- `responsibility_id`
- `natural_query`
- `semantic_equivalence_rationale`
- `quality_flags`
- `generation_status`

## Gates

- 142/142 exact coverage and unique texts.
- No forbidden wrapper, doubled control verb, action recipe, or evaluator leakage.
- No normalized opening trigram may exceed 10% of all queries.
- Each query must be 4–24 words and end with sentence punctuation.
- Every flagged V2.3 row remains `needs_review`; semantic drift also forces `needs_review`.
