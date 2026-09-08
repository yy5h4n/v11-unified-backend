# Evidence-Grounded Responsibility Induction

## Purpose

The benchmark must not derive household responsibilities from simulator
capabilities or from tasks that researchers find convenient to implement. It
first induces responsibilities from independent evidence of what people want
smart-home services to keep accomplishing over time. Only after these
responsibilities are frozen may executable backends be searched for relevant
physical processes.

The full construction pipeline is therefore:

```text
human-needs evidence + naturally authored automations
    -> evidence units and provenance ledger
    -> means-end semantic coding
    -> intent-only candidate grouping
    -> human merge/split and triangulation
    -> frozen canonical responsibility, Query, and Contract
    -> pre-registered physical-opportunity predicate
    -> backend window mining, replay, diversity selection, and Episode QA
```

The central methodological claim is:

> Responsibilities are human-evidence-derived and researcher-formalized before
> backend inspection; backends supply executable physical variation but do not
> invent benchmark responsibilities.

**Current status.** This document specifies a proposed induction study, not a
completed empirical method. The current V11 HVAC, EV, and battery/PV artifacts
predate it and must not be described as human-evidence-derived. In particular,
the present battery/PV compiler assigns objective families from mined backend
statistics; that behavior is incompatible with the proposed claim and must be
removed before an evidence-grounded release is produced.

## What counts as a responsibility

A standing responsibility is a recurring, temporally extended obligation to
maintain or bring about a human-relevant household state under changing
conditions. It is more abstract than an individual device command but more
operational than a value such as comfort, safety, or sustainability.

Canonical form:

```text
In <context/time horizon>, <responsible actor> should ensure or maintain
<desired state> for <beneficiary>, avoid <failure/harm>, and respect
<constraints/trade-offs>, until <release condition>.
```

We preserve a means-end ladder:

- L0 device action: turn the thermostat to 23 C at 17:00;
- L1 automation function: pre-cool the home before arrival;
- L2 contextual goal: make the occupied home comfortable without cooling it
  unnecessarily while empty;
- L3 standing responsibility: maintain occupancy-aware household comfort while
  limiting avoidable energy use;
- L4 human value: comfort, stewardship, or care.

Benchmark responsibilities normally live at L2-L3. L0-L1 are implementation
evidence; L4 is too broad to define an executable evaluator by itself.

A candidate passes the abstraction test only if:

1. it still makes sense after replacing the device or concrete action;
2. it names a beneficiary and a desired state or failure consequence;
3. it extends beyond one isolated command;
4. its meaning is supported by source evidence rather than analyst intuition.

It must also be distinguishable from neighboring constructs. A one-shot
instruction is a task; a desired outcome without temporal ownership may be a
goal; a trade-off or favored setting may be a preference. We call a construct a
standing responsibility only when evidence supports recurring or persistent
scope, an accountable actor, a beneficiary, a failure meaning, and an override
or release condition. This discriminant coding is itself validated rather than
assumed.

## Evidence roles

No single corpus is treated as complete ground truth. Sources play different
roles and are triangulated.

| Evidence source | What it supports | What it cannot establish alone |
|---|---|---|
| Interviews, diary studies, probes, and co-design | User vocabulary, desired outcomes, beneficiaries, trade-offs, failure meanings, and non-use | Population prevalence at scale |
| Naturally authored Routines or Trigger-Action rules | Ecological occurrence, recurrence, contexts, mechanisms, and household-level prevalence | A unique underlying purpose; rules usually encode means rather than why |
| Interaction/device logs | Whether routines were active, overridden, abandoned, or repeatedly used | User intent without linked qualitative evidence |
| Defaults, copied recipes, and synthetic rules | Expressible platform capabilities and possible mechanisms | Independent user demand |

This yields three explicit epistemic levels:

1. **Observed automation evidence**: what a household configured or used.
2. **Responsibility hypothesis**: a possible recurring purpose inferred from one
   or more evidence units.
3. **Validated responsibility**: a canonical construct supported across
   independent households and evidence types, with contradictions and scope
   boundaries recorded.

