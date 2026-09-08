# Research brief for designing Responsibility Episodes with GPT Pro

This document is the single discussion entry point for the next research
stage. It explains why the backend exists, what has been implemented and
accepted, what an Episode means, how task correctness should be defined, and
which design decisions still need to be made before generating a dataset.

The current repository contains an accepted physical-backend layer. It does
**not** yet contain a released Responsibility Episode dataset. Backend validity
is necessary for Episode construction, but it does not establish that a task is
human-grounded, feasible from public information, leakage-free, or correctly
evaluated.

## 1. Research question

The project studies whether an Agent can maintain household responsibilities
whose intent is persistent or temporal while the environment changes. Examples
include keeping indoor air safe while occupancy and weather change, ensuring
devices complete work despite faults, and allocating a shared resource across
coupled household subsystems.

The target is not a collection of one-shot device commands. Each task should
require an Agent to observe a changing process, choose authorized actions over
time, and satisfy a trajectory-level responsibility. The benchmark should
measure the environmental outcome and temporal/safety constraints rather than
whether the Agent reproduced a preferred action sequence.

The construction direction is deliberately one-way:

```text
human evidence
  -> canonical responsibility and authorized scope
  -> user-facing Query and trajectory Contract
  -> backend-independent physical opportunity predicate
  -> source-grounded physical process
  -> executable Episode
  -> downstream Agent evaluation
```

A simulator must not invent a responsibility merely because it exposes a
convenient control variable. Likewise, an Agent or baseline result must not
decide which tasks enter the dataset.

## 2. Why the architecture is separated

Earlier prototypes tended to create a separate environment, task definition,
and evaluator for each household responsibility. That coupled the benchmark's
ontology to whichever backend was available and encouraged duplicated physical
processes with inconsistent evidence standards.

V11 therefore keeps these objects distinct:

```text
human-supported responsibility semantics
  != backend-independent Query and Contract
  != canonical physical process
  != backend adapter and runtime
  != Agent conversation and action loop
  != trajectory evaluator and dataset decision
```

The implementation is unified at the public schema, route registry, Agent
protocol, executor, and evaluation boundaries. Different native physics engines
remain behind adapters. The shared layer normalizes receipts and lifecycle; it
must not fabricate missing physics or silently fall back to a surrogate.

## 3. Dynamic-mechanism levels

The D0--D3 levels describe the environmental dynamics the Agent must handle.
They are not a ranking of simulator prestige or generic task difficulty.

| Level | Definition | Representative mechanism |
|---|---|---|
| D0 | Exogenous discrete context or event changes | occupancy, doors, weather, cancellations, conflicting events |
| D1 | Device health changes the effect of the same command | degradation, stuck actuator, dropout, sensor bias or drift |
| D2 | Actions affect a continuously evolving physical process | air quality, thermal dynamics, water pressure, smoke/fire |
| D3 | Two or more controllable subsystems share a finite resource or constraint | transformer capacity, shared flow, heat or ventilation allocation |

A D3 claim needs a same-prefix counterfactual showing that intervention on one
channel changes another channel's feasible action, allocation, state, or
attainable outcome through the shared constraint. Merely summing independent
loads is insufficient.

## 4. Current backend status

The accepted local campaign covers 15 registered routes: one D0 route, four D1
routes, four D2 routes, and six D3-labelled routes. For every route the current
acceptance package checks three full-horizon seeds, action-sensitive branches,
repeated reset/close cycles, process-isolated concurrency, source bindings, and
fail-closed evidence. The campaign also includes a 600-second active stability
run and a bounded 30-task queue run.

The source of current evidence is
[`acceptance/backend_acceptance_local_final/acceptance.json`](acceptance/backend_acceptance_local_final/acceptance.json).
The route contract is
[`unified_compiler/route_registry.py`](unified_compiler/route_registry.py), and
the public backend boundary is
[`unified_compiler/agent_interface.py`](unified_compiler/agent_interface.py).

Two capability limits must stay explicit:

1. `fds_smoke_fire` executes the real FDS backend through full-history/prefix
   replay. Native synchronous online stepping is unsupported. It may be used
   only where this disclosed execution model is compatible with the Episode.
2. `d3_citylearn_multi_system` can generate action-sensitive Episodes, but
   native cross-channel D3 coupling has not been established. It must not enter
   a coupling-required D3 slice until that claim has independent evidence.

