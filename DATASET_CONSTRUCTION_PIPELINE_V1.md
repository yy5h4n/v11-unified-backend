# Dataset Construction Pipeline v1

**Document role:** sole normative specification for constructing the benchmark.

**Status:** `frozen` specification release 1.0 (2026-08-30). Conformance means
matching the hash-bound document/package tuple in `conformance_v1/MANIFEST.json`
and passing every released assertion; the label does not retroactively validate
legacy V11 data.

## 1. Construction objective

Construct executable **Responsibility Episodes** that evaluate whether an Agent
can keep a human-evidenced household responsibility fulfilled throughout a
changing, action-sensitive physical process.

In this benchmark, *responsibility* is an operational label, not a claim of
moral or legal obligation. It means:

> a participant-supported, recurring or persistent household maintenance
> objective for which the evidence identifies a beneficiary, a responsible or
> delegating party, an unacceptable failure state, an authorized action scope,
> and an override or release condition.

Evidence that supports only a desire, observed practice, device use, preference,
or recurring event remains separately coded and cannot be promoted by analyst
interpretation. Acceptance of an objective does not imply acceptance of Agent
delegation; delegation and action authorization require their own evidence.

The construction direction is one-way:

```text
human evidence
  -> responsibility semantics
  -> Query and evaluator Contract
  -> backend-agnostic physical requirements
  -> backend process mining
  -> executable Episode
```

Neither backend availability, mined windows, baseline performance, nor an
author-preferred story may create or rename a responsibility.

## 2. Core data objects

| Object | Meaning | Must contain |
|---|---|---|
| `CorpusCard` | Audited source | provenance, collection unit, household/participant counts, access/license, bias |
| `EvidenceUnit` | Smallest traceable human evidence | source span/rule, source type, household/participant ID, authorship/status, support limits |
| `EvidenceBundle` | Cross-unit support for one candidate | supporting, compatible, contradicting and boundary evidence; independent support counts |
| `CanonicalResponsibility` | Frozen human-level obligation | desired state, beneficiary, accountable party, context, persistence, failure meaning, override/release, evidence hash |
| `Query` | User-facing expression of the frozen responsibility | natural language only semantically entailed by the responsibility; paraphrase set and equivalence verdict |
| `Contract` | Trajectory-level operationalization | hard invariants, conditional obligations, terminal goals, soft costs, priorities, clause provenance |
| `OpportunityPredicate` | Backend-independent definition of relevant physical processes | required states/actions/observations/dynamics and positive/boundary/certified-no-op conditions |
| `PhysicalProcess` | Canonical source-grounded backend window | backend/version, source hash, horizon, exogenous trace reference, capabilities |
| `Episode` | Executable evaluation unit | one responsibility/Query/Contract bound to one process and interaction protocol |

All objects are versioned and content-addressed. The immutable lineage is:

```text
CorpusCard + EvidenceUnit
  -> EvidenceBundle@hash
  -> CanonicalResponsibility@version/hash
  -> Query@version/hash
  -> Contract@version/hash
  -> OpportunityPredicate@version/hash
  -> PhysicalProcess@source_hash
  -> Episode@id
```

Canonical serialization uses RFC 8785 JSON Canonicalization Scheme followed by
SHA-256 over UTF-8 bytes. Hashes include schema version. Source snapshots are
immutable and accompanied by retrieval date, original checksum, access/license
status and a privacy-preserving subject key.

Four statuses are stored independently and must never be collapsed into one
field:

- `semantic_status`: whether human evidence supports the responsibility;
- `authorization_status`: what delegation/action scope is supported;
- `physical_status`: whether a backend binding is informative and executable;
- `release_status`: whether the object is provisional, frozen, invalidated or
  released.

Frozen objects are append-only. A semantic or evaluator change creates a new
version and invalidates all downstream hashes; it never edits an existing object
in place. Every transition, exclusion and adjudication carries a machine-readable
reason code.

## 2.1 Evidence inference matrix

