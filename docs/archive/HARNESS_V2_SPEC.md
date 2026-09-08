# Responsibility Harness V2 Specification

Status: **round-4 major revision in progress; implementation and Episode generation remain blocked pending semantic conformance tests and a clean independent review**

Normative machine-readable contracts:

- `harness_v2/interaction_state_machine_v2.json`
- `harness_v2/rule_dsl_v0.json`
- `harness_v2/public_private_schema_v2.json`
- `harness_v2/evaluator_and_group_protocol_v2.json`
- `harness_v2/shared_types_v2.json`
- `harness_v2/session_protocol_v2.json`
- `harness_v2/evaluation_bundle_v2.json`

If prose in this document conflicts with a normative contract, the conflict blocks implementation and must be adjudicated; prose never silently overrides the machine-readable contract.

## 1. Benchmark question

The harness evaluates whether an agent can turn a resident's natural-language standing responsibility into persistent, context-sensitive behaviour over a changing home process.

The required reasoning is not low-level thermostat control. It is identifying and maintaining:

- the responsible outcome;
- beneficiary and spatial scope;
- activation and suspension conditions;
- persistence, deadline, and release semantics;
- authorized actions and priorities;
- when clarification or future monitoring is necessary.

The harness must never infer these semantics for the agent, select target devices from the responsibility, or silently repair an agent action.

## 2. Unit under test

An evaluated agent is a stateful policy session. At Episode reset it receives one public `agent_view`. At later decision points it receives observation deltas and prior execution results. It may use only the public tools defined below.

The simulator's internal tick is not an agent decision step. Physical dynamics may advance for many ticks between agent invocations. There is no generic periodic rule tick: rule trigger and expiry instants enter the same deterministic scheduler queue as physics boundaries, exogenous events, workflow completions, requested wakes, replies, and the horizon.

## 3. Interaction lifecycle

```text
reset Episode
  -> deliver public bootstrap view
  -> agent issues zero or more public tool calls
  -> validate the complete transaction
  -> atomically execute valid commands / install valid rules
  -> advance the simulator until the next public decision event
  -> deliver observation delta and execution feedback
  -> repeat until the private termination condition
  -> seal the full trace
  -> run the private evaluator offline
```

The agent is invoked only at:

1. Episode reset;
2. a wake-up time explicitly requested by the agent;
3. a public event to which the agent subscribed;
4. completion or failure of an agent-created workflow to whose result the agent subscribed;
5. a user reply to an allowed clarification;
6. Episode termination, for notification only.

The harness must not wake the agent because a private evaluator constraint is at risk. Such a callback would leak the gold contract. Unsubscribed public events are recorded but do not create callbacks. If the agent requests no future wake-up or subscription, the environment advances to termination unless a subscribed workflow result or allowed user reply occurs. Multiple eligible reasons at one timestamp are coalesced into one callback. When the callback budget is exhausted, installed rules continue to run until termination; only further non-termination Agent callbacks are suppressed.

One Agent turn contains a read-only inspection phase followed by exactly one terminal choice: commit one mutation transaction, ask one user question in the interactive track, or yield without mutation. A mutation transaction may combine immediate commands, rule creation/cancellation, subscriptions, and wake requests; it is validated against an immutable backend snapshot and commits entirely or not at all. On failure, backend/rule/subscription/wake state and its digest do not change, while the separate accounting ledger records exactly one attempted transaction and one protocol error. Snapshot digests exclude that ledger.

## 4. Public tools

All tools are backend-independent and versioned. Backend adapters translate them to native APIs.

### 4.1 `inspect`

Read current public state for declared rooms, devices, workflows, and public context. It cannot query future traces, private labels, evaluator state, or undeclared sensors.

### 4.2 `act_now`

Issue a sparse, atomic list of capability commands. An empty command list is an explicit no-op.

```json
{
  "commands": [
    {
      "device_id": "kitchen_hvac_1",
      "capability": "thermal_control",
      "operation": "set",
      "parameters": {"mode": "heat", "target_c": 22.0}
    }
  ]
}
```

No parameter has an implicit semantic default. Operations that semantically take no parameters use an explicit empty `{}` object. Missing, unknown, unauthorized, or out-of-range fields invalidate the entire transaction. Invalid actions change no backend/rule/subscription/wake state, consume one transaction attempt, and return a public protocol error.

### 4.3 `create_rule`

Install a persistent automation over public variables. The rule language is common to all responsibility families and does not encode a target responsibility. Exact trigger, condition, ordering, conflict, lifecycle, release, and failure semantics are normative in `harness_v2/rule_dsl_v0.json`; the harness provides no deadline planner or hidden controller.

