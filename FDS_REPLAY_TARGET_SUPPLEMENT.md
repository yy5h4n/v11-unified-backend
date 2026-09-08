# User-approved FDS replay target supplement

Date: 2026-09-07

The current production target is reliable Episode generation with truthful
backend execution modes. The user approved real FDS full-history/prefix replay
as a formal execution mode. FDS therefore remains in the 15-route inventory
and may satisfy the Episode-generation gate when its replay history,
same-prefix causal fork, clock/action alignment, no-future-leakage,
isolation/cleanup, horizon and measured cost evidence pass.

This supplement does not change frozen dataset construction specifications,
historical release tuples, or conformance hashes. It does not claim native
lockstep continuation: `online_step_supported` remains false, and requests
that explicitly require synchronous native online stepping are rejected.
Interactive conformance and replay Episode readiness are separate gates.

The approved FDS model currently supports a 4-second physical horizon and
replays the accumulated schedule through a fresh real FDS process for each
transition. Reports must retain that execution cost and process isolation
semantics rather than relabeling replay as a persistent solver session.