| Source type | May support | Cannot establish alone |
|---|---|---|
| Direct interview/diary quotation | reported purpose, beneficiary, failure experience, preference, refusal | behavioral recurrence, prevalence, delegation acceptance unless stated |
| Participant-authored desired automation | desired behavior, stated trigger/context | deployment, persistence, unique purpose, accountability, successful use |
| Naturally authored Routine | configured mechanism and context; recurrence only if longitudinal metadata exists | unique purpose, beneficiary, accountability or authorization |
| Interaction log | observed action/event recurrence and override behavior | purpose or consent without linked evidence |
| Caregiver/researcher-authored rule | caregiver/researcher objective and installed mechanism | care recipient's objective, consent or delegation |
| Default/copied/synthetic rule | platform capability or test fixture | independent user demand |

Every coded proposition records which inference-matrix row permits it. Unsupported
inference is a conformance failure.

## 3. Stage 1 — Acquire and audit real user evidence

### Input

User-needs interviews, longitudinal diaries, co-design/probe studies,
participant-authored desired automations, naturally authored Routines, and
interaction logs.

### Procedure

Create a `CorpusCard` before using any source. Treat households/participants—not
rule rows—as independent support units. Mark rules as authored, copied,
suggested, default, active, abandoned, or unknown when the source permits it.

### Gate

- Verifiable source and stable locator.
- Legal/access status recorded; unknown licensing remains explicit.
- Source unit, population, platform and collection bias recorded.
- Synthetic/default/copied rules cannot count as independent user demand.
- Ethics/consent, de-identification, participant compensation and allowed
  secondary-use scope are recorded for newly collected human data.

### Output

Versioned corpus registry and immutable raw evidence pool.

## 4. Stage 2 — Build the evidence ledger

Each `EvidenceUnit` preserves verbatim text or is explicitly labeled a
paper-described example. It records:

```text
source + household/participant + context
semantic content + temporal scope + ownership evidence
beneficiary + desired state + failure meaning
constraints + trade-offs + override/release
means/device/action + evidence limitations
```

Device/action fields are stored separately from human semantics. A raw Routine
with no purpose information is normally only `compatible_only` evidence.

### Gate

- No analyst-generated purpose is inserted into the source layer.
- Missing fields remain missing rather than being filled from common sense.
- Household conflicts, refusals, manual-only preferences and non-use remain
  first-class evidence.

## 5. Stage 3 — Induce responsibility candidates

### Procedure

1. At least two annotators independently code the ledger while blinded to
   backend capabilities, mined windows, existing responsibility labels and the
   other annotation.
2. Represent each unit using desired state, beneficiary, context, temporal
   scope, ownership, failure and release. Mask brand/device/action vocabulary
   from semantic grouping.
3. Use embeddings or clustering only to retrieve candidate neighbors.
4. Human annotators merge, split, retain multi-label membership or reject
   candidates using constant comparison and negative-case analysis.

AI annotation may debug the schema or pre-label data. Formal claims require
independent human annotation and pre-adjudication agreement.

### Semantic independence and dataset partitions

An `independence_unit_id` is the connected component obtained by joining
evidence that shares a household, participant, longitudinal subject, copied-rule
ancestor or participant-authored template ancestor. Study and recruitment-pool
membership are recorded as clustering variables but do not merge otherwise
distinct households into one support unit. Cross-study identity links do merge
the affected units. The reference graph builder, edge enums and golden fixture
are part of the conformance package.

To prevent development leakage, human-evidence data are partitioned by the
coarser connected components formed from independence units, study, recruitment
pool, author/template genealogy and longitudinal subject:

- `semantic_discovery`: open coding and candidate induction;
- `semantic_development`: codebook training, threshold selection and revision;
- `semantic_confirmatory`: untouched until the codebook, admission rule and
  candidate statements are frozen.

Confirmatory evidence cannot influence candidate existence, wording, codebook or
thresholds. It tests whether already-frozen constructs transfer. These partitions
are never reused as the simulator train/dev/test split. Any construct change
after a confirmatory failure creates a new version and requires a previously
untouched confirmatory partition.

### Responsibility admission gate

