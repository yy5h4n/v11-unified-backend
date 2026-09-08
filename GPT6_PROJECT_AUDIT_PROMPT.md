# GPT-6 Independent Project Audit Prompt

You are performing an independent, adversarial technical audit of a research
prototype. Do not assume that generated catalogs, test names, comments, status
labels, or previous agents' summaries are true. Treat them only as claims that
must be traced to executable code and backend-produced evidence.

## Repository and scope

Audit this directory:

```text
/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler
```

The intended research claim is that a household Agent benchmark can expose
multiple kinds of dynamic environments through one closed-loop interface,
while retaining real backend dynamics and machine-verifiable evaluation.

The present task is **review only**. Do not modify code or regenerate artifacts
unless explicitly asked after the audit. You may run read-only probes and tests.
Avoid network installation, deletion, or changes to sibling prototypes.

## Why the project has this architecture

This section records design motivation, not verified implementation. Audit the
code against it rather than treating it as evidence.

Earlier prototypes tended to build one environment, Episode generator, and
evaluator per household responsibility (for example, separate HVAC and EV
paths). That made backend availability shape the task ontology, duplicated one
physical process across multiple responsibilities, and made evidence/status
labels incomparable. V11 therefore separates five things:

```text
human-supported responsibility semantics
  != backend-independent task/trajectory contract
  != canonical physical process
  != backend adapter/runtime
  != Agent interaction/evaluation run
```

The intended architecture is unified only at the schema, compiler, registry,
Agent protocol, and evaluator boundaries. It deliberately retains multiple
physics engines behind adapters; building one universal hand-written simulator
would weaken the research claim rather than simplify it. A canonical process
pool stores a physical process once, and binding records connect it to a frozen
responsibility contract. Backend discovery is supposed to fail closed and must
not create, rename, or semantically expand a responsibility.

D0--D3 are meant to stratify *environmental dynamics required from the Agent*,
not backend prestige or task difficulty. D0 covers exogenous discrete change;
D1 adds health-dependent action effects; D2 adds continuous physical evolution;
D3 adds cross-system feasible-set coupling. The purpose is to prevent a broad
"dynamic home" claim from being supported only by many variants of HVAC.

The common `reset/observe/legal_actions/step` boundary exists so the evaluated
Agent sees one interaction grammar while each adapter preserves native backend
state and causal transitions. It must be a thin normalization layer, not a
place to synthesize missing physics. Incremental observations were introduced
because appending full environment snapshots at every turn caused quadratic
prompt growth. The intended conversation retains the initial full observation,
every Agent action, every exact action result, and sequential observation
deltas; private model thinking is neither replayed nor persisted.

The LLM runner's text envelope (`<answer>JSON_OBJECT</answer>`) is separate from
the physical-backend abstraction. It was retained as a gateway-compatible,
strictly parseable action channel; native tool calling must not be claimed when
the request does not actually send provider-native tool definitions. Episode
calls are serial within one Episode but may run concurrently across Episodes
(the recent runner target is 30 workers). Token reporting is intended to treat
provider-reported API **output tokens** as the primary generation-volume metric;
prompt/input tokens and provider aggregates are diagnostics and must not be
mislabelled as model output.

The primary success metric is intentionally about environment outcome,
temporal requirements, and safety constraints. User-notification behavior was
removed from the current headline success rate and from the corresponding
non-notification dataset slice; it may be analyzed later as a separate metric.
Do not silently reintroduce notification style/completeness into primary task
success. Evaluators should be gold-action-free: they judge backend-produced
state trajectories, not whether the Agent copied one reference policy.

Current work stopped after backend/Agent-loop construction. It has **not** yet
claimed that the new D0--D3 backends have been converted into a released Episode
dataset. Responsibility selection, initial-state admission, task feasibility,
query realization, evaluator attack tests, and dataset balancing remain a
separate construction stage.

## Claimed dynamic-mechanism taxonomy

The current taxonomy is D0--D3 (the letter is `D`, not `L`):