A raw rule such as `17:00 -> AC on` normally supports only compatibility with a
responsibility hypothesis. It becomes stronger evidence only when a rule name,
description, linked interview, or repeated contextual pattern identifies the
purpose.

## Corpus assembly and auditing

Candidate source families include user-needs studies and datasets such as
TAREME, Wyze/FedRule, SmartSense, and prior in-the-wild or elicited
Trigger-Action Programming studies. An initial audit found important corrections
to commonly repeated scale claims:

| Source | Audited evidence | Access and role |
|---|---|---|
| TAREME | 159 rules across six AAL apartment pilots with older residents/caregivers; the reported 35 usability tests are a separate study component | No verified downloadable raw corpus or dataset license; valuable contextual care evidence, not population prevalence |
| Wyze/FedRule | 201,940 rules from 76,218 users, reduced to 1,207 unique rule forms in the reported study | Internal Wyze rule-engine data; no verified public release/license; large-scale mechanism evidence only |
| SmartSense | Public four-country SmartThings/Bixby action-sequence and Routine artifacts; 19,650 sessions and 373,379 ten-action instances are reported | Public GitHub artifacts but no located repository license; strong sequence evidence, weak purpose/household-role evidence |
| Practical TAP / IFTTT | The original practical-TAP paper does not itself provide a verified downloadable corpus; later work reports an IFTTT snapshot of 144 users, 8,729 rules, and 3,020 rule types | Canonical current download and redistribution terms remain unverified; useful rule-form evidence with strong platform/self-selection bias |
| User Needs of Smart Home Services | Interviews and card sorting with 11 participants, yielding ten reported household challenges | No located raw transcripts; useful semantic theme discovery, not prevalence estimation |

These audited values replace earlier unverified claims of 638 TAREME rules or
roughly one million public Wyze rules. Any differently versioned or repackaged
artifact must receive its own corpus card before its statistics are used.

Before analysis, every source receives a
corpus card recording:

- provenance and collection protocol;
- participant, household, and rule counts, keeping these units distinct;
- whether rules were authored, copied, suggested, active, dormant, abandoned,
  or observed only in a study;
- available context, natural-language descriptions, logs, and participant
  metadata;
- access status, license, privacy restrictions, and publication-safe fields;
- demographic, platform, device, expertise, geographic, and survivorship bias.

Households, not rule rows, are the primary support unit. A power user with 500
rules must not contribute 500 independent votes. Exact duplicates, vendor
templates, test rules, and copied recipes are flagged rather than silently
treated as independent needs.

## Evidence ledger

Each source span or automation becomes an auditable evidence unit. The ledger
stores:

```text
evidence_id
source_id and source_type
source span or rule identifier
household/participant identifier (privacy-preserving)
collection context and provenance
authorship/status (authored, copied, suggested, active, abandoned, unknown)
actor and beneficiary
desired human/household state
activation context
failure or harm to avoid
hard constraints and soft preferences
time horizon and release condition
acceptable trade-offs
means/device/action fields
explicitness (explicit, strongly implied, analyst-inferred)
relation to candidate responsibility
```

The relation field is one of `entails`, `strongly_implies`,
`compatible_only`, `contradicts`, or `irrelevant`. This prevents a plausible
analyst interpretation from being reported as a user-stated preference.

The ledger also separates three layers that must never be conflated:

- `user_evidenced_semantics`: what users actually express or support;
- `benchmark_operationalization`: the observable Query and evaluator clauses
  chosen to represent that meaning;
- `backend_parameters`: simulator-specific horizon, thresholds, capacities,
  weather, and device dynamics.

## Induction protocol

### 1. Maximum-variation discovery sample

Sample across household composition, care relationships, automation expertise,
device families, and source types. Open-code a discovery subset before defining
the final responsibility inventory. Keep a separate household-held-out sample
for validation.

### 2. Blinded dual semantic coding

At least two annotators, blinded to backend capability and window availability,
independently code the desired outcome, beneficiary, context, failure, temporal
extent, constraints, trade-offs, and L0-L4 abstraction level. They maintain
analytic memos and explicit negative cases.

