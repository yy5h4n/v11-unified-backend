"""Policy sandbox checks.

Policies are declared by their input contract (an explicit list of public
observation variables and authorized actions plus the policy seed).  The
sandbox verifies (normative doc section 7):

  * policies read only the public-observation and authorized-action interface;
  * the finite witness-search subset ``Pi_cert`` is enumerated and contains the
    no-intervention policy ``pi_0``;
  * policy selection uses only frozen search seeds and never touches
    certification seeds (seed leakage and certification optimization are
    conformance failures);
  * ``pi_0`` is acceptable given the authorization status, and no-op is the
    correct default in the certified-no-op stratum.
"""

from __future__ import annotations

from typing import Any

from conformance_v1.config import CONFIG, ConformanceError

FORBIDDEN_INPUT_PREFIXES = ("episode_id", "hash", "source_id", "lookup", "private")


class PolicySandbox:
    def __init__(self, config=CONFIG):
        self.config = config

    def check_policy_admissible(self, policy: dict, public_vars: list[str], authorized_actions: list[str]) -> None:
        """Raise POLICY_UNADMISSIBLE if the declared policy inputs leave the
        public-observation and authorized-action interface."""
        declared_inputs = list(policy.get("inputs", []))
        declared_actions = list(policy.get("actions", []))
        for name in declared_inputs:
            if name not in public_vars:
                raise self.config.error("POLICY_UNADMISSIBLE", f"policy reads {name!r} outside public observations {public_vars}")
        for name in declared_actions:
            if name not in authorized_actions:
                raise self.config.error("POLICY_UNADMISSIBLE", f"policy emits {name!r} outside authorized actions {authorized_actions}")
        blob = " ".join(declared_inputs + declared_actions).lower()
        for forbidden in FORBIDDEN_INPUT_PREFIXES:
            if forbidden in blob:
                raise self.config.error("POLICY_UNADMISSIBLE", f"policy references forbidden input family {forbidden!r}")

    def check_pi_cert_contains_pi0(self, pi_cert_entries: list[dict], pi_0: dict) -> None:
        ids = [e.get("id") for e in pi_cert_entries]
        if pi_0.get("id") not in ids:
            raise self.config.error("SEED_LEAKAGE", f"pi_0 {pi_0.get('id')!r} is missing from Pi_cert")

    def check_seed_isolation(self, search_seeds: list[int], certification_seeds: list[int], search_log: list[dict]) -> None:
        """Search and certification seeds must be disjoint and the search phase
        must never touch a certification seed (SEED_LEAKAGE)."""
        cert_set = set(certification_seeds)
        overlap = set(search_seeds) & cert_set
        if overlap:
            raise self.config.error("SEED_LEAKAGE", f"search and certification seeds overlap: {sorted(overlap)}")
        for entry in search_log:
            seed = entry.get("seed_id")
            if seed in cert_set:
                raise self.config.error("SEED_LEAKAGE", f"search phase touched certification seed {seed!r}")

    def check_no_certification_optimization(self, selection_log: list[dict]) -> None:
        """The chosen policy must not have been selected using certification
        seed outcomes (CERTIFICATION_OPTIMIZATION)."""
        for entry in selection_log:
            if entry.get("evaluated_on") == "certification_seeds":
                raise self.config.error("CERTIFICATION_OPTIMIZATION", "policy selection optimized on certification seeds")

    def check_noop_acceptable(self, authorization_status: str, acceptable_policies: dict[str, list[str]]) -> None:
        allowed = acceptable_policies.get(authorization_status, [])
        if "noop" not in allowed:
            raise self.config.error("POLICY_UNADMISSIBLE", f"no-op not acceptable for authorization {authorization_status}")

    def check_bound_covers_pi_auth(self, certified_ub: float | None, covers_pi_auth_pub: bool) -> None:
        """A certified-no-op claim requires a bound over Pi_auth_pub; a bound
        covering only Pi_cert yields boundary_opportunity (spec section 7)."""
        if certified_ub is not None and not covers_pi_auth_pub:
            raise self.config.error("BOUND_NOT_COVERING_PI_AUTH", "certificate covers only Pi_cert, not Pi_auth_pub")