- **D0: exogenous context/event dynamics.** State changes through events or
  context, without requiring continuous physical evolution.
- **D1: device health/fault dynamics.** The same command can have different
  effects under healthy, degraded, stuck, intermittent-dropout, sensor-bias,
  or sensor-drift conditions.
- **D2: continuous physical dynamics.** Actions change a continuously evolving
  physical process such as thermal/air-quality, water-network, smoke/fire, or
  heat/hot-water dynamics.
- **D3: strong multi-system coupling.** At least two controllable subsystems
  share a finite resource or constraint. Intervening on subsystem A must change
  subsystem B's state, effective action, feasible action space, resource
  allocation, or attainable outcome.

The generated catalog currently claims:

- D0: one exogenous-context runtime.
- D1: four routes: HVAC, CityLearn battery, EV2Gym charger, and discrete-device
  workflow.
- D2: four routes: EnergyPlus IAQ, WNTR residential water, FDS smoke/fire, and
  Modelica Buildings/AixLib.
- D3: six routes: CityLearn single-building multi-system, CityLearn
  multi-building competition, WNTR water competition, Modelica shared heat,
  EV2Gym electrical competition, and EnergyPlus shared ventilation.

Relevant starting points include:

```text
README.md
DESIGN.md
scenario_catalog.json
unified_compiler/agent_interface.py
unified_compiler/evaluator.py
generated/dynamic_mechanism_catalog_v1.json
generated/claim_backend_catalog_v1.json
tests/
```

For the normative and historical rationale, also read rather than relying only
on this condensed prompt:

```text
DATASET_CONSTRUCTION_PIPELINE_V1.md      normative construction specification
DATASET_CONSTRUCTION_PIPELINE_V1_REVIEW.md
V10_COMPACT_OBSERVATION_AUDIT.md         context/history and delta protocol
V9_EPISODE_CONCURRENCY_AUDIT.md          Episode-level concurrency rationale
V8_JSON_HISTORY_MIGRATION_AUDIT.md       action-envelope/history rationale
CODELAB_PORTABILITY.md                   Mac-to-Linux runtime boundary
RESPONSIBILITY_DEFINITION_REVIEW.md
responsibility_ai_coding_v1/NATURAL_PRIMARY_QUERY_PROTOCOL_V2_4.md
responsibility_ai_coding_v1/STANDING_QUERY_PROTOCOL_V2_3.md
```

When this prompt and a normative frozen specification differ, report the
difference explicitly. Do not silently choose the newer prose or mutate the
frozen specification.

## Non-negotiable evidence rules

Apply these rules strictly:

1. A hand-written state machine, mocked transition, precomputed lookup table,
   or surrogate must not be described as a real physical backend.
2. Importability, schema inspection, static tests, or a successful reset do not
   prove action-sensitive dynamics.
3. Replay evidence, Agent-interface evidence, and mechanism/coupling evidence
   are three separate claims; none implies the others.
4. A real route must preserve one native environment instance over a trajectory
   and support the effective loop `reset -> observe/legal_actions -> step ->
   observation` without rerunning the full prefix on every step, unless that
   limitation is explicitly disclosed.
5. Evidence must fail closed when a runtime, model, asset, or provenance check
   is missing or stale. A generated JSON file is not proof by itself.
6. D3 requires a bidirectional or otherwise clearly demonstrated cross-system
   intervention through a shared constraint. Merely summing two independent
   loads is insufficient.
7. Distinguish native backend constraints from benchmark-added constraints.
   In particular, the CityLearn multi-building shared-meter threshold is
   externally imposed (`native_clipping=false`), whereas EV2Gym's transformer
   overload is claimed to be native.
8. Tests that only assert fields in generated evidence are weaker than tests
   that invoke the real runtime. Identify circular tests and self-attestation.
9. Platform-specific local binaries or absolute runtime paths must be called
   out as migration/reproducibility risks, even if they work on this Mac.
10. Do not infer Episode validity from backend validity. Query semantics,
    scenario selection, feasibility, leakage, and evaluator correctness require
    separate evidence.

