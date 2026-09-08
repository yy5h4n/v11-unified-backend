# D1 discrete-device fault backend evidence

This route is backend-only.  `unified_compiler/adapters/d1_discrete_device_fault.py`
wraps the real `harness_v2.workflow_backend.WorkflowBackend` state machine and
adds an immutable, agent-independent fault schedule for:

* `front_door_lock.main`
* `garage_door.main`
* `laundry.washer`
* `dishwasher.main`

The supported mechanisms are `offline`, `stuck`, `jammed`, and `slowdown`.
Offline/stuck/jammed commands are rejected atomically with a distinct error
code; active timed motion is frozen.  Slowdown accepts the same command and
extends the underlying operation deterministically.  Fault windows use
`[start_step, end_step)` and cannot overlap for one device.  Only currently
active faults appear in public observations, so future schedule windows are not
advertised to an agent.

## Replay gate

`generated/d1_discrete_device_fault_v1/replay_gate.json` records compact
target-device evidence: action outcomes, target state/attribute sequences,
trace digests, healthy-vs-fault witnesses, schedule IDs, and source hashes.  It
does not embed the full inventory, public profile, or full observation.  The
probe covers all four devices for offline/stuck/jammed and timed slowdown for
the garage, washer, and dishwasher.  Its checks require deterministic replay,
same-action healthy/fault divergence, atomic rejected commands, and complete
source provenance; the checked-in gate is below 100 KB.

Reproduce and verify the evidence with:

```text
python probe_d1_discrete_device_fault.py
python probe_d1_discrete_device_fault.py --check
python -m pytest -q tests/test_d1_discrete_device_fault.py
```

This evidence does not claim responsibility alignment, a benchmark Episode,
query or gold action, dataset quality, evaluator validity, hardware
diagnostics, or real-world failure rates.
