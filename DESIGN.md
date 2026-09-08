# v11 Unified Process Compiler — Design

## Problem

Prototypes v4–v10 built one environment per responsibility (HVAC here, EV there).
That fragments the pipeline: N environments, N ad-hoc episode generators, N evaluators,
and no shared notion of "a physical process that a responsibility operates on".

v11 replaces that with **one shared data-construction pipeline** for all household
physical scenarios.

## "All together" — what it means and what it does not

- **One schema**: every scenario is described by the same typed objects
  (`BackendCapability`, `ProcessRequirement`, `PhysicalProcess`,
  `ResponsibilityContract`, `Episode`, `TrajectoryClause`).
- **One compiler**: `unified_compiler/compiler.py` matches requirements to
  capabilities, scans adapters, stores manifests, binds contracts — the same
  code path for HVAC, DHW, batteries, PV, blinds, robots, anything.
- **One registry**: `AdapterRegistry` + canonical `ProcessPool` guarantee each
  physical process is stored exactly once.
- **Multiple real physics engines remain adapters**: CityLearn, future IAQ
  simulators, robot-coverage simulators, etc. are *not* merged into one monolith.
  Each implements the `ProcessAdapter` protocol (capabilities + one batched
  `scan`). The compiler talks to the protocol, never to engine internals.

So: unified at the schema/compiler/registry level, plural at the physics level.

## Top-down pipeline

```
1. Responsibility lifecycle + topology    (what control loop / what dynamics?)
        |
2. Process requirements                     (what state/action/capability does it need?)
        |
3. Backend capability registry              (what do installed engines actually offer?)
        |
4. Canonical physical process pool          (each real process stored ONCE)
        |
5. Contract binding                         (exactly ONE primary responsibility per
        |                                     main process; semantic_control bindings
        |                                     may explicitly reuse it, labeled)
6. Executable Episode                       (contract + process + horizon + exogenous events)
        |
7. Trajectory evaluator                     (typed state-trajectory clauses, gold-action-free)
        |
8. QA / coverage selection                  (which episodes ship, per evaluator verdicts)
```

### 1. Responsibility lifecycle & physical topology taxonomy

Every household responsibility is classified along two independent axes, matching
the two enums in `unified_compiler/types.py`:

- **Responsibility lifecycle** — the kind of control loop the responsibility
  runs (`ResponsibilityLifecycle`): `MAINTAIN`, `ACHIEVE_BY`, `PREPARE_FOR`,
  `RECOVER_AFTER_EVENT`, `OPTIMIZE_UNDER`, `GUARD`.
- **Physical topology** — the physical dynamics the underlying process has
  (`PhysicalTopology`): `THERMAL_DYNAMICS`, `STORAGE_DYNAMICS`,
  `CONTAMINANT_DYNAMICS`, `NONINTERRUPTIBLE_CYCLE`, `SPATIAL_MOBILITY`.

The two axes together are the top-level organizing dimensions of
`scenario_catalog.json`.

### 2. Process requirements

A `ProcessRequirement` declares, per responsibility: responsibility lifecycle,
physical topology, state variables, legal action types, and the **capability
keys** a backend must provide
(e.g. `storage.soc`, `storage.charge_discharge_action`). It is engine-agnostic.

### 3. Backend capability registry

`AdapterRegistry` collects `BackendCapability` objects from registered adapters
plus the static `capability_catalog`. Every capability carries a verification
status: `API_VERIFIED_NOT_DATA_PROBED`, `LEGACY_EXECUTABLE_PENDING_MIGRATION`,
or `CAPABILITY_UNVERIFIED`. The compiler **fails closed**: a requirement with no
capable adapter is rejected, never silently approximated.

### 4. Canonical physical process pool

`ProcessPool` stores each `PhysicalProcess` under a canonical `process_id`.
Re-scanning or recompiling deduplicates: one physical water tank is one pool
entry, no matter how many responsibilities reference it.

### 5. Contract binding

A `ResponsibilityContract` binds a responsibility to a process. Roles:

- `PRIMARY` — the main responsibility for that process. **Exactly one per
  process.** A second primary binding raises `PrimaryBindingConflict`.
- `SEMANTIC_CONTROL` — explicit, separately-labeled reuse (e.g. a cost-shaping
  view over the same tank). Many allowed, never confused with the main binding.

### 6. Executable Episode

An `Episode` pairs a contract with a process, a horizon, and exogenous events
(weather, occupancy, prices). Backend-specific adapters expose state/action
semantics while the shared compiler emits the same public/private Episode
contract. Physical trajectories are replayed from the bound backend; no fake
trajectory or gold action sequence is authored.

### 7. Trajectory evaluator

`evaluator.py` evaluates typed `TrajectoryClause`s over a `StateTrajectory`
(state variables × time). There are **no gold actions** — membership and quality
are judged from states only. Clause kinds:

- `HARD_INVARIANT` — must hold at every step (e.g. SOC ∈ [0, 1]).
- `TERMINAL_GOAL` — must hold at the final step (e.g. EV SOC ≥ 0.8 at departure).
- `CUMULATIVE_SOFT_COST` — summed per-step penalty (e.g. comfort-band deviation).

Scoring is **lexicographic**: hard feasibility first, then soft cost. A
hard-infeasible trajectory can never outrank a feasible one.

### 8. QA / coverage selection

Evaluator verdicts (per-clause results, feasibility, cost) are the inputs to
downstream QA/coverage selection, which decides which episodes enter a dataset.
Selection consumes evaluator output only — never solver/agent internals.

## Current verified scope

- Frozen v10 migration contributes 50 HVAC and 36 EV canonical processes.
- The new battery/PV path scans 4,380 source-grounded modeled residence-days, selects 48
  requirement-relevant and season-diverse processes, replays them with a fixed
  full-year device configuration, and binds 48 primary Responsibility Episodes.
- Process membership is determined by requirement-aligned physical facts
  (chronological PV surplus, later grid deficit, transferable energy and
  diversity cells). A private fixed policy is used only after selection as a
  feasibility gate; solver/Agent outcomes are never membership inputs and no
  gold actions are released.
- HVAC, EV and battery/PV remain separate physics adapters behind the shared
  schema/compiler/evaluator rather than being merged into one simulator.