The simulator binaries and large runtime assets are local external
dependencies. A fresh GitHub clone needs the pinned runtimes described in
[`RUNTIME_DEPENDENCIES.md`](RUNTIME_DEPENDENCIES.md) and must rerun the strict
preflight and acceptance checks.

## 5. What an Episode is

An Episode binds exactly one primary responsibility, one Query, one Contract,
and one physical process to a finite interaction protocol:

```text
Episode =
  frozen responsibility semantics
  + semantically equivalent public Query
  + trajectory-level success Contract
  + admitted native scenario/window
  + initial public observation
  + legal action schema
  + cadence, horizon, and termination semantics
  + private evaluator/provenance state
```

The initial observation must be obtained after resetting the exact persistent
backend instance used for the Episode and before the first Agent action. A
separate reset that happens to use the same seed is weaker evidence.

For each interval `[t, t+1)`, the intended ordering is:

```text
publish observation and pending obligations at t
  -> receive and validate an Agent action
  -> apply one backend transition
  -> obtain the backend-produced observation at t+1
  -> evaluate temporal clauses over the transition
  -> apply releases and deadlines effective at t+1
```

The Episode horizon is an evaluation window. Reaching its end does not imply
that a standing responsibility has been fulfilled forever. Pending obligations
must be classified as released, continuing, censored-but-recoverable, or
violated at truncation under a frozen terminal rule.

## 6. Where tasks and correct answers come from

### Task requirement

The task requirement comes from a frozen `CanonicalResponsibility`, not from a
simulator trace. Human evidence must support the desired state, beneficiary,
responsible or delegating party, persistence/recurrence, unacceptable failure,
override/release condition, and the Agent's authorized action scope.

The public Query is a natural-language realization of that responsibility. It
may fill public scenario slots such as a visible departure deadline or device
name, but it may not add a backend-convenient objective, hidden future event, or
suggested action. Bidirectional semantic review must establish that the Query
adds no obligation and omits no essential clause.

### Correct outcome

There is normally no single exact `correct_state` and no released gold action
sequence. Correctness is a frozen `success_spec`/Contract evaluated over the
entire backend-produced trajectory. It may include:

- hard invariants that must hold at every relevant step;
- conditional trigger-to-deadline obligations;
- terminal goals;
- release and override conditions;
- safety and authorization constraints;
- cumulative soft costs with a declared priority order.

Many different policies and final states may satisfy the same responsibility.
The evaluator should report clause-level results and a lexicographic verdict,
for example hard violation count, hard deficit, then soft cost. It must not
score equality with a reference trajectory.

### Private witness

A construction oracle searches for at least one legal non-anticipating policy
that succeeds using only the public observation interface. Its replayed trace is
private feasibility evidence. The witness may reject an infeasible candidate,
but it must not:

- define or rename the responsibility;
- change the frozen Contract after inspecting the scenario;
- become the Agent's gold answer;
- leak actions, latent future state, source IDs, or evaluator clauses;
- select samples according to the performance of an experimental Agent.

The no-op policy must also be evaluated. An achievement Episode is trivial and
should be rejected if no-op already satisfies it. A maintenance Episode may
legitimately require waiting, so non-triviality must be defined by the frozen
opportunity predicate rather than a blanket requirement to act.

## 7. Agent, observation, token, and evaluator boundaries

### Agent boundary

The public interaction is `reset(seed) -> observe() -> legal_actions() ->
step(action, dt) -> close()`. Actions are validated before native state
advances. Invalid actions, invalid time steps, `NaN`, and infinity must fail
without advancing time or state. A poisoned native run cannot continue until a
new reset.

The Agent sees the Query, current public observation, legal action schema,
cadence/horizon, prior actions, and exact public receipts. It must not see the
hidden future schedule, fault realization, witness policy, evaluator clauses,
private backend state, process/source IDs, or split-sensitive lookup keys.

### Observation and conversation boundary

The backend always returns complete public observations. The provider-neutral
conversation layer may send the full initial observation once and exact,
lossless observation deltas thereafter to control context growth. The delta
chain must reconstruct every full observation exactly. Conversation compaction
must not discard Agent actions or backend action results.

### Token boundary

Token accounting is a measurement property of the LLM runner, not of the
physical backend. Provider-reported API output tokens should be the primary
generation-volume metric. Input/prompt tokens, cached tokens, reasoning tokens,
and provider totals should be retained as named diagnostics and must not be
silently relabelled as output tokens. The current repository has a
provider-neutral conversation formatter; provider API execution and finalized
cross-provider token normalization remain part of the Episode-runner design.