A candidate becomes a `CanonicalResponsibility` only when:

- discovery/development contain at least three independent household components
  across at least two studies and two evidence modalities;
- at least two independent households provide explicit purpose-bearing
  `entails` or `strongly_implies` evidence;
- at least one independent behavioral or longitudinal source corroborates
  recurrence, persistence, override, or actual practice;
- recurring/persistent scope, accountable ownership, beneficiary, desired state,
  failure meaning and override/release are positively supported or explicitly
  marked unknown; executable control additionally requires positive Agent
  delegation and an evidence-bounded authorized-action set;
- contradictions and population/context boundaries are resolved or encoded;
- device replacement preserves the responsibility identity;
- two trained human annotators achieve Krippendorff's alpha >= 0.80 on each
  admission-critical nominal/ordinal field in development before adjudication;
- the frozen construct achieves point-estimate precision >= 0.80 and recall >=
  0.70 on an untouched confirmatory sampling frame containing at least 30
  independence units and at least 10 adjudicated positive units for that
  construct.

The confirmatory frame is sampled from all eligible units in the held-out source
components before candidate retrieval, so negatives are not prefiltered away.
Two blinded human annotators apply the frozen codebook; their pre-adjudication
labels and an adjudicated multi-label gold set are released. Each annotator's
pre-adjudication label is a separate prediction, and both annotators must
independently pass the admission thresholds against adjudicated gold.
Adjudication never rewrites a prediction label. Metrics are one-vs-rest at
`independence_unit_id`: predicted positive means that annotator assigned the
frozen construct before adjudication;
`semantic_unknown/out_of_scope` is abstention, and is a false negative when gold
is positive. Precision and recall use the usual TP/(TP+FP) and TP/(TP+FN)
denominators. Study-cluster bootstrap 95% intervals with 10,000 frozen resamples
are descriptive; the stated point estimates are the admission gate. Missing
gold fields make a unit ineligible for that field rather than negative. The
release reports the eligible denominator, missingness and multi-label overlap.

Threshold sensitivity at household support 2/3/4 and agreement 0.67/0.80 is
reported. These thresholds define this release protocol, not a universal law.
Annotator recruitment, expertise, training examples, compensation, missingness,
raw disagreement, adjudicator identity and adjudication reasons are released.

Low-frequency high-risk cases may receive `critical_case_validated` only if they
pass every semantic-validity and authorization gate except the ordinary
frequency/support-count threshold. They receive a separate track and score and
cannot support prevalence, population-general or core-coverage claims. They are
not `human_validated` core responsibilities.

`semantic_unknown/out_of_scope` means the evidence does not justify assigning a
frozen responsibility. It is distinct from the later physical label
`certified_no_opportunity`, where a valid responsibility applies but a released
certificate shows intervention cannot improve the frozen construction utility
beyond tolerance in that physical process group.

### Semantic and authorization status

- `provisional_ai_pilot`: useful for engineering previews only.
- `human_validated`: passed the formal admission protocol.
- `critical_case_validated`: semantically and operationally validated but exempt
  only from the ordinary frequency gate; separate claims apply.
- `rejected_or_unresolved`: retained with reasons; cannot generate benchmark
  claims.

Delegation/action authorization is independently coded as
`authorized_agent_control`, `authorized_automation_only`, `manual_only`,
`refused`, or `unknown`, with an explicit allowed-action set and evidence span.
Only `authorized_agent_control` with a nonempty bounded action set may enter the
executable Agent-control track. All other values remain available for semantic,
non-control or preview analyses and cannot be silently promoted by a benchmark
Query.

## 6. Stage 4 — Freeze Responsibility, Query and Contract

Freezing is an explicit release event. The evidence ledger and bundle are
hashed; responsibility semantics cannot change during backend scanning.

### Query construction

Generate natural paraphrases only after semantics are frozen. Two independent
annotators verify bidirectional equivalence: the Query adds no obligation and
omits no essential responsibility clause. Accountability, authorization,
delegation, failure and release claims additionally require participant/member
checking when direct participants can be recontacted; otherwise the construct is
narrowed to what the archived evidence supports.

