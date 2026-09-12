# Query scale campaign V1

## Outcome

This development campaign froze two disjoint, topic-stratified batches before
generation. It selected 100 public development records (30 CrowdRE and 70 HIIS),
produced 78 structurally validated extractions and 77 candidate queries. One
ambiguous CrowdRE record was correctly rejected at extraction. None of these 100
records is admitted as a benchmark item: semantic review and backend evidence are
separate gates.

The requested API model was `deepseek-v4-flash`; provider receipts reported the
resolved model as `deepseek-flash`. Across all campaign activities there were 268
calls with usage receipts: 259,329 prompt tokens, 713,258 completion tokens, and
972,587 total tokens. One pre-provider sandbox `URLError` has no usage receipt and
is reported as unknown rather than zero. No further provider calls were made after
the known remaining budget reached 27,413 tokens.

## Source batches

| Fact | Batch 1 | Batch 2 | Combined |
|---|---:|---:|---:|
| Frozen source records | 50 | 50 | 100 |
| CrowdRE / HIIS | 15 / 35 | 15 / 35 | 30 / 70 |
| Structurally completed extractions | 42 | 36 | 78 |
| Candidate / source reject | 42 / 0 | 35 / 1 | 77 / 1 |
| Model-output truncations | 8 after one configured retry | 14, no retry | 22 |

The second inventory excludes every first-batch ID. Both use only IDs already in
the source inventories' development partitions. CrowdRE holdout records were not
selected or sent to the provider. HIIS remains development-only because of prior
exposure and unresolved redistribution status.

The first batch sent 42 candidates to same-model semantic review: 19 completed
with `clear=true` and 23 truncated. The 19 clear candidates were screened against
all 15 formal capability cards: only three complete route outputs were obtained,
and each found all routes unsupported; 16 outputs truncated. The second batch sent
35 candidates to a lower-budget semantic review: eight completed clear and 27
truncated. These clear flags are corroborating signals, not independent validation.

The joined source-record states are:

- 22 generator failures caused by reported output-length termination;
- 1 evidence-level rejection for an ambiguous desired direction;
- 50 candidates pending completed semantic review;
- 16 candidates pending completed route screening;
- 3 rejected by the current formal route catalogue screen;
- 8 review-clear candidates not yet routed.

This is evidence that hundreds of raw candidates are mechanically feasible, but
not evidence that hundreds of accepted benchmark tasks can be obtained from these
two corpora. In particular, generating more unconstrained human rules recreates the
known low-overlap problem with the current backends.

## Complementary evidence-grounded variants

Ten previously audited V2 responsibility anchors generated three surface variants
each. The resulting artifact contains 30 queries in ten semantic/evidence clusters:
six variants of accepted-core anchors, three of an accepted calibration anchor,
six inheriting pending status, and fifteen inheriting rejection. All were compared
against their anchors by Codex; this is AI review, not human annotation. Variants
never upgrade their parent's decision and contribute zero independent demand
observations. They are suitable for wording-robustness tests, not prevalence or
task-diversity counts.

## Exact model/backend evidence

The previously accepted V2 multi-room thermal responsibility was run with the exact
query and public evaluation conditions against the native Modelica route. A first
4,000-output-token run stopped at t=2100 after its fifth model response truncated;
it is retained as an infrastructure-output-budget failure. The single predeclared
configuration retry used an 8,000-token ceiling and completed:

- 7 model decisions and 60 native one-minute transitions through t=3600;
- full lossless state reconstruction after autonomous waits;
- four temperature clauses, each with 60 opportunities and zero violations,
  unknowns or pending obligations;
- room A range 18.497–21.601°C and room B range 18.127–22.066°C;
- trajectory evaluation `task_success=true`.

This result belongs only to `core-transfer-multiroom-comfort-01`. It does not admit
or validate any of the 100 new source records.

## Recommendation

Use `deepseek-v4-flash` as a first-layer extractor with source-aware output budgets.
HIIS short rules completed much more reliably than long CrowdRE narratives. Do not
use the same model/prompt for exhaustive 15-route comparison: deterministic
capability prefiltering followed by targeted real-snapshot mapping is both cheaper
and methodologically stronger. For accepted-task growth, prioritize evidence-led,
backend-compatible need discovery and transparent synthesis, then require native
feasible/failing policy contrasts. Do not scale by paraphrase count or by repeatedly
retrying long unsupported records.

## Reproduction artifacts

- `generated/query_construction_v3/query_scale_campaign_v1.json`: joined 100-record report and global usage ledger.
- `generated/query_construction_v3/batch50_freeze_manifest_v1.json` and `batch50_freeze_manifest_v2.json`: frozen selections and strata.
- `generated/query_construction_v3/evidence_query_surface_variants_v1.json`: clustered 30-query surface set.
- `generated/query_construction_v3/core_transfer_deepseek_v4_flash_run8000_v1/`: successful model/native events and result.
- `tools/select_query_scale_batch.py`, `run_query_extraction.py`, `review_query_extraction.py`: source selection and LLM stages.
- `tools/generate_evidence_query_variants.py`, `report_evidence_query_variants.py`: evidence-clustered surface augmentation.
- `tools/run_evidence_query_agent_packet.py`: frozen exact-query native model execution.
- `tools/report_query_scale_campaign.py`: final deterministic join.

Run `pytest -q tests/test_query_*.py`; the current result is 142 passing tests.
