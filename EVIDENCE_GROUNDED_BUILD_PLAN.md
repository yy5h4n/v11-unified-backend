# Evidence-Grounded Release Preview — End-to-End Build Contract

## Objective

Run one reproducible construction path from the existing 24-unit seed evidence
pool to a QA-checked executable Episode preview. This preview is an engineering
release, not a claim that the responsibility ontology has been human validated.
It reuses the existing V11 compiler, adapters, physical process inventory, and
backend assets; it does not create another simulator environment.

## Frozen stage order

```text
seed evidence + corpus cards + codebook v3
  -> independent candidate induction (Luna, Kimi K3)
  -> evidence-bundle adjudication
  -> CanonicalResponsibility
  -> Query
  -> Contract
  -> responsibility-specific OpportunityPredicate
  -> existing V11 adapter scan / canonical process pool
  -> positive + boundary + no-op windows
  -> executable Episode preview
  -> replay / evaluator / lineage / dedup / split QA
  -> unified HTML dashboard
```

## Separate status axes

Every responsibility and Episode must expose both:

- `semantic_status`: initially `provisional_ai_pilot`; it may become
  `human_validated` only after formal blinded human annotation and held-out
  validation.
- `physical_status`: one of `unbound`, `data_probed`, `replay_verified`, or
  `unsupported_by_installed_backend`.

Physical executability must never upgrade semantic evidence, and semantic
support must never imply backend capability.

## Immutable lineage

```text
EvidenceBundle@hash
  -> CanonicalResponsibility@version/hash
  -> Query@version/hash
  -> Contract@version/hash
  -> OpportunityPredicate@version/hash
  -> PhysicalProcess@source_hash
  -> Episode@id
```

Every non-universal Query or Contract clause must reference evidence IDs or be
explicitly labeled `benchmark_design_choice`. Backend windows cannot assign,
rename, or weight responsibility families.

## Construction rules

1. Use the 24 existing evidence units exactly as the seed corpus; never rewrite
   source text during induction.
2. Luna and Kimi K3 independently group evidence using desired state,
   beneficiary, context, temporal scope, ownership, failure, and release. Device
   names/actions remain mechanism metadata.
3. A group can enter the preview as a provisional responsibility only when its
   evidence bundle is auditable. Missing human-validation gates remain explicit.
4. A backend scan begins from a frozen responsibility-specific predicate. The
   current battery/PV `family_for(process)` behavior cannot be used.
5. Include positive, weak/boundary, and no-op physical windows. Agent or solver
   performance is never an Episode membership criterion.
6. Require evaluator-relevant action sensitivity, legal actions, deterministic
   termination, and public-observation information feasibility.
7. Do not release gold actions. Private feasibility witnesses are QA artifacts
   only.
8. Keep legacy V10/V11 artifacts unchanged. New preview artifacts live under
   `generated/evidence_grounded_preview/`.

## Minimum end-to-end acceptance for today's preview

- one deterministic build entry point;
- two independent induction outputs plus an adjudicated responsibility catalog;
- versioned Query, Contract, and OpportunityPredicate artifacts with hashes;
- at least one responsibility bound to existing executable physics;
- at least one positive and one boundary/no-op Episode for each bound
  responsibility where the source pool permits it;
- zero backend-derived responsibility labels;
- complete public/private lineage and no gold actions in public records;
- deterministic QA report and dashboard summary;
- tests covering semantic/physical status separation, hash stability, and
  failure-closed behavior.

