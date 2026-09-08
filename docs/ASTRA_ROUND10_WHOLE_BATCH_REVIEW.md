# Round 10 independent whole-batch review

**REJECT. B3 is resolved; B2 remains for CityLearn's claimed cross-channel gate and the new cross-channel evidence validator.** EV's new evidence supports a native shared-constraint counterfactual, with the limitation explained below. This review applies to `generated/backend_acceptance_round10_final`. B1, B4 and the unaffected measured stability evidence retain their accepted status. FDS real replay remains formally accepted.

## Independent verification

Both new native causal probes were rerun in `astra_round10_evidence`, using Anaconda for CityLearn and the pinned v10 virtual environment for EV. Their entire `cross_channel` records exactly match the delivered records. No implementation or original-project file was edited. Package hashes, CHANGED_FILES hashes and original baseline checks all pass. The unmodified package checker exits 0 without changing the package.

The stability stream still has SHA256 `bd5ee98ef871cf2e48b6c55841e6e2794491edc35b9134b03b8df266aded8b18`: 2999 raw events, including 30 queue jobs and 2969 active runs, zero errors/seed mismatches, reconstructed active duration 600.226062 seconds. This is explicitly reused round8 evidence. Existing timing/cleanup implementations are unaffected; unnecessary long reruns were not performed.

## B3 — PASS

The checker now obtains the authoritative horizon and cadence from `route_metadata(route_id)` and compares both campaign metadata and every seed against it. Independent tests produced:

- Original package: exit 0.
- D0 seed 0 reduced to its genuine first 60-second tick, seed-local horizon reduced to 60, hashes refreshed: exit 1.
- Same mutation plus campaign/route metadata and top-level route horizon reduced to 60: exit 1.

The implementation checks contiguous cadence and the authoritative final endpoint for each seed. These tests close the existing incomplete-horizon defect; no new horizon requirements are added. Reproduction and results: `astra_round10_evidence/check_probe.py` and `check_probe.json`.

## B2 — EV evidence accepted as a conditional shared constraint

Seed 3 now supplies identical native prefixes at 23400 seconds with both ports connected. Both directions hold one port's request at 1 while varying the other between 0 and 1. At 24300 seconds, the fixed port remains 11 kW, the varied port changes from 0 to 11 kW, and the native transformer constraint changes from 11/15 kW feasible to 22/15 kW overloaded. Native `is_overloaded()` returns false versus true. The reverse intervention reproduces this.

This does **not** demonstrate native clipping or a change in the fixed port's realized power/SOC, and must not be described that way. It does establish the permitted conditional feasible-domain interpretation using the native capacity data and fixed-peer counterfactuals: the other port's 11 kW request is within the 15 kW shared bound when its peer draws 0, but exceeds the residual 4 kW bound when its peer draws 11. Both native ports are connected and the actual native constraint predicate was exercised. This resolves the previous disconnected-peer evidence problem. No requirement to invent power clipping is imposed.

## B2 — CityLearn and proof validation still OPEN

CityLearn's battery intervention with HVAC fixed changes only battery SOC/storage and net accounting; HVAC electricity and indoor temperature remain identical. The reverse intervention with battery fixed changes HVAC electricity/temperature and net accounting, while battery SOC/storage remain identical. Independent rerun confirms these exact results. Two direct channel responses are useful Episode evidence, but they are not a cross-channel effect.

The artifact explicitly says `native_cross_effect_observed=false`, yet sets `cross_channel.gate=true`, `mechanism_gate=true`, and the assembled package advertises all mechanism gates passed. The explanatory prose admitting no native clipping does not remove that machine-readable coupling claim. The gate is currently `all(effect_digest_changed and same_prefix)`, which is satisfied by independent channel effects. CityLearn also infers same-prefix from equal timestamps instead of retaining/comparing its reset/prefix record.

The checker then trusts these summary bits. An independent negative test removed the raw `runs` from every new cross-channel probe, retained the booleans and refreshed artifact hashes. **The checker still returned 0.** This is the same B2 proof requirement, not an additional test category: the new mechanism gate accepts missing mechanism evidence. Reproduction: `astra_round10_evidence/cross_negative.py`; result: `cross_negative.json`.

## One remaining whole-batch correction

1. Make CityLearn's capabilities and machine claims truthful. If native coupling exists in this configuration, demonstrate an actual fixed-peer cross-effect or native constraint counterfactual. If it does not, keep the route available for reliable multi-system Episode generation but explicitly mark cross-channel coupling unsupported/unproven, separate Episode readiness from D3 coupling readiness, and reject explicit coupling-required requests. Do not label two independent responses as a passed coupling gate. Preserve the frozen historical specification and record the narrower claim/conflict transparently; no fabricated native clipping is acceptable.
2. Validate B2 raw evidence structurally and semantically. Require two runs per declared intervention, the intended varied action and identical fixed-peer action, matching seed/prefix records, and route-specific native effects or the EV shared-constraint counterfactual. Derive pass status from these records, not producer-supplied booleans. Empty runs and a direct-only CityLearn record must not certify coupling. Keep legitimate Episode capability separate from unsupported coupling so a truthful limitation can be delivered rather than repeatedly pretending this native model changed.
3. Run the affected checks and native probes, refresh the final package once, and update the report's leading current-package/status text. Keep B3's two negative cases. Preserve trusted unrelated evidence with its original campaign provenance. Return one complete package for review.

Useful scope remains all 15 routes' local Episode execution, including FDS replay; acceptance of the current all-mechanisms-ready claim is withheld. This round does not reopen B1/B4, demand another soak, or claim CityLearn native coupling is impossible. It requires an honest capability boundary and an evidence gate that actually reads its evidence.
