# Responsibility Definition Review

## Scope

Independent conceptual review of the refined construction story:

1. derive a standing household responsibility from user evidence;
2. freeze its Query, observation/action protocol, and trajectory Contract;
3. compile it into a physical-opportunity predicate;
4. mine, diversify, replay-gate, and bind backend windows as Episodes.

This review concerns the refined methodology, not the earlier V11 implementation.

## Verdict

**Major revision, then potentially acceptable.** The pipeline avoids
performance-based circularity, but responsibility provenance and construct
validation must become auditable procedures.

## Required protocol

- Maintain a versioned evidence ledger linking every responsibility to source
  spans, source type, context, household/participant identity, and independent
  support counts.
- Require support from multiple independent households and preferably at least
  two evidence types (e.g. qualitative user research plus an automation/routine
  corpus). Automation corpora alone overrepresent already-expressible routines.
- Use at least two annotators, blinded to backend-window availability, to label
  actor, recurring goal, context, constraints, horizon, priorities, and
  acceptable trade-offs; report agreement and adjudication.
- Separate `user_evidenced_semantics`, `benchmark_operationalization`, and
  `backend_parameters`. A 24-hour horizon or 20% SOC threshold must not be
  presented as a user preference unless the evidence supports it.
- Freeze the evidence ledger, canonical responsibility, Query semantics, and
  Contract before scanning backend trajectories.
- Independently verify that the Query and Contract are entailed by the evidence;
  LLMs may paraphrase only after semantics are frozen and equivalence-checked.

## Construction-validity gates

- A private action sequence proves physical solvability, not information
  feasibility. Also verify that a policy restricted to public observations can
  reasonably act, or expose the necessary forecasts.
- Pre-register the physical-opportunity predicate and include boundary/negative
  windows where waiting is correct, rather than selecting only action-favorable
  windows.
- Require action sensitivity to change evaluator-relevant outcomes materially,
  not merely any backend state.
- Report modeled physical provenance accurately: current ResStock/PVWatts
  trajectories are source-grounded and executable, not measured household data.
- Audit near-duplicate modeled configurations and weather seeds across splits in
  addition to household identifiers.

## Safe methodological claim after revision

Responsibilities are human-evidence-derived and researcher-formalized under an
auditable annotation protocol; backends provide executable physical variation
but do not invent responsibilities. Episode membership is determined by a
pre-registered responsibility-relevance predicate and physical/construct QA,
never by baseline Agent performance.