```json
{
  "rule_id": "agent_evening_heat",
  "trigger": {"type": "named_period_enter", "period_name": "evening"},
  "condition": {
    "op": "lt",
    "field": "rooms.kitchen.temperature_c",
    "value": 20.0,
    "max_staleness_seconds": 300
  },
  "commands": [
    {
      "device_id": "kitchen_hvac_1",
      "capability": "thermal_control",
      "operation": "set",
      "parameters": {"mode": "heat", "target_c": 22.0}
    }
  ],
  "lifecycle": {"type": "until_named_period_exit", "period_name": "evening"},
  "on_release_commands": [],
  "cooldown_seconds": 0,
  "priority": 50
}
```

Only fields present in the public observation schema may appear in triggers or conditions. The harness validates syntax and authorization, not semantic correctness.

### 4.4 `cancel_rule`

Cancel an agent-created rule by public rule ID. Cancellation is explicit and logged.

### 4.5 `subscribe`

Request callbacks for public events or state transitions. Subscription does not reveal whether an event is relevant to the private responsibility.

### 4.6 `wake_at`

Request a future callback at an explicit public time. Periodic monitoring must be requested by the agent and counts toward the interaction budget.

### 4.7 `ask_user`

Ask one bounded clarification when the Episode enables interaction. One question requests exactly one enumerated semantic slot. Invalid or repeated-slot questions consume one clarification attempt and return a protocol error. The user simulator may answer only from a pre-existing visible user profile or evidence-grounded private response record. If no supported answer exists, it returns `unknown`; it never invents a hidden 22 C-style target.

## 5. Capability-family action layer

V2 begins with five reusable families:

1. `thermal_control`: mode, setpoint, fan, off;
2. `air_quality_control`: purifier/humidifier/dehumidifier power and intensity;
3. `lighting_and_shading`: power, level, covering position;
4. `flexible_appliance_cycle`: configure, start, pause, resume, stop;
5. `energy_storage_and_charging`: charge, discharge, idle, power limit, target-energy configuration, start, pause, resume, and stop. The harness provides no deadline-compatible planner; the Agent must express timing through rules and wakes.

An Episode publishes the complete controllable inventory for its home, including controllable devices that are currently unavailable or offline with their public availability state, not only devices relevant to the query. Inventory selection is independent of the assigned Query or private Contract. Target scope is derived by the agent from language and public context. Capability commands are sparse: the agent need not repeat unchanged settings or cover every room.

## 6. Public `agent_view`

The reset payload contains exactly:

- the natural-language query;
- visible user preferences, named periods, tariff, safety limits, priorities, and action costs;
- the complete public room/device inventory and capability schemas;
- initial observations and virtual clock;
- available public event types and filter schemas;
- tool schemas, interaction budgets, and horizon disclosure policy;
- provenance class of every preference (`user_stated`, `user_history`, `environment_observable`, or `system_configuration`). A value is public only when it would be available in real execution independently of evaluator construction and Episode selection. `source_hash` and `visibility_justification` remain audit-only and are never serialized to the Agent.

Large static schemas are sent once. Later callbacks contain only timestamped state deltas, public events, workflow status, and execution feedback. The conversational session retains the bootstrap view. All runtime instants use RFC3339 timestamps with explicit offsets; comparisons use absolute instants and presentation uses the frozen Episode timezone.

## 7. Private evaluator boundary

The agent never receives:

- responsibility/type/variant IDs or the canonical Contract;
- evaluator active masks, gold spatial scope, or success predicates;
- future exogenous traces, simulator seed, source window, or selection stratum;
- witness, no-op, oracle, counterfactual query assignment, or scores;
- construction parameters unless explicitly promoted to public scenario parameters.

Public and private payloads are constructed separately and checked by a static leakage allowlist plus dynamic canary tests. The public serializer accepts only the public DTO and has no evaluator/private dependency; import/dependency tests and nested canaries must prove that a private evaluator object cannot enter an Agent message.

## 8. Backend adapter contract

Every backend adapter must implement:

```text
reset(private_seed) -> native_state
public_inventory(native_state) -> normalized_inventory
public_observation(native_state) -> normalized_observation
validate(public_transaction, normalized_inventory) -> verdict
apply_atomically(valid_transaction, native_state) -> execution_result
advance_until(next_public_decision_event) -> native_state, public_events
private_trace_frame(native_state) -> evaluator_frame
snapshot_digest() -> deterministic replay digest
```

Capability matching is `FULL`, `PARTIAL`, or `UNSUPPORTED` and separately checks:

- required observations;
- required actions;
- physical dynamics;
- lifecycle events;
- evaluator primitives;
- spatial and temporal resolution;
- replay determinism and fidelity tier.

Only `FULL` bindings may produce main-track Episodes. `PARTIAL` bindings are calibration assets and retain an explicit missing-capability list.

## 9. Offline scoring

The evaluator runs only after both the physical trace and complete Agent session transcript are sealed, and reports a vector, never only a binary success. `J` is a preregistered private benchmark loss, not a public loss or claimed user utility:

