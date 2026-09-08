# Natural Surface Query Protocol v2.4

## Layering

V2.3 remains the stable semantic standing-intent layer. V2.4 adds natural surface forms only. Surface variation never creates a new responsibility or Episode, and it may not change the beneficiary, outcome, lifecycle, constraints, profile dependencies, or evaluator contract.

Each `standing_intent_id` receives four semantically equivalent English utterances:

1. `primary`: the most natural query for ordinary benchmark use;
2. `contextual`: a natural request that uses only evidence-supported context;
3. `preference`: a user preference or desired-state formulation;
4. `concise`: a short but complete delegation.

The four labels describe pragmatic form, not mandatory templates. Do not force “Please help me,” “I want you,” or any other shared stem. Imperatives, questions, contractions, first-person context, and direct requests are all allowed when natural.

Surface generation must rewrite the whole utterance, not prepend a politeness wrapper to the V2.3 canonical sentence. The following meta-templates are forbidden: “Given this context,” “For the situation described,” “My request is that,” “My household preference is that,” “What I would like you to do is,” “Would you be willing to,” “I ask you to,” and “Please make sure you.” Avoid doubled control verbs such as “help me ensure,” “make sure you ensure,” or “help me support.”

Write as an ordinary resident speaking to a capable home assistant. Prefer concrete desired states such as “Keep the house secure while we’re out” over ontology-like phrases such as “support maintenance of household security.” The contextual form must use an actual evidence-supported boundary or setting, never generic filler. Across the four variants, no more than two may copy the same five-token sequence from the V2.3 canonical query.

## Semantic and leakage rules

- Preserve exactly one responsibility and its evidence-grounded scope.
- Do not add a beneficiary, hazard, deadline, notification duty, escalation policy, or termination condition.
- Household-specific values may be referred to naturally (for example, “my preferred temperature” or “the prescribed time”) but must not be invented.
- Do not prescribe sensors, devices, tool calls, action sequences, waiting policies, or optimal timing.
- Do not expose evaluator criteria, oracle actions, reward terms, or trajectory expectations.
- A natural temporal clause such as “while we are away” is permitted; Trigger–Action pseudocode is not.
- `needs_review` responsibilities remain `needs_review`; fluent paraphrasing must not conceal their source uncertainty.

## Output

One JSONL row per input, in input order:

- `standing_intent_id`
- `responsibility_id`
- `primary_query_id`
- `surface_queries`: exactly four objects with `query_id`, `form`, and `text`
- `semantic_equivalence_rationale`
- `quality_flags`
- `generation_status`

Query IDs are deterministic: `<standing_intent_id>__primary`, `__contextual`, `__preference`, and `__concise`.

## Acceptance gates

- 142/142 standing intents covered exactly once and 568 unique query IDs/text records.
- Within each intent, all four texts are distinct and responsibility-equivalent.
- No normalized opening trigram occurs in more than 10% of primary queries.
- No fixed opening bigram occurs in more than 20% of all surface queries.
- No forbidden meta-template or doubled control verb occurs, and no responsibility has more than two variants copying a canonical five-token sequence.
- No action-recipe or hidden-evaluator leakage.
- Final readiness cannot exceed the inherited V2.3 readiness without explicit source adjudication.

For opening n-grams, normalize with Unicode case-folding, replace punctuation with spaces, collapse whitespace, tokenize on spaces, and take the first two or three tokens. Contractions remain one token after punctuation removal.

Anti-drift validation is separate from style validation. For each set of four variants, compare against the V2.3 beneficiary, delegated outcome, lifecycle, duration boundary, evidence-grounded constraints, and profile dependencies. Flag any newly introduced person/entity, exact time or number, obligation, notification/escalation duty, termination condition, device/action prescription, or broader/narrower source scope. Any such hit forces `generation_status=needs_review` until adjudicated; opening diversity can never override semantic consistency.