### Contract construction

Two annotators independently translate the frozen semantics into the released
temporal Contract DSL, then independently review the merged draft for
completeness and non-expansion before adjudication. The DSL defines interval
closure, trigger and deadline timing, persistence, release, priority, missing
observations and conflict resolution. Each Contract releases a machine-readable
clause-coverage matrix:

```text
responsibility_field
  -> contract_clause/formula
  -> frozen transformation_rule
  -> evidence_span or benchmark_design_choice
  -> reviewer_A verdict + reviewer_B verdict
  -> adjudication verdict/reason
```

The resulting clauses may contain:

- hard invariants;
- conditional obligations over time;
- terminal goals;
- cumulative soft costs;
- lexicographic or explicitly justified priorities.

Every non-universal threshold, weight, horizon or priority must reference either
supporting evidence or `benchmark_design_choice`. A design choice may resolve
measurement cadence, numerical tolerance or reporting resolution, but may not
create a new beneficiary, authorized action, trigger, hard obligation, failure
condition or priority absent from the responsibility. Design choices receive
sensitivity analysis and must not be described as user preferences. Positive
and negative clause-coverage golden fixtures define the exact entailment and
completeness verdicts.

Responsibility, Query, Contract, evaluator implementation, thresholds,
selection rule and every rejected predicate are timestamped and frozen before an
eligible backend scan. Because authors may know existing V11 assets, author
access and hindsight risk are recorded rather than claiming impossible blinding.

### Gate

- Query-to-responsibility entailment passed.
- Contract-to-responsibility entailment passed clause by clause.
- No gold action sequence is part of the Contract.
- Semantic hash is frozen before any backend process is inspected.

## 7. Stage 5 — Compile backend-agnostic physical requirements

Create a responsibility-specific `OpportunityPredicate` specifying:

- required observable and latent state variables;
- legal action types and authorization boundaries;
- required dynamics and exogenous processes;
- horizon and observation cadence;
- evaluator-relevant action-sensitivity criterion;
- information available to the Agent, including forecasts if needed;
- `positive_opportunity`, `boundary_opportunity`,
  `certified_no_opportunity` and `unsupported` definitions.

This predicate is preregistered before scanning. It describes what would make a
physical process informative without naming a particular backend record.

### Mathematical opportunity strata

The evaluator reports the frozen lexicographic Contract verdict and clause-level
metrics. Selection uses a separate, unique bounded construction utility
`U_C(tau) in [0,1]`; its clause mapping is included in the Contract coverage
matrix and may change measurement resolution but not responsibility semantics.
This separation avoids subtracting lexicographic vectors.

Before inspecting a source process, the release freezes `Pi_auth_pub`, the
universe of all legal non-anticipating policies over the public-observation and
authorized-action interface, and a finite enumerable witness-search subset
`Pi_cert` containing the no-intervention policy `pi_0`. Policies and their
selection procedure cannot access Episode IDs, hashes, source identifiers,
lookup tables or private future traces.

Opportunity is classified over a preregistered public-history equivalence group
`g`, not by fitting a different policy to each private process. Group membership
is built before policy search using the frozen public-prefix fields, tolerance
metric and linkage algorithm. One policy-selection algorithm and one resulting
policy must serve the entire group. Let:

```text
pi_hat(g) = argmax_{pi in Pi_cert}
              mean_{s in search_seeds} U_C(tau[g, pi, s])

Delta_witness(g) = E[U_C(tau) | g, pi_hat(g)]
                   - E[U_C(tau) | g, pi_0]
```

`Pi_cert` is fully enumerated on frozen `search_seeds`. After policy selection,
the chosen policy and `pi_0` are compared by paired evaluation on disjoint,
untouched `certification_seeds`. Scores are bounded; the release freezes seed
counts, power analysis, confidence method and family-wise correction across all
scanned groups. If seeds are declared the complete finite target population,
exact means replace sampling intervals. Optimization on certification seeds is
a conformance failure.

