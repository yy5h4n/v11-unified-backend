# V7 Context Migration Audit

Status: implementation and offline functional verification **PASS** (`30 passed`); live V4 Flash native-tool support remains **UNVERIFIED** until the read-only probe succeeds with `AIGC_API_KEY`.

## Scope and source baseline

V7 is additive: it does not modify v1-v6, release data, historical results, harness code, or configuration. It imports the exact v5 sample indices and Episode IDs (`evaluate_workflow_v4_flash_v7.py:32-37`), while v5 freezes release hashes, 210 public/private pairs, 21 responsibilities, and the selected 15 distinct responsibilities (`evaluate_workflow_v4_flash_v5.py:23-41`, `48-84`). Formal V7 startup invokes that v5 freeze gate before running (`evaluate_workflow_v4_flash_v7.py:712-729`).

The v6 transport used a text `<answer>` envelope and retained provider text. V7 replaces the transport with native function calls and retains only canonical actions plus a response fingerprint; it has no text-envelope fallback.

## Explicit migration manifest

The machine-readable migration checklist is defined at `evaluate_workflow_v4_flash_v7.py:47-67` and copied into every report at `evaluate_workflow_v4_flash_v7.py:614-649`.

| Public surface | V7 destination and evidence |
|---|---|
| Frozen release/sample | v5 constants and freeze assertion (`evaluate_workflow_v4_flash_v7.py:32-37`, `evaluate_workflow_v4_flash_v5.py:48-84`) |
| Household-agent responsibility and monitoring rules | static system directive (`evaluate_workflow_v4_flash_v7.py:69-80`, `196-198`) |
| Four public action kinds, represented by six native tools | strict `act`, three `wait` branches, `cancel_rule`, and `install_rule` schemas (`evaluate_workflow_v4_flash_v7.py:38-45`, `141-189`) |
| Complete public device interface | operation-specific command branches derived from the public inventory (`evaluate_workflow_v4_flash_v7.py:85-113`, `141-189`) |
| Query-scoped public event interface | deterministic query-only selection plus typed event-filter branches (`evaluate_workflow_v4_flash_v4.py:267-325`, `evaluate_workflow_v4_flash_v7.py:116-133`, `141-189`) |
| Original request, public preferences, obs0 | one initial user message (`evaluate_workflow_v4_flash_v7.py:201-207`) |
| Fresh state, events, active rules, metrics | complete `current_observation` in each paired tool result (`evaluate_workflow_v4_flash_v7.py:210-212`, `368-381`, `421-435`) |
| Action result / public receipts | `action_result` in the tool message and action records in the result (`evaluate_workflow_v4_flash_v7.py:210-212`, `461-490`) |
| Full action/observation history | canonical assistant call and matching tool message retained across requests (`evaluate_workflow_v4_flash_v7.py:215-225`, `336-435`) |
| Split token accounting | prompt, completion/output, exact provider total, presence, and mismatch counters (`evaluate_workflow_v4_flash_v7.py:275-294`, `341-343`, `393-400`, `482-485`, `494-512`) |
| Report/checkpoint invariants | frozen-prefix validation, canonical transcript validation, exact report rebuild, atomic replacement, and completed-report refusal (`evaluate_workflow_v4_flash_v7.py:515-610`, `614-673`) |

## Canonical conversation contract

The first request contains exactly `system, user`. The user payload contains only `original_query`, `public_preferences`, and `initial_observation` (`evaluate_workflow_v4_flash_v7.py:201-207`, `357-370`). Each successful native response is strict-parsed and rewritten as one canonical `assistant.tool_calls` message (`evaluate_workflow_v4_flash_v7.py:215-272`, `390-412`). Before the next decision, V7 appends one `tool` message whose `tool_call_id` matches that assistant call and whose payload contains the previous public result and fresh complete observation (`evaluate_workflow_v4_flash_v7.py:210-212`, `368-385`). The final action is paired during finalization (`evaluate_workflow_v4_flash_v7.py:421-435`). No duplicate `previous_action` or `previous_action_result` fields are sent.

System content and request-level tools are byte-identical within an Episode (`evaluate_workflow_v4_flash_v7.py:357-381`). Parsing fails closed for missing/multiple/non-function calls, invalid names, non-text arguments, duplicate JSON keys, nonfinite constants, branch mismatch, and duplicate call IDs (`evaluate_workflow_v4_flash_v7.py:248-272`, `390-419`).

## Thinking and private-data boundary

Provider message content is never copied into history or results. The ephemeral message is reduced to SHA256 and byte length, while the canonical action, error, and usage fields are retained (`evaluate_workflow_v4_flash_v7.py:228-231`, `386-419`).

Prompt construction consumes only public query/profile/observation and static public schemas (`evaluate_workflow_v4_flash_v7.py:141-225`, `357-381`). Private contract data is used only after harness execution for scoring (`evaluate_workflow_v4_flash_v7.py:461-490`), not in model requests.

## Native-tools verification gate and its limits

Formal execution requires probe evidence matching the frozen endpoint/model, probe kind/version, HTTP 200, recomputable production request/tools hashes, exact returned tool name/arguments, and response fingerprint metadata (`evaluate_workflow_v4_flash_v7.py:676-721`). No path silently falls back to text output.

The probe constructs its production tool schema from **only the first frozen Episode** (`probe_v4_flash_native_tools.py:30-40`). That schema contains its 32 device-operation branches and the event types selected for that Episode's query. The probe therefore checks whether the endpoint accepts and returns a native call under one real production-sized schema; it does **not** verify all 15 Episodes' different query-scoped event selections. Probe request construction, parsing, and evidence capture are at `probe_v4_flash_native_tools.py:43-120`.

The probe now uses the formal runner's network policy: `retries=2` means up to three total attempts, with bounded exponential delays of 0.5 and 1.0 seconds. It retries only `URLError`, timeout, and HTTP 429/500/502/503/504; other HTTP and malformed-payload failures return immediately. Failure evidence exposes only a finite diagnostic category (for example `dns_error`, `tls_error`, or `connection_reset`) and never exception text, credentials, or response bodies. Retry configuration is local transport behavior and is deliberately excluded from `request_sha256`, which continues to bind exactly the JSON body sent to the provider.

The evidence/checkpoint checks are structural and configuration-bound. They reject legacy weak evidence, missing or mismatched fields, wrong schema/configuration, malformed role order, unpaired or duplicate call IDs, noncanonical serialization, inconsistent token counters, transcript/action-record inconsistency, altered aggregate metadata, and non-prefix Episodes (`evaluate_workflow_v4_flash_v7.py:515-610`, `614-709`). They are not a cryptographic signature and do not claim to resist an actor who can fabricate every evidence field or arbitrarily edit local code and consistently rewrite all dependent fields.

## Verification coverage

`tests/test_evaluate_workflow_v4_flash_v7.py` covers the exact role sequence, paired IDs, accumulated history, initial-only query/profile/obs0, fresh observations, static tools, all 15 frozen Episodes with 32 operation branches each, explicit no-network FakeClient/Harness execution, thinking exclusion, private-data exclusion, strict malformed-output handling, split token accounting, probe retry/safety behavior, strong/weak probe evidence cases, checkpoint consistency, and transcript pairing/order/duplicate-ID tampering.

Offline command and result:

```bash
python -m pytest -q tests/test_evaluate_workflow_v4_flash_v7.py
# 30 passed
```

This PASS validates local construction, parsing, history, accounting, and checkpoint logic. Live native-tool compatibility is still **UNVERIFIED** and no formal V7 smoke result exists yet.