The primary method is team-based codebook thematic analysis / Framework Method
with constant comparison. It should not be described as fully automated topic
discovery or as grounded theory unless the corresponding theoretical-sampling
requirements are actually met.

### 3. Means-end abstraction

For each action-oriented unit, annotators ask:

1. Why is this action performed?
2. Who benefits and what goes wrong if it is not performed?
3. Would the purpose survive a change of device or implementation?

This step turns rules into responsibility hypotheses without erasing the link
to their original mechanisms.

### 4. Intent-only representation

Computational grouping uses only:

```text
desired state + beneficiary + avoided failure + context/horizon
+ constraints/trade-offs + responsible actor
```

Brand, platform, device name, and concrete action are removed or separately
typed. The mechanism view remains in the ledger for later leakage audits.

### 5. Constrained candidate grouping

Embeddings, hierarchical clustering, or card-sorting tools retrieve candidate
neighbors; they do not determine the final ontology.

- Must-link candidates have equivalent beneficiaries, success/failure meaning,
  and responsibility ownership despite different mechanisms.
- Cannot-link candidates use similar devices/actions but have different
  beneficiaries, harms, values, or accountability relationships.
- Multi-label membership is allowed when one automation genuinely serves more
  than one responsibility.

Two candidates are merged only when their success/failure criteria are
interchangeable, their beneficiary and accountability structure agree, and no
stable negative cases require a split. They are split when the same mechanism
serves different purposes, when beneficiaries differ, or when safety, privacy,
autonomy, care, comfort, or energy trade-offs change the obligation materially.

### 6. Human adjudication and triangulation

Annotators review each candidate cluster using a
`household x responsibility x source` matrix. Each source relationship is
recorded as convergence, complementarity, dissonance, or silence. Merge, split,
parent-child, exclusion, and scope decisions require a written memo and linked
evidence.

Qualitative evidence supplies semantic validity; naturally authored routines
supply ecological and prevalence support. Rule-only clusters remain provisional
hypotheses until their purpose is validated.

### 7. Validation and saturation

Freeze the codebook, then independently code a household-held-out, stratified
sample. Report pre-adjudication reliability rather than consensus-only values.

- nominal fields: Krippendorff's alpha;
- ordinal fields such as evidence strength and abstraction level: ordinal alpha;
- multi-label responsibilities: per-label agreement plus Jaccard or multilabel
  F1;
- cluster structure: human pairwise purity and bootstrap stability;
- generalization: leave-household-out coverage and `other/unknown` rate;
- device invariance: leave-device-family-out assignment and device-replacement
  consistency;
- construct evidence: blinded entailment precision, contradiction rate, and
  participant/member correction rate where available.

The held-out set also contains negative constructs: one-shot commands, vague
wishes, contradictory household preferences, explicitly manual-only actions,
unauthorized or impossible requests, and insufficient-evidence cases. Correct
`unknown`, `ask`, or `no-op` decisions are scored positively; the induction
system is not rewarded for forcing every item into a standing responsibility.

Code saturation and meaning saturation are reported separately. A practical
stopping rule is several consecutive new, maximally different households with
no new L2-L3 responsibility and no new beneficiary, failure mode,
accountability relation, or boundary condition, while unresolved negative cases
continue to trigger targeted sampling.

### 8. Responsibility admission gate

A responsibility is admitted only when:

- multiple independent households provide `entails` or `strongly_implies`
  semantic support for the full construct, not merely compatible actions;
- at least two evidence modalities converge, including one source that exposes
  user purpose and one behaviorally grounded source;
- contradictory evidence and population/context boundaries are documented;
- annotators can distinguish it reliably from neighboring responsibilities;
- it passes the device-replacement test;
- a Query and Contract can be shown to be entailed by the frozen semantics.

Low-frequency, high-risk, or marginalized-user responsibilities are not simply
discarded; they enter a separately labeled critical-case stratum. Exact numeric
support thresholds should be preregistered after corpus auditing and accompanied
by sensitivity analysis rather than chosen to maximize the final inventory.
`compatible_only` Routine counts can describe mechanism occurrence within a
platform, but cannot satisfy the semantic admission threshold or establish the
prevalence of a responsibility.