The certification-seed confidence bound estimates `Delta_witness(g)`. It is a
lower-bound witness that an improvement is achievable, not an estimate of the
population maximum unless the complete finite target population is exhaustively
evaluated. The release configuration requires
`0 <= delta_noop < delta_positive <= 1`, and
defines acceptable **policy** sets, deterministic ties and the following labels:

- `positive_opportunity`: the multiplicity-adjusted lower confidence bound of
  paired `Delta_witness(g)` is at least `delta_positive`, and `pi_hat(g)` belongs
  to the nonempty intersection of all group members' frozen authorized
  acceptable-policy sets;
- `certified_no_opportunity`: a released analytic bound, exhaustive finite-state
  verifier or solver with a proven optimality/regret bound establishes that the
  upper bound on improvement over `Pi_auth_pub` is at most `delta_noop`, and
  `pi_0` is acceptable. A bound covering only `Pi_cert` is insufficient and
  yields `boundary_opportunity`;
- `boundary_opportunity`: all other applicable groups, including no detected
  gain, optimization gaps, uncertainty, ties or incompatible acceptable actions;
  no precise gold action is asserted;
- `unsupported`: required capability, authorized action or public information is
  absent.

A finite-budget optimizer without a valid upper-bound certificate can establish
a positive witness but can never establish `certified_no_opportunity` merely by
failing to find improvement. `Pi_cert` is a declared construction oracle, not an
experimental baseline. The correct release claim is zero
experimental-baseline-driven membership, not zero oracle-driven membership.

### Gate

- Physical solvability and information feasibility are separate checks.
- The predicate cannot consult experimental Agent/baseline performance; only the
  frozen construction oracle and analytic physical tests may be used.
- Waiting/no-op is correct in the certified-no-op stratum and may be acceptable
  in boundary cases; the acceptable-policy type is fixed.
- Numeric thresholds, seeds, ordering and tie rules exist in the frozen
  machine-readable release configuration.

## 8. Stage 6 — Scan unified physical backends

The shared compiler matches the frozen predicate to verified adapter
capabilities, then scans source-grounded trajectories once per backend/source
bundle. CityLearn, EV2Gym and future simulators remain separate adapters behind
one schema and process pool.

Backend processes become candidate bindings but receive no final stratum,
primary assignment or quota decision until Stage 7 QA. They may not select the
responsibility, Query, Contract, utility, weights or semantic family.

### Gate

- Source provenance, version and hashes are complete.
- No process is duplicated across semantic labels as separate physical data.
- Selection uses only preregistered physical facts.

## 9. Stage 7 — Replay and construct-validity QA

For every candidate binding:

1. replay the preregistered trace fixture set plus the frozen construction oracle
   across all configured stochastic seeds;
2. verify deterministic termination and state bounds;
3. verify action sensitivity changes an evaluator-relevant outcome materially;
4. run the frozen construction oracle using only the exact public observation
   interface and without Episode/source identifiers;
5. verify required forecasts are public; if absent, label the binding
   `unsupported` rather than changing the frozen Contract;
6. test boundary/certified-no-op correctness and unnecessary-action penalties.

Private witnesses establish feasibility only; they are never released as gold
actions and never determine semantic membership.

### Information-set test

The preregistered equivalence groups from Stage 5 are materialized before oracle
search. Matched public histories with different latent futures are retained to
measure ambiguity; if the intersection of their frozen authorized
acceptable-policy sets is empty, every affected binding is
`boundary_opportunity`. Oracle-detected gain, certified
upper-bound status, public identifiability, raw controllability and authorization
are separate verdict fields.

Only after all bindings pass replay and information-set QA are physical labels
fixed. If one canonical process has multiple valid bindings, the reference
assignment algorithm orders them by the frozen, stratum-specific normalized
certificate margin, then lexical responsibility ID; exact formulas and a golden
fixture are in the release configuration. It runs once over canonical processes
in lexical process-ID order. Quota failure never changes assignment: the affected
responsibility becomes `preview_only`. Alternate bindings remain private
metadata, and one process is never multiplied into independent primary samples.
Any Contract amendment creates a new version and restarts Stages 5–7 for every
eligible source snapshot.

