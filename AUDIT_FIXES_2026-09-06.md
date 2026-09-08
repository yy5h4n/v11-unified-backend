# Audit fixes staged

This scratch set addresses the bounded findings F1/F2/F3 and D1 boundary issues:

- `unified_compiler/evaluator.py` rejects non-finite/non-numeric trajectory values and clause operands/weights, validates equal trajectory lengths at scoring time (including post-construction mutation), and gives empty hard trajectories a real violation.
- `unified_compiler/agent_interface.py` validates D1 `dt_seconds`, tracks terminal lifecycle, refuses post-terminal steps before backend mutation, derives D1 time from the native finite workflow step at the fixed 60-second tick, and keeps private feedback out of public receipts.
- `probe_d3_ev2gym_electric_competition.py` uses seed `3` for its CLI default, matching `probe()`.
- `tests/test_audit_regressions.py` covers the evaluator attacks and the actual D1 workflow facade.

Validation command after placing these files over the project tree:

```bash
PYTHONPATH=. pytest -q tests/test_audit_regressions.py tests/test_evaluator.py tests/test_agent_interface.py
```

No evidence JSON or generated artifact is regenerated. Existing hash-bound probes that include `agent_interface.py` will become stale until explicitly rebuilt. This patch intentionally does not add an `expected_horizon` argument: exact horizon binding remains the caller's responsibility to preserve the existing evaluator API. Frozen normative specification conflicts remain unresolved.
