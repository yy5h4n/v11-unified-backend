# Standing-Intent Query Compilation Protocol v2.3

## Purpose

Compile each evidence-grounded responsibility into one canonical natural-language user query. The query states the responsibility the user delegates; it must not encode the trigger-action program or the evaluator's preferred policy.

The responsibility catalog is defined before backend inspection. Backend capability is considered only later, when responsibility-compatible physical episodes are mined.

## Separation of layers

1. **Source evidence** records what users asked for or configured. It may be operational and device-specific.
2. **Responsibility** abstracts the durable human outcome, beneficiary, lifecycle, failure meaning, and material context.
3. **Canonical query** is a natural delegation of that responsibility.
4. **Resident profile / episode context** supplies household-specific values such as comfort bands, medication schedules, budgets, quiet hours, return times, device inventory, and notification preferences.
5. **Hidden evaluator contract** scores whether the responsibility was fulfilled over the trajectory. It is never copied into the query.

## Query requirements

Each canonical query must:

- use natural first-person delegation language and contain one coherent responsibility;
- name the desired human outcome and beneficiary when material;
- express the lifecycle or duration boundary when supported (for example, while I am away, overnight, until the person returns, or at the prescribed time);
- preserve evidence-supported hard constraints, but refer to household-specific values through the resident profile when possible;
- leave observation, waiting, action selection, escalation, recovery, and trade-offs to the agent;
- remain reusable across multiple compatible physical episodes.

Each canonical query must not:

- use IF/WHEN/THEN syntax or enumerate a workflow;
- prescribe sensors, actuator calls, exact tool names, or a sequence of actions;
- reveal the optimal action time, expected trajectory, scoring thresholds, or oracle answer;
- invent preferences, hazards, beneficiaries, notification requirements, or termination conditions absent from the evidence;
- combine distinct responsibilities merely because one automation rule mentioned several actions.

Exact times or thresholds remain in the query only when they are the user-level outcome itself. Otherwise they are configuration fields in the resident profile. For example, “make sure I wake at 7:00” may retain 7:00; “turn on the heater below 14°C” becomes maintaining the resident's configured comfort range.

## Output schema

One JSON object per responsibility:

- `standing_intent_id`: stable ID derived from the responsibility ID; it does not change across episodes
- `responsibility_id`
- `canonical_query`
- `beneficiary`
- `lifecycle`
- `delegated_outcome`
- `duration_boundary`
- `profile_dependencies`: values the episode/profile must expose
- `evidence_grounded_constraints`
- `configuration_variants_removed`: operational details deliberately abstracted out
- `source_evidence_ids`
- `quality_flags`: zero or more of `operational_leakage`, `oracle_leakage`, `unsupported_invention`, `ambiguous_outcome`, `missing_boundary`, `compound_responsibility`, `canonical_name_mismatch`, `needs_source_review`
- `generation_status`: `ready` or `needs_review`
- `rationale`

Episode records bind to a query through `standing_intent_id` and carry their own `episode_id`. A single `standing_intent_id` may therefore map to many compatible `episode_id` values without regenerating or specializing the canonical query.

## Acceptance gates

- Exactly one row for every final v2.2 responsibility ID; no additions or omissions.
- Every claim in a query must be traceable to its source evidence or clearly delegated to an episode profile.
- No query contains an action recipe or hidden evaluator answer.
- Machine checks reject IF/WHEN/THEN recipes, tool-call syntax, explicit sensor/actuator commands, episode IDs, and verbatim values drawn only from hidden evaluator fields.
- A profile/evaluator separation test constructs two episodes with different device inventories and profile values; their `standing_intent_id` and `canonical_query` must remain identical.
- A responsibility marked `needs_review` remains in the catalog and is never silently dropped.
- AI-generated rows are labeled provisional and are not described as human validated.