## 10. Stage 8 — Compile executable Responsibility Episodes

Each Episode binds exactly one primary `CanonicalResponsibility`, Query,
Contract and PhysicalProcess.

### Finite-window semantics

A standing responsibility begins before or at Episode activation and is assumed
to continue after the finite window unless its evidence-backed release fires.
For each discrete interval `[t,t+1)`, the protocol is fixed as: publish
observation and pending-obligation state at `t`; receive action; apply transition;
evaluate clauses on `(history_t, observation_t, action_t, observation_{t+1})`;
then apply releases and deadlines whose effective time is `t+1`. Simultaneous
events use the Contract's frozen priority rule.

Initial state carries a sufficient statistic for every evaluator-relevant
pre-window trigger, cumulative cost, deadline, cooldown and release condition.
The reference validator reconstructs this state from a hidden prefix and checks
equivalence. At the terminal boundary, `released`, `continues_beyond_window`,
`censored_pending`, and `violated_at_truncation` are distinct. A pending
obligation is never counted as success merely because its deadline lies outside
the window.

The release configuration chooses one declared anti-gaming rule per Contract:
(a) score only complete trigger-deadline pairs using a frozen terminal guard
band, or (b) apply a terminal-viability evaluator that verifies recoverability
under an authorized safe continuation policy without private future access.
Irrecoverable or already-doomed terminal states are failures; recoverable open
obligations are censored and reported outside the success denominator. Release
effective times and late triggers follow the same temporal DSL. Horizon choice
must cover the preregistered event cycle, terminal rule and sensitivity analysis;
window end is never equated with responsibility completion.

### Public record

- responsibility Query;
- initial observation and observation schema;
- legal action schema;
- cadence, horizon and termination;
- semantic and physical status;
- public content hashes and split.

### Private record

- evidence/semantic/contract/predicate lineage;
- backend binding and source hashes;
- evaluator clauses and QA verdicts;
- selection stratum and replay digests;
- no public gold-action leakage.

## 11. Stage 9 — Deduplicate, diversify and split

Deduplicate at four levels:

1. textual/semantic evidence;
2. household and source genealogy;
3. physical trajectory/event topology;
4. modeled configuration, weather and generator lineage.

For each level, the release configuration defines the canonical key, distance
function, threshold, interval-overlap rule and transitive-closure algorithm.
Thresholds are frozen before candidate selection. Near-duplicate removal uses a
stable lexical object-hash order; infeasible diversity or quota constraints
produce `preview_only`, never manual sample substitution.

Select diversity across household, season, event topology, opportunity strength,
initial state, action sensitivity and responsibility-relevant failure mode.
Two distinct graphs are built:

- `semantic_provenance_graph`: households, participants, studies, recruitment
  pools, rule/template ancestry and paraphrase lineage;
- `physical_provenance_graph`: buildings/vehicles, overlapping source intervals,
  exogenous traces, weather, simulator configurations, generator seeds and model
  ancestry.

The graph domains are not interchangeable. The semantic graph alone assigns
discovery/development/confirmatory evidence partitions. The physical graph alone
is the base constraint for process train/dev/test splits. A track then adds its
declared grouping constraints:

- seen responsibility / unseen process: responsibility may span physical splits,
  but no physical connected component may do so;
- unseen paraphrase: paraphrase lineage components are held out while the same
  frozen responsibility may appear elsewhere;
- held-out responsibility family: the entire responsibility/family component is
  added to the Episode conflict graph and assigned to one split.

The reference splitter constructs the track-specific conflict graph, computes
transitive closure, and assigns components with a frozen seed, stable component
order and deterministic quota solver. It releases rules for infeasible quotas
and an end-to-end golden fixture. Semantic confirmatory data are never reused as
simulator test data. Every responsibility must meet machine-readable per-split
and per-stratum minimum counts or be `preview_only`.

## 12. Stage 10 — Release QA and reporting

The release fails closed unless it verifies:

