"""Reference cross-object validator.

JSON Schema is necessary but insufficient (normative doc section 12); this
module carries the semantic checks: lineage integrity, content hashes, status
separation, inference-matrix compliance, Query/Contract entailment structure,
contract-construction gates, opportunity-predicate preregistration, release
thresholds, and public/private record separation.
"""

from __future__ import annotations

from typing import Any, Iterable

from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1 import enums, hashing, schemas
from conformance_v1.evaluator import TemporalEvaluator

LINEAGE_CHAIN = (
    ("CorpusCard", "EvidenceUnit"),
    ("EvidenceUnit", "EvidenceBundle"),
    ("EvidenceBundle", "CanonicalResponsibility"),
    ("CanonicalResponsibility", "Query"),
    ("Query", "Contract"),
    ("Contract", "OpportunityPredicate"),
    ("OpportunityPredicate", "PhysicalProcess"),
    ("PhysicalProcess", "Episode"),
)

_PARENT_REF = {
    "EvidenceUnit": "corpus_card_id",
    "EvidenceBundle": "corpus_card_id",
    "CanonicalResponsibility": "evidence_bundle_hash",
    "Query": "responsibility_id",
    "Contract": "query_id",
    "OpportunityPredicate": "contract_id",
    "Episode": "physical_process_id",
}