## Why raw-rule clustering is insufficient

Directly embedding and clustering raw Trigger-Action text will usually recover
device/action families instead of human responsibilities:

- device names and action verbs dominate short rule text;
- the same rule can serve safety, convenience, energy, care, or deterrence;
- one responsibility can be implemented by unrelated devices;
- rules omit beneficiaries, harms, trade-offs, and accountability;
- templates, copied rules, and platform phrasing create spurious similarity;
- responsibilities are hierarchical, multi-label, and sometimes conflicting;
- geometric cluster quality does not establish human construct validity.

Therefore clustering is useful only after semantic abstraction, and only as a
candidate-retrieval tool followed by evidence-aware human adjudication.

## Example

Suppose the corpora contain:

- `away -> thermostat off`;
- `17:00 -> cool to 23 C`;
- `geofence: approaching home -> cooling on`;
- an interview statement: "I do not want to cool an empty house, but I want it
  comfortable when the children get home.";
- a diary entry reporting manual overrides on unusually hot days.

Raw clustering may split these into thermostat, schedule, and geofence groups.
Means-end coding instead links them to a candidate responsibility:

> Maintain a comfortable occupied home and prepare it for expected return,
> while avoiding unnecessary conditioning during absence.

The rule rows establish recurring mechanisms and contexts; the interview
establishes beneficiary, purpose, and trade-off; the diary exposes a boundary
condition. The physical backend is consulted only after this statement is
frozen, to find diverse absence-return and thermal-dynamics windows.

## Pilot plan

1. Audit access, licensing, unit counts, and fields for the candidate corpora.
2. Select a maximum-variation discovery sample from user-needs studies and
   naturally authored routines; keep households disjoint across discovery and
   validation.
3. Build the evidence ledger and dual-code an initial batch.
4. Produce an intent-only representation and candidate hierarchical grouping;
   compare it with raw-rule clustering to quantify device leakage.
5. Adjudicate a small responsibility inventory and run support, negative-case,
   device-replacement, and held-out validation.
6. Freeze the accepted responsibilities, Queries, and Contracts.
7. Only then compile physical-opportunity predicates and scan CityLearn,
   EV2Gym, and later backends.

The pilot should report the number of independent households, evidence units,
source types, confirmed/provisional/critical-case responsibilities, agreement,
held-out coverage, unknown rate, device leakage, and every merge/split decision.

## Immutable bridge to executable Episodes

Every accepted construct is exported as a versioned, content-addressed chain:

```text
EvidenceLedger@hash
  -> CanonicalResponsibility@version/hash
  -> Query@version/hash
  -> Contract@version/hash
  -> OpportunityPredicate@version/hash
  -> Episode@id
```

The canonical responsibility stores ownership, beneficiary, recurrence,
failure, release/override, scope, and its supporting/contradicting evidence IDs.
Every Query statement and non-universal Contract clause records either the
evidence that entails it or an explicit `benchmark_design_choice` label.
Entailment is independently reviewed before freezing. The opportunity predicate
is then preregistered per frozen responsibility and includes positive,
weak/boundary, and no-op windows. It must distinguish physical solvability from
information feasibility and state which forecasts or observations the Agent can
actually access.

Backend processes cannot choose or rename the responsibility. The current
`family_for(process)` pattern in the battery/PV prototype must therefore be
deleted or relabeled as researcher-defined scenario stratification. Mining must
run from each frozen predicate, and replay sensitivity must affect the final
responsibility evaluator rather than merely any physical state.

## Planned methods language

