"""Claim adapter over the frozen official EV2Gym EV-charging process pool.

Reuses the frozen v10 ``EVRuntime`` chain (v10 -> v9 -> v8 on top of the pinned
official EV2Gym checkout) together with the frozen ``diversity_pilot_v1``
public/private episode artifacts. No new events are simulated, no dataset is
generated, and nothing is written.

Claims are gated, never assumed: ``capabilities()`` reports
``EXECUTABLE_REPLAY_VERIFIED`` only after pinned verification passes — the
frozen artifact pairs load and are mutually consistent, every EV2Gym binding
carries pinned source/config digests with one consistent EV2Gym commit, the
pinned checkout HEAD matches that commit, the pinned runtime venv exists, and
a probe replay (fresh runtime + observe + one legal WAIT step) succeeds under
the pinned interpreter. Any miss raises :class:`EV2GymClaimError` (fail
closed).

Public surfaces stay label-free: observations cover the exogenous schedule
(arrival/departure), household load, price signal, and grid service limit plus
vehicle SoC — never the hidden ``responsibility_contract``, evaluator labels,
or gold actions. ``private_state()`` projects a whitelist of binding digests
and replay bookkeeping only; binding load/price traces and contract payloads
are never copied out. ``replay_id`` binds each stateful episode to the
source/config digests and the adapter identity.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from ..types import (
    BackendCapability,
    CapabilityStatus,
    PhysicalProcess,
    ProcessRequirement,
)
from .legacy_v10 import V11_ROOT, _canonical_json
from .legacy_v10 import PRIVATE_FILENAME, PUBLIC_FILENAME
from .legacy_v10 import EV2GymV10Adapter
from .legacy_v10_runtime import (
    V10RuntimeBridge,
    _jsonable,
    _schema_declares_parameters,
    _validate_action,
)

ADAPTER_ID = "unified_compiler.adapters.ev2gym_claim.v1"
PROVIDES: tuple[str, ...] = ("ev.soc", "ev.charge_action")

DEFAULT_V10_ROOT = V11_ROOT.parent / "v10_diversity_aware_compiler"
PINNED_EV2GYM_CHECKOUT = DEFAULT_V10_ROOT / ".runtime" / "ev2gym"
PINNED_RUNTIME_PYTHON = DEFAULT_V10_ROOT / ".runtime" / "venv" / "bin" / "python"
REQUIRED_DIGEST_FIELDS: tuple[str, ...] = (
    "config_sha256",
    "load_source_sha256",
    "price_source_sha256",
)
PROBE_TIMEOUT_SECONDS = 300

# Executed by the pinned venv interpreter only; proves the frozen runtime chain
# actually executes one legal step on the frozen artifacts. No writes occur.
_PROBE_SOURCE = (
    "import json, sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from unified_compiler.adapters.legacy_v10_runtime import V10RuntimeBridge\n"
    "runtime = V10RuntimeBridge().runtime_for_episode(sys.argv[2])\n"
    "observation = runtime.observe()\n"
    "transition = runtime.step({'type': 'WAIT'})\n"
    "print(json.dumps({'observation_keys': sorted(observation),\n"
    "                  'transition_keys': sorted(transition),\n"
    "                  'done': bool(runtime.done)}))\n"
)


class EV2GymClaimError(RuntimeError):
    """A pinned claim or replay invariant could not be proven (fail closed)."""


class UnknownClaimEpisodeError(LookupError):
    pass


def _digest_is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value.lower()
    )


class EV2GymClaimAdapter(EV2GymV10Adapter):
    """ProcessAdapter + stateful episode runtime over the frozen EV2Gym pool."""

    backend: ClassVar[str] = "EV2Gym"
    domain: ClassVar[str] = "ev_charging"
    capability: ClassVar[BackendCapability] = BackendCapability(
        capability_id="ev2gym_claim.ev",
        backend="EV2Gym",
        provides=PROVIDES,
        status=CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED,
        evidence=(
            f"prototypes/v10_diversity_aware_compiler/generated/diversity_pilot_v1/{PUBLIC_FILENAME}",
            f"prototypes/v10_diversity_aware_compiler/generated/diversity_pilot_v1/{PRIVATE_FILENAME}",
            "prototypes/v10_diversity_aware_compiler/runtime.py (EVRuntime)",
            "prototypes/v10_diversity_aware_compiler/.runtime/ev2gym (pinned official checkout)",
        ),
        metadata={
            "adapter_id": ADAPTER_ID,
            "claim": "EXECUTABLE_REPLAY_VERIFIED requires pinned verification to pass; otherwise fail closed",
        },
    )

    def __init__(
        self,
        index: V10ArtifactIndex | None = None,
        artifact_dir: Path | str | None = None,
    ) -> None:
        super().__init__(index=index, artifact_dir=artifact_dir)
        self._bridge = V10RuntimeBridge(self._index)
        self._verification: dict[str, Any] | None = None

    # ------------------------------------------------------------------ #
    # Pinned verification (fail closed)                                   #
    # ------------------------------------------------------------------ #

    @property
    def bridge(self) -> V10RuntimeBridge:
        return self._bridge

    def verification(self) -> Mapping[str, Any]:
        """Return the pinned-verification record, proving or raising."""
        if self._verification is None:
            self._verification = self._verify_pinned_claims()
        return self._verification

    def capabilities(self) -> Sequence[BackendCapability]:
        self.verification()
        return (self.capability,)

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        self.verification()
        self.scan_calls += 1
        processes = self.processes()
        if not requirements:
            return ()
        return tuple(
            process
            for process in processes
            if any(req.required_capabilities <= process.provided_capabilities for req in requirements)
        )

    def _verify_pinned_claims(self) -> dict[str, Any]:
        processes = self.processes()
        if not processes:
            raise EV2GymClaimError("no EV2Gym processes in the frozen artifact pool")

        episode_ids: list[str] = []
        commits: set[str] = set()
        for process in processes:
            binding = self._binding_for_process(process.process_id)
            for field in REQUIRED_DIGEST_FIELDS:
                if not _digest_is_sha256(binding.get(field)):
                    raise EV2GymClaimError(
                        f"process {process.process_id}: pinned digest {field!r} missing/invalid"
                    )
            commit = binding.get("commit")
            if not isinstance(commit, str) or not commit:
                raise EV2GymClaimError(
                    f"process {process.process_id}: pinned EV2Gym commit missing"
                )
            commits.add(commit)
            episode_ids.extend(process.manifest["legacy_episode_ids"])
        if len(commits) != 1:
            raise EV2GymClaimError(f"EV2Gym commit is not pinned across processes: {sorted(commits)}")
        commit = commits.pop()

        if not PINNED_RUNTIME_PYTHON.is_file():
            raise EV2GymClaimError(
                f"pinned runtime venv missing: {PINNED_RUNTIME_PYTHON} "
                "(bootstrap the frozen v10 runtime before claiming executability)"
            )
        self._verify_pinned_commit(commit)

        probe = self._run_probe(episode_ids[0])
        expected_observation_keys = {
            "charge_price_eur_per_kwh",
            "charger_max_power_kw",
            "declared_departure_time",
            "household_load_kw",
            "household_power_limit_kw",
            "required_departure_soc",
            "time",
            "vehicle_connected",
            "vehicle_soc",
        }
        if not expected_observation_keys <= set(probe["observation_keys"]):
            raise EV2GymClaimError(
                f"probe observation lacks exogenous keys: "
                f"{sorted(expected_observation_keys - set(probe['observation_keys']))}"
            )
        for forbidden in ("responsibility", "contract", "gold", "score", "evaluation"):
            if any(forbidden in key for key in probe["observation_keys"]):
                raise EV2GymClaimError(f"probe observation leaks evaluator label: {forbidden}")

        artifact_dir = self._index.artifact_dir
        return {
            "adapter_id": ADAPTER_ID,
            "status": self.capability.status.value,
            "commit": commit,
            "process_count": len(processes),
            "episode_count": len(episode_ids),
            "probe_episode_id": episode_ids[0],
            "probe_observation_keys": list(probe["observation_keys"]),
            "artifact_refs": [
                self._relative(artifact_dir / PUBLIC_FILENAME),
                self._relative(artifact_dir / PRIVATE_FILENAME),
            ],
            "artifact_digests": {
                name: self._file_digest(artifact_dir / name)
                for name in (PUBLIC_FILENAME, PRIVATE_FILENAME)
            },
            "pinned_runtime_python": self._relative(PINNED_RUNTIME_PYTHON),
            "pinned_checkout": self._relative(PINNED_EV2GYM_CHECKOUT),
        }

    def _verify_pinned_commit(self, commit: str) -> None:
        if not PINNED_EV2GYM_CHECKOUT.is_dir():
            raise EV2GymClaimError(
                f"pinned EV2Gym checkout missing: {PINNED_EV2GYM_CHECKOUT}"
            )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=PINNED_EV2GYM_CHECKOUT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        head = result.stdout.strip() if result.returncode == 0 else ""
        if head != commit:
            raise EV2GymClaimError(
                f"pinned checkout HEAD {head or '<unavailable>'} != binding commit {commit}"
            )

    def _run_probe(self, episode_id: str) -> dict[str, Any]:
        try:
            result = subprocess.run(
                [str(PINNED_RUNTIME_PYTHON), "-c", _PROBE_SOURCE, str(V11_ROOT), episode_id],
                cwd=V11_ROOT,
                capture_output=True,
                text=True,
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as exc:
            raise EV2GymClaimError(f"probe replay timed out: {episode_id}") from exc
        if result.returncode != 0:
            stderr_tail = "\n".join(result.stderr.strip().splitlines()[-5:])
            raise EV2GymClaimError(
                f"probe replay failed for {episode_id} (rc={result.returncode}): {stderr_tail}"
            )
        try:
            probe = json.loads(result.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError) as exc:
            raise EV2GymClaimError(f"probe replay produced no verdict: {episode_id}") from exc
        if not isinstance(probe, dict) or "observation_keys" not in probe:
            raise EV2GymClaimError(f"probe replay verdict malformed: {episode_id}")
        return probe

    def _binding_for_process(self, process_id: str) -> Mapping[str, Any]:
        episode_ids = self._index.episode_ids(process_id)
        private = self._index.private_record(episode_ids[0])
        binding = private.get("backend_binding")
        if not isinstance(binding, Mapping):
            raise EV2GymClaimError(f"process {process_id}: missing backend_binding")
        return binding

    def _relative(self, path: Path) -> str:
        try:
            return str(Path(path).resolve().relative_to(V11_ROOT.parent))
        except ValueError:
            return str(path)

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    # ------------------------------------------------------------------ #
    # Stateful episode runtime                                            #
    # ------------------------------------------------------------------ #

    def episode_ids(self) -> tuple[str, ...]:
        self.verification()
        ids: list[str] = []
        for process in self.processes():
            ids.extend(process.manifest["legacy_episode_ids"])
        return tuple(sorted(ids))

    def episode_runtime(self, episode_id: str) -> "EV2GymClaimEpisode":
        return EV2GymClaimEpisode(self, episode_id)

    def replay_id_for(self, episode_id: str) -> str:
        return _replay_id(self._claim_payload_for(episode_id))

    def _claim_payload_for(self, episode_id: str) -> dict[str, Any]:
        try:
            public, private = self._index.pair(episode_id)
        except KeyError:
            raise UnknownClaimEpisodeError(episode_id) from None
        binding = private.get("backend_binding")
        if not isinstance(binding, Mapping) or binding.get("backend") != self.backend:
            raise EV2GymClaimError(f"{episode_id}: not an EV2Gym episode")
        return {
            "adapter_id": ADAPTER_ID,
            "episode_id": episode_id,
            "physical_process_id": private.get("physical_process_id"),
            "config_sha256": binding.get("config_sha256"),
            "load_source_sha256": binding.get("load_source_sha256"),
            "price_source_sha256": binding.get("price_source_sha256"),
            "commit": binding.get("commit"),
            "arrival_step": binding.get("arrival_step"),
            "departure_step": binding.get("departure_step"),
            "public_schema_digest": hashlib.sha256(
                _canonical_json(
                    {
                        "allowed_actions": public.get("allowed_actions"),
                        "horizon_steps": public.get("horizon_steps"),
                        "observation_interval_minutes": public.get("observation_interval_minutes"),
                    }
                ).encode("utf-8")
            ).hexdigest(),
        }


def _replay_id(payload: Mapping[str, Any]) -> str:
    missing = [key for key, value in payload.items() if value is None]
    if missing:
        raise EV2GymClaimError(f"replay id inputs incomplete: {sorted(missing)}")
    return "sha256:" + hashlib.sha256(
        _canonical_json({"ev2gym_claim_replay_v1": dict(payload)}).encode("utf-8")
    ).hexdigest()


class EV2GymClaimEpisode:
    """Stateful, fail-closed runtime over exactly one frozen EV2Gym episode.

    Public surfaces: :meth:`observe` / :meth:`legal_actions` / :meth:`step`
    carry exogenous observations and public action schema only. Private
    surfaces: :meth:`private_state` exposes binding digests and replay
    bookkeeping, never traces, the hidden contract, or gold actions.
    """

    def __init__(self, adapter: EV2GymClaimAdapter, episode_id: str) -> None:
        self._adapter = adapter
        try:
            public, private = adapter.index.pair(episode_id)
        except KeyError:
            raise UnknownClaimEpisodeError(episode_id) from None
        binding = private.get("backend_binding")
        if not isinstance(binding, Mapping) or binding.get("backend") != adapter.backend:
            raise EV2GymClaimError(f"{episode_id}: not an EV2Gym episode")
        allowed_actions = public.get("allowed_actions")
        if not isinstance(allowed_actions, Mapping) or not allowed_actions:
            raise EV2GymClaimError(f"{episode_id}: malformed public allowed_actions")
        self.episode_id: str = episode_id
        self.public: Mapping[str, Any] = public
        self._allowed_actions = allowed_actions
        self._require_mapping = _schema_declares_parameters(allowed_actions)
        self._replay_id: str = _replay_id(adapter._claim_payload_for(episode_id))
        self._runtime: Any = None
        self._step_index = 0
        self._last_transition: Mapping[str, Any] | None = None

    @property
    def replay_id(self) -> str:
        return self._replay_id

    @property
    def done(self) -> bool:
        return False if self._runtime is None else bool(self._runtime.done)

    def reset(self) -> dict[str, Any]:
        """Instantiate a fresh frozen runtime and return the initial observation.

        The frozen v10 runtime chain is imported in-process; it requires the
        pinned runtime environment. Under any other interpreter this fails
        closed with :class:`EV2GymClaimError` rather than simulating anything.
        """
        try:
            self._runtime = self._adapter.bridge.runtime_for_episode(self.episode_id)
        except ImportError as exc:
            raise EV2GymClaimError(
                "frozen v10 runtime chain not importable under this interpreter "
                f"({exc}); rerun under the pinned runtime environment "
                f"{self._adapter._relative(PINNED_RUNTIME_PYTHON)}"
            ) from exc
        self._step_index = 0
        self._last_transition = None
        return self.observe()

    def observe(self) -> dict[str, Any]:
        """Public exogenous observation (arrival/departure, load, price, grid limit)."""
        if self._runtime is None:
            raise EV2GymClaimError("reset() required before observe()")
        if self._runtime.done:
            raise EV2GymClaimError(f"episode {self.episode_id} terminated")
        observation = _jsonable(self._runtime.observe())
        if not isinstance(observation, dict):
            raise EV2GymClaimError(f"{self.episode_id}: runtime observation malformed")
        return observation

    def legal_actions(self) -> dict[str, Any]:
        """Public action schema (fail-closed copy, no parameter defaults added)."""
        return json.loads(json.dumps(self._allowed_actions))

    def step(self, action: Any) -> dict[str, Any]:
        """Apply one caller-supplied action after public-schema validation."""
        if self._runtime is None:
            raise EV2GymClaimError("reset() required before step()")
        if self._runtime.done:
            raise EV2GymClaimError(f"episode {self.episode_id} terminated")
        _validate_action(action, self._allowed_actions, self._step_index, self._require_mapping)
        transition = _jsonable(self._runtime.step(action))
        if not isinstance(transition, Mapping) or "effect" not in transition:
            raise EV2GymClaimError(f"{self.episode_id}: runtime transition malformed")
        self._step_index += 1
        self._last_transition = transition
        return transition

    def private_state(self) -> dict[str, Any]:
        """Whitelisted private bookkeeping; never traces/contract/gold actions."""
        payload = self._adapter._claim_payload_for(self.episode_id)
        state: dict[str, Any] = {
            "replay_id": self._replay_id,
            "episode_id": self.episode_id,
            "physical_process_id": payload["physical_process_id"],
            "backend": self._adapter.backend,
            "backend_version": payload["commit"],
            "config_sha256": payload["config_sha256"],
            "load_source_sha256": payload["load_source_sha256"],
            "price_source_sha256": payload["price_source_sha256"],
            "step_index": self._step_index,
            "horizon_steps": self.public.get("horizon_steps"),
            "done": self.done,
            "started": self._runtime is not None,
            "last_transition": None
            if self._last_transition is None
            else {
                "source_step": self._last_transition.get("source_step"),
                "effect": self._last_transition.get("effect"),
                "backend_terminated": self._last_transition.get("backend_terminated"),
            },
            "contract_exposed": False,
        }
        return state