### Evaluator boundary

The evaluator consumes the backend-produced trajectory plus private frozen
Contract state. It judges outcomes, temporal obligations, safety, and costs. It
does not judge stylistic notification behavior in the current headline metric,
does not compare against witness actions, and does not trust Agent-supplied
claims about state. Missing evaluator variables, non-finite values, stale
lineage, and ambiguous terminal obligations must fail closed or produce a
declared censored/unsupported status.

## 8. Proposed end-to-end Episode generation plan

The frozen normative source is
[`DATASET_CONSTRUCTION_PIPELINE_V1.md`](DATASET_CONSTRUCTION_PIPELINE_V1.md).
The following is an implementation-oriented decomposition for discussion; it
must not silently override that specification.

### Phase A: freeze semantic inputs

1. Build auditable evidence units and bundles from real household evidence.
2. Admit a canonical responsibility only when semantic and authorization gates
   pass; retain unknown or refused delegation outside the executable track.
3. Freeze the responsibility, Query skeleton, Contract clauses, evaluator,
   thresholds, horizon policy, priorities, and rejected predicates.
4. Compile a backend-independent opportunity predicate describing required
   observations, actions, dynamics, information, and positive/boundary/no-op
   strata.

Output: content-addressed responsibility, Query, Contract, and opportunity
predicate objects. Any semantic edit creates a new version and invalidates all
downstream bindings.

### Phase B: mine candidate physical processes

5. Match the frozen opportunity predicate against verified route capabilities.
6. Sample source-grounded scenario/window candidates using preregistered seeds
   and selection rules. Record backend/runtime/model/assets and hashes.
7. Reset the native backend and capture the exact pre-action public observation,
   hidden scenario state, cadence, horizon, and legal action schema.

Output: candidate `PhysicalProcess` bindings. Backend availability may reject a
binding as unsupported but may not change the task semantics.

### Phase C: certify construction validity

8. Run the no-op policy and controlled interventions to establish
   non-triviality and evaluator-relevant action sensitivity.
9. Search a frozen finite witness-policy family on search seeds only.
10. Freeze the selected witness policy, then replay it on disjoint certification
    seeds in the same backend through the exact public interface.
11. Test physical feasibility separately from public-information feasibility.
    Retain matched public histories with different hidden futures and reject or
    mark boundary cases when one public policy cannot satisfy all compatible
    futures.
12. Attack the evaluator with controlled violations, missing observations,
    illegal actions, late triggers, unsafe shortcuts, no-op, and terminal-edge
    cases.

Output: physical opportunity label, witness digest, replay digest, clause-level
QA, information-set verdict, and explicit rejection/failure codes.

### Phase D: materialize and release Episodes

13. Bind public scenario slots and render surface Query paraphrases only after
    the candidate has passed semantic and physical gates. Recheck bidirectional
    equivalence and leakage.
14. Write separate public and private records. Keep witness actions and hidden
    futures private; expose only the Query, initial observation, schemas,
    interaction timing, allowed status fields, content hashes, and split.
15. Deduplicate by canonical physical process and semantic lineage, then apply a
    preregistered diversity policy across responsibility, mechanism, season,
    event topology, opportunity strength, backend, and horizon.
16. Construct train/dev/test splits using a conflict graph so near-duplicate
    processes, households, paraphrases, and responsibility families cannot leak
    across forbidden boundaries.
17. Run frozen conformance, deterministic replay, provenance, evaluator,
    leakage, count, and reproducibility checks before setting `released`.

Output: immutable Episode records plus a release manifest. Experimental Agents,
rules, MPC, or baselines are evaluated only after release is frozen.

## 9. Minimum Episode record proposed for implementation

The frozen schema in
[`conformance_v1/schemas/episode.schema.json`](conformance_v1/schemas/episode.schema.json)
is authoritative. For implementation planning, the essential conceptual split
is:

```json
{
  "public_record": {
    "responsibility_query": "natural-language task",
    "initial_observation": {},
    "observation_schema": "versioned reference",
    "legal_action_schema": "versioned reference",
    "cadence": "declared interval",
    "horizon": 0,
    "termination": "finite_window",
    "semantic_status": "...",
    "authorization_status": "...",
    "physical_status": "...",
    "content_hashes": {},
    "split": "train|dev|test"
  },
  "private_record": {
    "lineage": {},
    "backend_binding": {},
    "hidden_scenario": {},
    "source_hashes": {},
    "evaluator_clauses": "private reference",
    "qa_verdicts": {},
    "selection_stratum": "...",
    "replay_digests": {},
    "witness_digest": "...",
    "gold_actions": []
  }
}
```

