"""Construction-utility opportunity classification and primary assignment.

Implements the frozen construction utility ``U_C(tau) in [0,1]`` separation
(normative doc section 7): the evaluator reports the lexicographic Contract
verdict; selection uses the bounded utility.  Classification honors
``0 <= delta_noop < delta_positive <= 1``, the Pi_cert / Pi_auth_pub
separation, and the requirement that a certified-no-op claim needs a bound over
``Pi_auth_pub`` (a bound over Pi_cert alone yields boundary_opportunity).
"""

from __future__ import annotations

from typing import Any

from conformance_v1.config import CONFIG, ConformanceError


class OpportunityClassifier:
    def __init__(self, config=CONFIG):
        self.config = config

    def validate_thresholds(self) -> None:
        cfg = self.config.release_config["opportunity_thresholds"]
        d_noop = cfg["delta_noop"]
        d_pos = cfg["delta_positive"]
        if not (0 <= d_noop < d_pos <= 1):
            raise self.config.error(
                "OPPORTUNITY_THRESHOLD_INVALID",
                f"require 0 <= delta_noop < delta_positive <= 1, got {d_noop} < {d_pos}",
            )

    def classify(self, group: dict[str, Any]) -> str:
        """Classify an equivalence group into one of the four physical labels.

        ``group`` carries the frozen pre-registered fields:
          delta_lcb                 multiplicity-adjusted lower confidence bound
                                    of paired Delta_witness(g) for pi_hat(g)
          certified_ub              released upper bound on improvement over
                                    Pi_auth_pub (None when absent)
          certified_covers_pi_auth  bound covers Pi_auth_pub (not just Pi_cert)
          pi_hat_acceptable         pi_hat in intersection of authorized
                                    acceptable-policy sets
          pi_0_acceptable           no-intervention policy acceptable
          capability_supported      required capability present
          information_supported     required public information present
        """
        cfg = self.config.release_config["opportunity_thresholds"]
        self.validate_thresholds()
        d_noop = cfg["delta_noop"]
        d_pos = cfg["delta_positive"]
        if not group.get("capability_supported", True) or not group.get("information_supported", True):
            return "unsupported"
        certified_ub = group.get("certified_ub")
        if (
            certified_ub is not None
            and group.get("certified_covers_pi_auth") is True
            and certified_ub <= d_noop
            and group.get("pi_0_acceptable")
        ):
            return "certified_no_opportunity"
        delta_lcb = group.get("delta_lcb")
        if (
            delta_lcb is not None
            and delta_lcb >= d_pos
            and group.get("pi_hat_acceptable")
        ):
            return "positive_opportunity"
        return "boundary_opportunity"


def normalized_certificate_margin(margin: float, config=CONFIG) -> float:
    """Normalize a certificate margin into [0,1] using the release thresholds."""
    cfg = config.release_config["opportunity_thresholds"]
    d_noop = cfg["delta_noop"]
    d_pos = cfg["delta_positive"]
    if d_pos <= d_noop:
        return 0.0
    value = (margin - d_noop) / (d_pos - d_noop)
    return max(0.0, min(1.0, value))


def assign_primary(
    candidates: list[dict],
    process_ids: list[str],
    config=CONFIG,
) -> dict[str, str]:
    """Reference assignment over canonical processes in lexical process-ID
    order.  When a canonical process has multiple valid bindings, the chosen
    binding is the one with the highest normalized certificate margin, then the
    lexical responsibility ID (frozen order rule).  Runs once over canonical
    processes in lexical process-ID order; one process is never multiplied
    into independent primary samples."""
    cfg = config.release_config
    order = cfg["assignment"]["order"]
    if order != ["normalized_certificate_margin_desc", "lexical_responsibility_id"]:
        raise config.error("INVALID_TRANSITION", f"frozen assignment order changed: {order}")
    best: dict[str, list[dict]] = {}
    for cand in candidates:
        pid = cand["physical_process_id"]
        if pid not in process_ids:
            continue
        cand["_norm_margin"] = normalized_certificate_margin(cand["certificate_margin"], config)
        best.setdefault(pid, []).append(cand)
    primary: dict[str, str] = {}
    for pid in sorted(process_ids):
        cands = best.get(pid, [])
        if not cands:
            continue
        cands.sort(key=lambda c: (-c["_norm_margin"], c.get("responsibility_id", "")))
        primary[pid] = cands[0].get("responsibility_id", "")
    return primary