## Audit questions

### A. Architecture and public Agent loop

1. Is there genuinely one stable public interface for every supported D0--D3
   route? Report exact differences or adapter leaks.
2. Does `step(action, dt)` perform one persistent online transition and return
   an incremental observation rather than appending a full environment snapshot
   or rebuilding history?
3. Are actions validated before the native state advances? Do invalid actions
   leave state and time unchanged?
4. Are time, termination, truncation, reset determinism, observation freshness,
   and resource cleanup handled consistently?
5. Can hidden future events, faults, reference policies, evaluator clauses, or
   witness actions leak through observations, schemas, `info`, prompts, or
   generated public artifacts?

### B. D0--D3 claim verification

For every route, trace this chain:

```text
catalog claim
  -> factory/registry entry
  -> adapter implementation
  -> native runtime/model/assets
  -> executable probe
  -> generated evidence with provenance hashes
  -> test that independently verifies behavior
```

For each route decide one of:

```text
VERIFIED
PARTIALLY_VERIFIED
EVIDENCE_PENDING
MISCLASSIFIED
BROKEN
```

Specifically check:

- whether D0 really models externally arriving/cancelled/conflicting events and
  what is currently only a small custom runtime;
- whether each advertised D1 fault mode is implemented, observable where
  appropriate, action-sensitive, recoverable where claimed, and tested against
  the relevant native device route;
- whether every D2 route supports an online persistent `step(action) ->
  observation` loop using the real physics engine rather than batch replay or a
  replacement model;
- whether each D3 route has at least two controllable channels and a
  counterfactual showing that changing A changes B's feasible allocation/state
  or outcome through the claimed shared resource.

### C. Evidence and test integrity

1. Identify stale hashes, evidence regenerated from its own assertions,
   tests that duplicate implementation logic, and status promotion based only
   on file presence.
2. Separate lightweight unit tests from tests/probes that actually invoke
   CityLearn, EV2Gym, WNTR, FDS, Modelica/FMI, and EnergyPlus.
3. Determine whether the reported latest focused result (`73 passed`) covers
   all six D3 routes and real probes, and whether a wider regression reveals
   inconsistencies.
4. Check whether runtime versions, model inputs, seeds, and assets are pinned
   sufficiently to reproduce results on a Linux codelab host.
5. Look for silent fallbacks, broad exception handling, no-op actions, fake
   action sensitivity, prefix re-execution, and generated artifacts committed
   without reproducible builders.

### D. Episode-construction design

Evaluate the proposed construction order below. It is a proposal, not an
implemented claim:

```text
human-supported canonical responsibility
  -> freeze semantic query skeleton and backend-independent task contract
  -> define physical opportunity predicate (without solver/Agent outcomes)
  -> sample native backend scenario and reset state
  -> admit a candidate using physical opportunity facts
  -> solve/search for at least one private witness policy
  -> replay witness in the same real backend as a release/feasibility gate
  -> test terminal and trajectory predicates plus controlled violations
  -> bind public scenario slots and realize/paraphrase the surface query
```

This ordering has an important two-level Query distinction. The canonical
responsibility and semantic Query skeleton must be frozen before backend search,
so backend availability cannot invent the human objective. The final
Episode-specific surface Query may be rendered after scenario binding only to
insert already-authorized public slots (for example, a departure deadline or
named controllable devices). It must pass bidirectional semantic-equivalence
review and may not import hidden future state, witness actions, or a
backend-convenient objective. Ask whether the repository currently enforces
this distinction or merely documents it.

Likewise, distinguish candidate membership from release feasibility. Membership
must be determined by a frozen, backend-independent physical opportunity
predicate and diversity policy, not by Agent or solver performance. A private
witness may reject an otherwise selected candidate as infeasible, but must not
rename the task, tune the contract, rank candidates by preferred-policy reward,
or become a released gold action trace.

The intended record separates:

- `query`: natural language shown to the Agent;
- `initial_observation`: backend-produced, pre-action public observation;
- `success_spec`: acceptable terminal predicates, trajectory constraints,
  active scope, horizon, and optional soft objective;
- `witness_result`: private proof that the task is feasible, including replay
  digest and final observation, but not a unique gold action sequence;
- `hidden_scenario`: private future events, faults, weather, arrivals, prices,
  and other latent backend inputs;
- `provenance`: backend/runtime/model/seed/version hashes.

The `initial_observation` must be captured before the first Agent action from
the exact persistent native environment instance used for the Episode. Private
state may additionally store the backend reset/configuration state and hidden
exogenous schedule, but public/private schemas must explicitly distinguish
observable state from latent simulator state. Reject an initial state when the
no-op already satisfies an achievement task, no legal policy can satisfy the
frozen hard clauses, actions do not change evaluation, required public fields
are missing, or the task only becomes solvable by leaking future information.

There should normally be no single exact `correct_state`. The authoritative
answer is `success_spec`: a set of acceptable terminal predicates plus
trajectory-wide temporal/safety constraints and optional lexicographic soft
cost. `witness_result.final_observation` is one backend-produced feasibility
example, not the equality target for scoring. Require the audit to flag every
place where witness state/action data are accidentally treated as gold.

Determine whether this avoids circular supervision. Find any cases where a
single final-state equality would be incorrect because multiple trajectories
are valid or because safety/temporal constraints must hold throughout. Propose
concrete admission gates for D0, D1, D2, and D3, including:

- non-triviality (no-op does not already satisfy the task);
- feasibility (at least one independently replayed witness passes);
- action sensitivity (a controlled contrast changes evaluation);
- determinism or explicitly modeled stochasticity;
- semantic consistency between query and frozen task contract;
- no leakage of witness actions or hidden future information;
- evaluator sensitivity to at least one targeted violation.

Also assess whether query generation should be template/slot-first with LLM
paraphrasing only after the contract is frozen, and describe what human review
is still needed.

## Suggested checks

Start with inspection before running broad tests. Useful commands may include:

```bash
cd /Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/data-pipeline/prototypes/v11_unified_process_compiler
/opt/anaconda3/bin/python -m pytest tests/test_agent_interface.py tests/test_d2_closed_loop_protocol.py tests/test_dynamic_mechanism_catalog.py -q
/opt/anaconda3/bin/python -m pytest tests/test_d3_citylearn_agent.py tests/test_d3_citylearn_coupling.py tests/test_d3_citylearn_multibuilding.py tests/test_d3_wntr_water_competition.py tests/test_d3_modelica_shared_heat.py tests/test_d3_ev2gym_electric_competition.py tests/test_d3_energyplus_shared_ventilation.py -q
```

Do not treat these commands as sufficient. Locate and run the real-runtime
`probe_*.py --check` commands that generate each cited evidence artifact. If a
probe requires a dedicated environment, report the exact environment and
whether the dependency is portable instead of silently substituting another
Python interpreter.

## Required deliverable

Produce one evidence-backed report with these sections:

1. **Executive verdict:** what the project can honestly claim today.
2. **Claim matrix:** one row per D0--D3 route with classification, native
   backend, persistent Agent loop, replay evidence, mechanism/coupling evidence,
   status, and exact supporting file paths.
3. **Critical findings:** ordered by severity, each with code/evidence location,
   reproduction method, impact, and minimal fix.
4. **False-positive risks:** any place where tests/catalogs may overstate real
   capability.
5. **Episode readiness:** what is implemented versus still only designed for
   query, initial state, success specification, witness replay, and evaluator.
6. **Linux/codelab migration risks:** platform binaries, absolute paths,
   dependency environments, asset size, and reproducibility.
7. **Recommended next actions:** the smallest ordered set of changes required
   before generating benchmark Episodes.

Use precise language. Do not collapse `works on this Mac`, `real-runtime
verified`, `portable`, and `benchmark-ready` into one status. Quote exact file
paths and line numbers wherever possible, and explicitly say when evidence is
insufficient.
