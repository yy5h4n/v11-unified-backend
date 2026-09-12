# Query construction: executable development pipeline

## Evidence-grounded V2 pilot

The current recommended entry point is the evidence-layered V2 pilot described
in `docs/QUERY_CONSTRUCTION_V2_DELIVERY.md`. It accepts direct human statements,
explicitly labelled product/domain inference, and auditable evidence-grounded
synthesis without representing them as equivalent evidence classes. Run:

```sh
python tools/audit_evidence_query_batch.py --batch generated/query_construction_v2/development_batch_v1.json
python tools/audit_evidence_query_batch.py --batch generated/query_construction_v2/validation_batch_v1.json
python tools/audit_evidence_query_batch.py --batch generated/query_construction_v2/core_transfer_batch_v1.json
python tools/summarize_evidence_query_release.py \
  --batch generated/query_construction_v2/development_batch_v1.json \
  --batch generated/query_construction_v2/validation_batch_v1.json \
  --batch generated/query_construction_v2/core_transfer_batch_v1.json
python tools/freeze_evidence_query_release.py \
  --config generated/query_construction_v2/release_config_v1.json \
  --output generated/query_construction_v2/release_manifest_v1.json \
  --check
python tools/prepare_evidence_query_agent_packet.py \
  --batch generated/query_construction_v2/core_transfer_batch_v1.json \
  --item-id core-transfer-multiroom-comfort-01 \
  --diagnostic generated/query_construction_v2/core_transfer_thermal_native_v1.json \
  --output /path/to/new/agent_packet.json
```

This provenance/admission layer feeds the existing extraction, capability,
mapping, contract, native execution, and trajectory evaluator components below.
It does not replace them and does not infer model success from data validity.

Native evidence reporting: `tools/report_query_pipeline.py` now accepts repeated
`--native path/to/result.json` and `--diagnostic path/to/policies.json` alongside
the existing mapping arguments. It matches the exact mapping/query/contract,
recomputes native and diagnostic scores, rejects mismatched evidence, and labels
an idle/always-on diagnostic pass as calibration-only. Missing baseline evidence
is claim-validation-pending, never automatic admission. See
`generated/query_construction_v1/crowdre_arrival_pipeline_report_v1.json` for the
first real joined report. Policy labels are diagnostic metadata, not certified
policy implementations; this does not replace semantic or mechanism validation.

Current user-approved scope: deliver a usable pipeline on compatible backends;
coverage of all 15 routes is **not required**. Keep frequent human-demand topics
and missing capabilities in a backend opportunity backlog, without inventing
queries to fill backend coverage. Successful end-to-end examples and a held-out
batch remain required; all-rejection output is not completion.

Status: validated pilot, **not a released benchmark**. Three core items and one
calibration item have passed the V2 objective gates; only one core and one calibration
come from sources frozen after rule development. No exact-query model execution was
run in this pilot, so it reports no model success rate. Existing backend acceptance
alone remains insufficient evidence that a constructed task is valid.

## Data model

Keep three independent layers:

1. Human source and atomic responsibility: exact source text, collection setting,
   source locator and explicit derivation. LLM paraphrases do not invent user needs.
2. Backend scenario: real observations/actions, episode seed/configuration and
   demonstrated dynamics. A D3 route label does not establish actual competition.
3. Public evaluation conditions: quantities, units, entities, deadlines and their
   origins. The model receives the same conditions that compile into scoring clauses.

Atomic decomposition is allowed through `project_query_atom.py`; preserve the
parent, selected and unselected atoms and shared conditions. Do not claim the
result fulfils the complete parent requirement.

## Current source inventory

CrowdRE DOI `10.5281/zenodo.3550721`, CC BY 4.0: 1,823 parsed records,
1,811 retained and 12 quarantined. Use `crowdre_v2/inventory.json`, **not v1**:
v2 keeps context, stimuli and response in source text. The split is unchanged:
1,653 development / 158 held-out, grouped by participant, team and normalized
response duplicates. This conservative grouping produces a large component;
do not split it to force an exact holdout fraction. Original labels are serialization
markers, not human-authored words. Raw demographic/personality tables are excluded.

These are elicited crowd requirements, not verified household deployments.
The first five and the fixed random 20 development records are heavily lighting
focused. They do not establish energy/water/ventilation source coverage. Held-out
text has not been shown to the model or used to revise extraction rules.

HIIS remains development-only with unresolved redistribution permission. Big
House is metadata-only until the dataset attachment and its license are obtained.

## Run from repository root

