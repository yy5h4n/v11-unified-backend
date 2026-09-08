# Round 11 whole-batch review

**REJECT — only the existing B2 capability/evidence closure remains.** Honest CityLearn Episode/coupling separation is accepted as the intended solution; no native cross-coupling implementation or new physics model is required. B1/B3/B4, EV conditional shared-constraint evidence and the unaffected 600-second stability measurement remain accepted. FDS real replay remains accepted.

## What is now correct

The current registry declares CityLearn `episode_generation_supported=true` and `d3_coupling_supported=false`. Its raw causal record declares coupling unsupported, and the assembled route checks correctly contain `episode_ready=true`, `mechanism_gate=false`, `coupling_supported=false`. The report's leading status and final Round11 paragraph disclose the limitation. These accurately distinguish useful multi-system Episode generation from an unproven D3 coupling classification.

The original Round11 package checker returns 0. The independently repeated empty-raw-runs mutation, with artifact hashes refreshed, now returns 1. Package/CHANGED_FILES hashes match; original baseline hashes are unchanged. Existing stability evidence has the unchanged previously verified stream hash. No original or implementation file was modified and no unrelated long campaign was rerun.

## Remaining B2 capability inconsistency

The active `generated/dynamic_mechanism_catalog_v1.json` still exports `strong_coupling.routes.citylearn_multi_system.verified=true` (around line 4952), with only battery SOC, HVAC temperature and net-electricity direct counterfactual deltas. The builder still produces that claim in `build_dynamic_mechanism_catalog.py`'s CityLearn D3 record. It is not merely a frozen historical document: it is the generated mechanism catalog and its active regeneration path. Therefore a downstream consumer can still select/publish this route as a verified D3 coupling sample despite the new registry limitation.

The current route artifact's `metadata` and `campaign.metadata` also omit both new capability fields, although its `checks` and the registry contain the intended distinction. The public-protocol document explains FDS's capability boundary but contains no equivalent CityLearn restriction. Public construction rejects unknown `require_coupling` arguments generically; it has no explicit documented coupling-required selection contract. Do not claim this is an accepted coupling-required route merely because its historical alias contains “coupling.”

**Required closure:** use the authoritative capability distinction throughout the active generated catalog and its builder, current route metadata, and public documentation/selection contract. Retain Episode availability. Mark this route's coupling verification false/unsupported, and ensure downstream verified-D3 selection excludes it. Historical frozen evidence can remain unchanged when clearly identified as historical; the active catalog must not repeat the obsolete verified claim. No renaming of historical IDs or fabrication of native coupling is requested.

## Remaining B2 same-prefix proof validation

The checker now requires two runs, non-null effects and prefix fields, fixed-peer action equality and varied-action inequality. However, it still trusts the producer's `same_prefix=true` bit and **does not compare the two raw prefix observations**.

Independent negative test: retain both legal actions/effects, replace the second run's prefix observation with `{"deliberately_different_prefix": true}` in the new probes, keep summary bits and refresh all artifact hashes. **Checker returns 0.** This directly contradicts the report's claim that matching prefix observations are required and the previous B2 request for raw same-prefix validation. Reproduction: `astra_round11_evidence/prefix_negative.py`; result: `prefix_negative.json`.

**Required closure:** compare paired raw prefixes and timestamps (and recorded seed/prefix identity where supplied), require the two intended opposite channel interventions, and derive the accepted route-specific proof from the records. Keep the now-passing empty-runs negative and add this unequal-prefix negative. The original legitimate package should continue to pass; the two incomplete/invalid proof variants must fail. Do not reinstate a true CityLearn coupling gate merely because its direct effects differ.

## Next complete delivery

Finish these two B2 consistency checks in one batch; refresh the active catalog and final metadata/package, run the targeted positive and negative checks, and return one frozen delivery. Preserve trustworthy unrelated native timing, horizon, cleanup and stability evidence with explicit provenance. This is a finite closure list, not a request for another broad repair campaign.

The usable intended scope remains all 15 local Episode-generation routes, with CityLearn multi-system excluded from verified D3 coupling and FDS executed as real replay. This scoped delivery can be accepted once its active machine artifacts and validator agree with those claims.