- `protocol_valid`;
- `safety_pass` and safety deviation;
- responsibility deviation over the privately active room-time set;
- action, switching, irrelevant-intervention, and clarification costs;
- normalized gain relative to no-op and a public-information oracle.

For private loss `J`, a versioned family evaluator manifest freezes every component input and unit, integration boundary, missing/stale rule, normalizer source, aggregation, weight, safety threshold, oracle implementation/solver/tolerance, and canonical serialization rule. Raw normalized gain is:

```text
G_raw = (J_noop - J_agent) / (J_noop - J_oracle)
```

For eligible safe traces, raw gain, raw normalized regret, clipped display gain, and the denominator are all reported; negative raw gain is preserved. Protocol-invalid, unsafe, oracle-failed/nonfinite, nonpositive-denominator, below-threshold, and exactly-equal-threshold cases enter mutually exclusive status branches and cannot carry a headline gain. Agent, no-op, and oracle losses must each be bound to an independently sealed run receipt with identical frozen controls. The semantic oracle knows the canonical Contract but receives only the Agent's online observations, actions, callback budget, cadence, and no future information.

## 10. Language-identification design

Formal releases are organized into counterfactual scenario groups. Each group has exactly two Contract arms, A and B. Each arm owns one canonical Contract/evaluator and contains one source-near Query plus zero or more faithful paraphrases; paraphrases therefore cannot silently change the evaluator. The two arms freeze the same initial public state, complete inventory, simulator seed, exogenous realization, costs, budgets, horizon policy, split, and track. `contrast_axis` plus a contrast witness declares the sole canonical-contract dimension that differs. Agent actions may cause later endogenous observations, device/workflow state, and events to diverge.

Examples include kitchen-only versus whole-home warmth, evening comfort versus away-mode energy saving, or complete-by-deadline versus avoid-running-during-a-period. All group members remain in one dataset split.

Required diagnostics are:

- query deletion;
- query shuffle within a scenario group, with the policy rerun closed-loop under the same frozen exogenous realization rather than against a reused backend trace;
- source-near query versus faithful paraphrases;
- query-blind controller;
- query-aware rule compiler plus domain controller;
- end-to-end agent;
- public-information oracle.

If query deletion/shuffle does not materially alter preregistered responsibility deviation or irrelevant-action cost under the declared group aggregation rule, the group cannot support a language-understanding claim. Track is only a stratification variable; a counterfactual group never crosses tracks.

## 11. Episode admission gates

An Episode enters the main track only if all gates pass:

1. responsibility evidence and semantic variant are admitted at the declared validation level;
2. backend match is `FULL`;
3. all reasonable decisions are supported by public information;
4. actions causally affect the evaluated outcome;
5. oracle is feasible and meaningfully better than no-op, and the preregistered main-gain threshold is met under the evaluator manifest's explicit denominator and boundary rules;
6. at least one paired query in the scenario group requires a distinct policy;
7. deterministic replay and atomic-action tests pass;
8. static and dynamic leakage tests pass;
9. no hidden preference is needed to define a unique numeric gold;
10. source/process connected components remain in one split.

## 12. Non-goals and forbidden shortcuts

- Do not call the LLM at every simulator tick by default.
- Do not expose a target-specific device subset or `required_rooms` list.
- Do not default missing targets, modes, or schedules.
- Do not use fallback actions after model errors.
- Do not wake the agent on private contract violations.
- Do not return online evaluator feedback.
- Do not choose an Episode because a desired model success rate is reached.
- Do not claim human validation for AI-proxy coding.

## 13. Pilot implementation sequence

1. Implement schemas and an in-memory fake backend before changing real simulators.
2. Prove protocol, atomicity, scheduling, subscription, trace sealing, and leakage tests on the fake backend.
3. Bind SimuHome through complete-home thermal plus one non-thermal family; treat its simplified physics as a declared semantic/control fidelity tier.
4. Bind one physically stronger family (EnergyPlus thermal or CityLearn battery/PV) without changing the public harness API.
5. Construct counterfactual scenario groups, not isolated single-query windows.
6. Run no-op/oracle/query-blind/query-aware baselines before any general LLM evaluation.

## 14. Review acceptance criteria

Independent review must explicitly answer yes to all of the following before implementation proceeds:

- Does the harness measure responsibility interpretation and persistent fulfilment rather than repeated low-level control?
- Is every agent-visible field defensible as public information?
- Can an agent create, monitor, and release a standing policy without per-tick LLM calls?
- Can an invalid action avoid state mutation without receiving a repaired fallback?
- Are simulator dynamics, agent decisions, and evaluator cadence separated?
- Do counterfactual scenario groups make language causally identifiable?
- Can all headline metrics be reproduced from a sealed trace?
- Are current claims appropriately limited by evidence validation and backend fidelity?