Use the repository Python environment for construction. Native execution switches
to the route's configured interpreter. Set `AIGC_API_KEY` externally; never include
it in files or reports. All output paths must be new; tools do not overwrite evidence.

```sh
python -m query_construction.import_crowdre --archive /path/CrowdRE-Data.zip --output /path/new/inventory.json
python tools/run_query_extraction.py --inventory /path/new/inventory.json --output /path/new/extraction.jsonl --limit 20 --sample-seed 20260910 --execute
python tools/review_query_extraction.py --inventory /path/new/inventory.json --journal /path/new/extraction.jsonl --output /path/new/review.jsonl --limit 20
python tools/screen_query_routes.py --inventory /path/new/inventory.json --journal /path/new/extraction.jsonl --output /path/new/routes.jsonl --limit 20
```

Omit `--execute` for extraction packet preparation without external calls. Review,
routing, mapping and execution commands make external calls. Screening only
retrieves possible routes; it has produced false capability matches in practice.
Inspect real-interface mapping and independently validate meaning before admission.

For an explicit candidate source ID and route, obtain a native snapshot and map:

```sh
python tools/snapshot_query_backend.py --help
python tools/run_query_mapping.py --journal /path/new/extraction.jsonl --source-id SOURCE_ID --snapshot /path/route_snapshot.json --output /path/new/mapping.jsonl
python tools/run_constructed_query.py --mapping /path/new/mapping.jsonl --snapshot /path/route_snapshot.json --example-action 'LEGAL_JSON_ACTION' --output-dir /path/new/native_run --max-calls 30
```

The example action is syntax guidance, not a submitted reference policy. Native
execution retains autonomous wait and every native microstep. It checks current
contract compilation and initial-state identity; known incompatible reactive
deadlines stop before a model call. Unknown settling times and simultaneous
resource feasibility remain unresolved, not silently accepted.

## Reports and failure ownership

```sh
python tools/summarize_query_batch.py --journal /path/new/extraction.jsonl --output /path/new/extraction_summary.json
python tools/report_query_pipeline.py --inventory /path/new/inventory.json --extraction /path/new/extraction.jsonl --review /path/new/review.jsonl --routing /path/new/routes.jsonl --mapping /path/new/mapping.jsonl --output /path/new/pipeline_report.json
python -m pytest tests/test_query_*.py -q
```

Report every attempted source, including rejected and incomplete attempts.
Provider token usage is distinct from agent development usage; missing receipts
mean unknown cost, never zero. Do not sum heterogeneous physical resource units.

- Data/construction: changed source meaning, unsupported entity/condition,
  infeasible deadline, unexercised mechanism or mismatched scoring contract.
- Infrastructure: invalid prompt/schema, missing interface information,
  parser/transport/budget/runtime failure.
- Model task failure: only after data, environment and evaluation validity are
  established; extraction or routing errors are not task-agent benchmark failures.

Independent model review has false negatives. `model_review_clear`,
`structurally_valid`, `compiled` and `native_terminal_reached` are different facts;
none alone constitutes admission. Reports currently leave admission false.

## Scale campaign V1

The reproducible 100-source development campaign is summarized in
`docs/QUERY_SCALE_CAMPAIGN_V1.md` and
`generated/query_construction_v3/query_scale_campaign_v1.json`. Its scripts accept
an explicit provider model; the recorded run requested `deepseek-v4-flash`. Freeze
a non-overlapping 50-record source batch with:

```sh
python tools/select_query_scale_batch.py --crowdre generated/query_construction_v1/crowdre_v2/inventory.json --hiis generated/query_construction_v1/hiis_development_v2/source_inventory.json --batch-id NEW_ID --seed FROZEN_SEED --inventory-output /path/new/inventory.json --manifest-output /path/new/manifest.json
python tools/run_query_extraction.py --inventory /path/new/inventory.json --output /path/new/extraction.jsonl --limit 50 --model deepseek-v4-flash --max-tokens 4000 --execute
```

Use `--exclude-inventory` when freezing later batches. Treat output-length
termination as generator failure, not source rejection. Whole-catalogue routing was
unreliable and expensive for this model; prefilter with the checked-in capability
cards, then map only plausible candidates against a real native snapshot.

## Remaining release requirements

Obtain adequate licensed human sources for the supported task scope; resolve semantic ambiguity without
silently strengthening or weakening requests; validate native feasible and failing
counterpolicies; prove relevant mechanisms in each exact episode; complete dynamic
entity/departure contracts; freeze rules and execute the untouched source batch.
The current CLI components do not yet automate these full admission steps.