The conceptual `hidden_scenario` field above may need to be represented through
the schema's existing backend-binding/lineage fields or added in a versioned
schema revision. That choice is an open implementation question; do not mutate
the frozen v1 schema in place.

## 10. Decisions to resolve with GPT Pro

The highest-value discussion is not whether the backend can step. That gate has
already passed for the declared route boundaries. The next decisions are:

1. What is the smallest defensible pilot responsibility set with real human
   evidence and explicit Agent authorization?
2. What exact Contract DSL subset is sufficient for the first pilot while
   preserving triggers, deadlines, invariants, release, priority, and terminal
   semantics?
3. How should each D0--D3 opportunity predicate define non-triviality,
   feasibility, public identifiability, and meaningful action sensitivity?
4. What witness-search family is broad enough to certify feasibility without
   becoming circular supervision, and how should search/certification seeds be
   separated?
5. How should the Episode schema represent hidden reset state, hidden future
   events, evaluator sufficient statistics, and replay handles without leaking
   them publicly?
6. Which current routes should enter the first dataset pilot, given FDS replay
   semantics and the unverified CityLearn cross-channel coupling claim?
7. What terminal anti-gaming rule and horizon policy should be frozen for each
   responsibility type?
8. What minimal evaluator attack suite is required before an Episode can be
   released?
9. What balance and split constraints are feasible for a pilot without allowing
   manual substitution or backend-driven task selection?
10. What concrete builder modules, manifests, and intermediate artifacts should
    be implemented first so every decision remains auditable and reproducible?

## 11. Prompt to give GPT Pro

Use the following prompt together with this repository:

> Read `GPTPRO_EPISODE_RESEARCH_BRIEF.md` first, then inspect
> `DATASET_CONSTRUCTION_PIPELINE_V1.md`, `GPT6_PROJECT_AUDIT_PROMPT.md`,
> `BACKEND_PUBLIC_PROTOCOL.md`, `unified_compiler/agent_interface.py`,
> `unified_compiler/route_registry.py`, `unified_compiler/evaluator.py`, the
> `conformance_v1` schemas/tests, and
> `acceptance/backend_acceptance_local_final/acceptance.json`. We now have an
> accepted 15-route physical-backend layer and need to design the first real
> Responsibility Episode generation pipeline. Treat the frozen construction
> specification as normative and explicitly report every conflict between it,
> this brief, schemas, and implementation. Do not infer Episode validity from
> backend validity. Help us decide where task requirements come from, how
> success is represented without a unique gold state/action trace, how private
> witnesses certify feasibility without creating circular supervision, and how
> public/private information is separated. Produce: (1) a gap analysis,
> (2) a minimal pilot scope, (3) exact intermediate data objects and gates,
> (4) D0--D3 admission tests, (5) a witness-search and replay protocol,
> (6) evaluator attack tests, (7) split/diversity rules, and (8) an ordered
> implementation plan with acceptance criteria. Mark design proposals separately
> from facts verified in code or acceptance evidence.

## 12. Reading order and authority

1. [`DATASET_CONSTRUCTION_PIPELINE_V1.md`](DATASET_CONSTRUCTION_PIPELINE_V1.md)
   and [`conformance_v1/MANIFEST.json`](conformance_v1/MANIFEST.json): frozen
   normative construction release.
2. [`GPT6_PROJECT_AUDIT_PROMPT.md`](GPT6_PROJECT_AUDIT_PROMPT.md): architecture,
   historical decisions, evidence rules, and adversarial audit questions.
3. [`BACKEND_PUBLIC_PROTOCOL.md`](BACKEND_PUBLIC_PROTOCOL.md) and current source:
   implemented backend/Agent boundary.
4. [`acceptance/backend_acceptance_local_final/`](acceptance/backend_acceptance_local_final/):
   current locally accepted evidence.
5. This brief: discussion guide and implementation-oriented synthesis.

If these sources conflict, the frozen normative package wins for construction
semantics. The conflict must be reported explicitly rather than reconciled
silently. Current code and acceptance evidence determine what the backend
actually implements; prose does not promote an unverified capability.