class CrossObjectValidator:
    def __init__(self, config=CONFIG, inference_matrix: dict | None = None):
        self.config = config
        self._matrix = inference_matrix or config.fixture("fixtures/inference_matrix.json")
        self.evaluator = TemporalEvaluator(config)

    # -- per-object checks --------------------------------------------------
    def check_object(self, obj: dict) -> list[str]:
        codes: list[str] = []
        errors = schemas.validate(obj, schemas.schema_for_object(obj))
        if errors:
            codes.append("SCHEMA_VIOLATION")
        if obj.get("hash") != hashing.object_hash(obj):
            codes.append("HASH_MISMATCH")
        if not self._check_statuses(obj):
            codes.append("STATUS_SEPARATION_VIOLATION")
        ot = obj["object_type"]
        if ot == "EvidenceUnit":
            codes += self._check_inference(obj)
        if ot == "CanonicalResponsibility":
            codes += self._check_responsibility(obj)
        if ot == "Query":
            codes += self._check_query(obj)
        if ot == "Contract":
            codes += self._check_contract(obj)
        if ot == "OpportunityPredicate":
            codes += self._check_predicate(obj)
        if ot == "Episode":
            codes += self._check_episode(obj)
        return sorted(set(codes))

    @staticmethod
    def _check_statuses(obj: dict) -> bool:
        statuses = obj.get("statuses")
        if not isinstance(statuses, dict):
            return False
        present = all(k in statuses for k in ("semantic_status", "authorization_status", "physical_status", "release_status"))
        if "status" in obj:  # collapsed single field is a violation
            return False
        return present

    def _check_inference(self, unit: dict) -> list[str]:
        """Every coded proposition records the inference-matrix row that
        permits it; a code in a row's cannot_establish_alone list is a
        conformance failure (spec section 2.1 and gate section 4)."""
        codes: list[str] = []
        st = unit["source"]["source_type"]
        rows = self._matrix.get("rows", [])
        row = next((r for r in rows if r["source_type"] == st), None)
        if row is None:
            codes.append("UNSUPPORTED_INFERENCE")
            return codes
        may_support = set(row.get("may_support", []))
        cannot = set(row.get("cannot_establish_alone", []))
        for prop in unit.get("propositions", []):
            if prop.get("matrix_row_source_type") != st:
                codes.append("UNSUPPORTED_INFERENCE")
            if prop.get("code") in cannot:
                codes.append("UNSUPPORTED_INFERENCE")
            if prop.get("inference_matrix_row") != row.get("label", st):
                codes.append("UNSUPPORTED_INFERENCE")
            if prop.get("code") not in may_support:
                codes.append("UNSUPPORTED_INFERENCE")
        return codes

    @staticmethod
    def _check_responsibility(obj: dict) -> list[str]:
        codes: list[str] = []
        if obj.get("backend_influence") not in (None, "none"):
            codes.append("BACKEND_DERIVED_RESPONSIBILITY")
        if obj["statuses"]["semantic_status"] == "human_validated" and obj["admission"]["precision"] < 0.8:
            codes.append("INVALID_TRANSITION")
        if obj["statuses"]["semantic_status"] == "human_validated" and obj["admission"]["recall"] < 0.7:
            codes.append("INVALID_TRANSITION")
        return codes

    @staticmethod
    def _check_query(obj: dict) -> list[str]:
        if obj.get("equivalence_verdict") != "bidirectional_entailed":
            return ["ENTAILMENT_FAILURE"]
        return []

    def _check_contract(self, obj: dict) -> list[str]:
        codes: list[str] = []
        try:
            self.evaluator.validate_contract(obj)
        except ConformanceError as e:
            codes.append(e.code)
        for clause in obj.get("clauses", []):
            for cov in clause.get("coverage", []):
                if cov.get("adjudication") != "pass":
                    codes.append("ENTAILMENT_FAILURE")
            if not clause.get("coverage"):
                codes.append("MISSING_REQUIRED_COVERAGE")
            if clause["kind"] in ("conditional_obligation", "terminal_goal") and self._formula_prescribes_action(clause.get("formula", {})):
                codes.append("GOLD_ACTION_IN_CONTRACT")
        priority = set(obj.get("priority_order", []))
        for clause in obj.get("clauses", []):
            if clause["clause_id"] not in priority:
                codes.append("INVALID_TRANSITION")
        return codes

    @staticmethod
    def _formula_prescribes_action(formula: dict) -> bool:
        """No gold action sequence may be part of the Contract (spec gate 6).

        Only an action predicate inside an obligation or goal subtree is a
        prohibited gold action.  Action references in hard invariants, soft
        cost triggers and release conditions are legal (authorization
        boundaries) and are not flagged."""
        if not isinstance(formula, dict):
            return False
        op = formula.get("op")
        if op == "action":
            return True
        if op == "within":
            return CrossObjectValidator._formula_prescribes_action(formula.get("obligation"))
        if op == "terminal_goal":
            return CrossObjectValidator._formula_prescribes_action(formula.get("goal"))
        if op in ("release_when", "soft_cost", "cooldown_after"):
            return False
        for key in ("expr", "exprs", "pred"):
            val = formula.get(key)
            if isinstance(val, dict) and CrossObjectValidator._formula_prescribes_action(val):
                return True
            if isinstance(val, list) and any(
                isinstance(v, dict) and CrossObjectValidator._formula_prescribes_action(v)
                for v in val
            ):
                return True
        return False

    @staticmethod
    def _check_predicate(obj: dict) -> list[str]:
        """The predicate is preregistered and must not name a backend record."""
        blob = " ".join(
            str(obj.get(k, ""))
            for k in ("authorization_boundaries", "action_sensitivity_criterion")
        ).lower()
        for marker in ("episode_id", "source_id", "backend=", "process_id="):
            if marker in blob:
                return ["BACKEND_DERIVED_RESPONSIBILITY"]
        return []

    @staticmethod
    def _check_episode(obj: dict) -> list[str]:
        codes: list[str] = []
        pub = obj.get("public_record", {})
        priv = obj.get("private_record", {})
        for forbidden in ("evaluator_clauses", "gold_actions", "witness"):
            if forbidden in pub:
                codes.append("GOLD_LEAKAGE")
        if priv.get("gold_actions"):
            codes.append("GOLD_LEAKAGE")
        if obj.get("physical_label") == "none" and obj.get("split") != "none":
            codes.append("INVALID_TRANSITION")
        return codes

    # -- cross-object checks --------------------------------------------------
    def check_lineage(self, objects: dict[str, dict]) -> list[str]:
        """Parent references exist, parent hashes match, and the immutable
        lineage order holds.  A semantic change invalidates all downstream
        hashes (spec section 2); any break is a LINEAGE_BREAK."""
        codes: list[str] = []
        by_type: dict[str, list[dict]] = {}
        for obj in objects.values():
            by_type.setdefault(obj["object_type"], []).append(obj)
        for obj in objects.values():
            ot = obj["object_type"]
            if ot not in _PARENT_REF:
                continue
            ref_key = _PARENT_REF[ot]
            ref = obj.get(ref_key)
            if ref is None:
                codes.append("LINEAGE_BREAK")
                continue
            parent = objects.get(ref)
            if parent is None and not any(p.get("hash") == ref for p in objects.values()):
                codes.append("LINEAGE_BREAK")
            elif parent is not None and ot == "CanonicalResponsibility" and obj[ref_key] != parent.get("hash"):
                codes.append("LINEAGE_BREAK")
        if by_type.get("PhysicalProcess") and by_type.get("CanonicalResponsibility"):
            for p in by_type["PhysicalProcess"]:
                if p.get("source") == "real_backend":
                    resp = by_type["CanonicalResponsibility"][0]
                    if resp["statuses"]["release_status"] not in ("frozen", "released"):
                        codes.append("SEMANTIC_NOT_FROZEN_BEFORE_SCAN")
        # semantic_unknown / out_of_scope / rejected_or_unresolved responsibilities
        # cannot be promoted into Query/Contract/Episode (spec section 5).
        for resp in by_type.get("CanonicalResponsibility", []):
            sem = resp["statuses"]["semantic_status"]
            if sem in ("semantic_unknown", "out_of_scope", "rejected_or_unresolved"):
                for ot in ("Query", "Contract", "Episode"):
                    for child in by_type.get(ot, []):
                        if child.get("responsibility_id") == resp["id"]:
                            codes.append("INVALID_TRANSITION")
        return sorted(set(codes))

    def check_coverage_completeness(self, objects: dict[str, dict]) -> list[str]:
        """Every responsibility field must have a clause-coverage row and every
        released Contract must have passed clause-by-clause entailment
        (spec section 6 gate and coverage matrix)."""
        resp = next((o for o in objects.values() if o["object_type"] == "CanonicalResponsibility"), None)
        contracts = [o for o in objects.values() if o["object_type"] == "Contract"]
        if resp is None or not contracts:
            return []
        fields = [
            "desired_state", "beneficiary", "accountable_party", "context",
            "persistence", "failure_meaning", "override_release", "authorized_action_set",
        ]
        covered: set[str] = set()
        for c in contracts:
            for clause in c.get("clauses", []):
                for cov in clause.get("coverage", []):
                    covered.add(cov["responsibility_field"])
        missing = [f for f in fields if f not in covered]
        if missing:
            return ["MISSING_REQUIRED_COVERAGE"]
        return []

    def check_release_config(self) -> list[str]:
        cfg = self.config.release_config
        codes: list[str] = []
        d_noop = cfg["opportunity_thresholds"]["delta_noop"]
        d_pos = cfg["opportunity_thresholds"]["delta_positive"]
        if not (0 <= d_noop < d_pos <= 1):
            codes.append("OPPORTUNITY_THRESHOLD_INVALID")
        overlap = set(cfg["seeds"]["search_seeds"]) & set(cfg["seeds"]["certification_seeds"])
        if overlap:
            codes.append("SEED_LEAKAGE")
        pi0 = cfg["pi_0"]["id"]
        pi_cert_ids = [e.get("id") for e in cfg["pi_cert"]["entries"]]
        if pi0 not in pi_cert_ids:
            codes.append("SEED_LEAKAGE")
        return sorted(set(codes))

    def check_all(self, objects: dict[str, dict]) -> list[str]:
        codes: list[str] = []
        for obj in objects.values():
            codes += self.check_object(obj)
        codes += self.check_lineage(objects)
        codes += self.check_release_config()
        return sorted(set(codes))