- all lineage hashes and versions;
- semantic, authorization, physical and release status separation;
- zero backend-derived responsibility labels;
- zero experimental-Agent/baseline-driven membership decisions; construction
  oracle decisions are declared and reproducible;
- public/private schema and no gold leakage;
- positive, boundary and certified-no-op coverage;
- deterministic replay and evaluator-relevant action sensitivity;
- split leakage audit and responsibility coverage;
- exact counts derived from artifacts;
- reproducible build command and environment manifest.

### Frozen conformance package

Before implementation claims conformance, the specification release includes:

- JSON Schemas and enums for every core object;
- RFC 8785 + SHA-256 canonical hashing fixtures;
- annotation codebook and inference-matrix fixtures;
- machine-readable release configuration with thresholds, quotas, seeds,
  ordering and tie rules;
- source snapshot manifest, dependency/container lock and deterministic locale;
- positive, boundary, certified-no-op, unsupported, semantic-unknown and failure-closed
  golden fixtures;
- explicit failure codes and exact pass/fail assertions.

JSON Schema is necessary but insufficient. The package also includes a reference
cross-object validator, reference temporal evaluator, construction-oracle runner
and policy sandbox, immutable-object state machine, graph/split implementation,
and a small end-to-end corpus-to-Episode fixture. That fixture fixes expected
object hashes, semantic and authorization verdicts, physical labels, primary
assignment, split, replay digest, terminal evaluation and every exercised
failure code. Search/certification seed isolation and post-freeze mutation are
explicit negative fixtures.

The release manifest separates `public_reproducible`, `restricted_audit`, and
`operator_private` artifacts. It states which claims an external party can
reproduce. A private evaluator can support only operator-side result
reproducibility unless its logic and hashes are available under restricted audit;
privacy-preserving source hashes are not exposed when they enable dictionary
reconstruction of protected text.

The dashboard is generated from release artifacts and never serves as a source
of truth.

## 13. Prohibited circularities

The following invalidate a release:

- inventing a responsibility because a backend supports a device;
- assigning responsibility families from mined window statistics;
- selecting Episodes because a chosen Agent/Workflow performs poorly or well;
- treating Routine frequency as unique purpose or population prevalence;
- presenting benchmark thresholds as user preferences without evidence;
- using a private omniscient witness to claim public information feasibility;
- multiplying one physical process into nominally different primary samples;
- reporting modeled trajectories as measured household behavior.

## 14. Baselines are downstream, not construction inputs

Workflow, fixed schedules, rules, MPC/optimization and online Agents are run only
after the release is frozen. Their results characterize which approaches manage
which responsibilities; they do not control data membership or task design.

The pipeline evaluates evidence-grounded responsibility-conditioned control. V1
does **not** claim to isolate implicit-intent inference. An optional future track
may compare an underspecified natural-language Query against an explicit Contract
oracle, but only after separate human validation of what information is
reasonably inferable. The main paper claim must not use “implicit intent” as a
measured capability until that track exists.

## 15. Non-normative current project mapping

- Stages 1–3: a 24-unit, five-source AI pilot exists and has refined the coding
  schema; it is not human validation.
- Stage 4: no responsibility is yet `human_validated`; Luna/Kimi outputs are
  independent provisional candidates only.
- Stages 5–10: V11 contains reusable compiler, adapters, process pools,
  evaluators, HVAC/EV legacy processes and battery/PV replay infrastructure.
  Existing releases predate this specification and cannot claim conformance.
- No old V11 artifact is silently relabeled as evidence-grounded.

This section is informational and is excluded from the normative-specification
hash. The normative release manifest carries the authoritative implementation
status.

## 16. Implementation rule

A frozen specification release is the hash-bound tuple of this normative
document, the conformance-package manifest, Contract DSL, schemas, release
configuration, reference implementations and golden/negative fixtures. Release
status may become `frozen` only after that tuple exists and all conformance
assertions pass.

After this document and its conformance package are frozen, implementation
proceeds stage by stage against explicit conformance tests. Any change to stage
order, admission gates,
selection inputs or status semantics requires a versioned specification update,
not an ad hoc code edit.