> and naturally authored automation corpora. Two annotators, blinded to backend
> availability, code each evidence unit into a device-independent means-end
> frame containing the desired state, beneficiary, context, failure consequence,
> temporal extent, and constraints. Computational clustering is applied only to
> these intent frames to retrieve candidate groupings; researchers then merge or
> split candidates through constant comparison, negative-case analysis, and
> cross-source triangulation. We retain only responsibilities with auditable
> support across independent households, validate the frozen codebook on held-out
> households, and verify that Query and Contract semantics are entailed by the
> evidence. Only after this freeze do we search physical backends for diverse,
> action-sensitive processes and compile executable Responsibility Episodes.
> We will first assemble an evidence ledger from qualitative smart-home user
> studies and naturally authored automation corpora. Two annotators, blinded to
> backend availability, will code each evidence unit into a device-independent
> means-end frame containing the desired state, beneficiary, ownership, context,
> failure consequence, temporal persistence, release condition, and constraints.
> Computational clustering will be applied only to these intent frames to
> retrieve candidate groupings; researchers will then merge or split candidates
> through constant comparison, negative-case analysis, and cross-source
> triangulation. Only responsibilities with auditable semantic support across
> independent households and held-out construct validation will be frozen.
> Query and Contract semantics will then be checked for evidence entailment
> before preregistered, responsibility-specific predicates search physical
> backends for diverse, action-sensitive and no-op processes.
> and naturally authored automation corpora. Two annotators, blinded to backend
> availability, code each evidence unit into a device-independent means-end
> frame containing the desired state, beneficiary, context, failure consequence,
> temporal extent, and constraints. Computational clustering is applied only to
> these intent frames to retrieve candidate groupings; researchers then merge or
> split candidates through constant comparison, negative-case analysis, and
> cross-source triangulation. We retain only responsibilities with auditable
> support across independent households, validate the frozen codebook on held-out
> households, and verify that Query and Contract semantics are entailed by the
> evidence. Only after this freeze do we search physical backends for diverse,
> action-sensitive processes and compile executable Responsibility Episodes.

## Method references

- User Needs of Smart Home Services:
  https://doi.org/10.1177/1071181321651218
- TAREME field deployment:
  https://doi.org/10.1007/s12652-021-03239-0
- FedRule / reported Wyze and IFTTT corpus statistics:
  https://arxiv.org/abs/2211.06812
- SmartSense paper and public repository:
  https://arxiv.org/abs/2208.06089 and
  https://github.com/snudatalab/SmartSense
- Practical Trigger-Action Programming in the Smart Home:
  https://doi.org/10.1145/2556288.2557420
- Brush et al. (2011), Home Automation in the Wild:
  https://doi.org/10.1145/1978942.1979249
- Yang and Newman (2013), Learning from a Learning Thermostat:
  https://doi.org/10.1145/2493432.2493489
- Davies et al., 20-month smart-home auto-ethnography:
  https://doi.org/10.1007/s00779-023-01725-0
- Glaser (1965), constant comparative method:
  https://doi.org/10.1525/sp.1965.12.4.03a00070
- Braun and Clarke (2006), thematic analysis:
  https://doi.org/10.1191/1478088706qp063oa
- Gioia, Corley, and Hamilton (2013), inductive qualitative rigor:
  https://doi.org/10.1177/1094428112452151
- Gale et al. (2013), Framework Method:
  https://doi.org/10.1186/1471-2288-13-117
- Nickerson, Varshney, and Muntermann (2013), taxonomy development:
  https://doi.org/10.1057/ejis.2012.26
- Hennink, Kaiser, and Marconi (2017), code versus meaning saturation:
  https://doi.org/10.1177/1049732316665344
- O'Connor and Joffe (2020), intercoder reliability:
  https://doi.org/10.1177/1609406919899220
- Farmer et al. (2006), triangulation protocol:
  https://doi.org/10.1177/1049732305285708
- Birt et al. (2016), member checking:
  https://doi.org/10.1177/1049732316654870
- van Lamsweerde (2001), goal-oriented requirements engineering:
  https://doi.org/10.1109/ISRE.2001.948567
- Ur et al. (2016), Trigger-Action Programming in the Wild:
  https://doi.org/10.1145/2858036.2858556
- Chang et al. (2009), human validity of topic models:
  https://proceedings.neurips.cc/paper/2009/hash/f92586a25bb3145facd64ab20fd554ff-Abstract.html
