"""Immutable-object state machine.

Frozen objects are append-only (normative doc section 2).  Every release-status
transition and every rejected mutation carries a machine-readable reason code
from the failure-code catalog.  A semantic or evaluator change never edits an
existing object in place; it creates a new version and invalidates the affected
downstream lineage.
"""

from __future__ import annotations

import copy
from typing import Any

from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1 import enums

REASON_FREEZE = "INVALID_TRANSITION"
REASON_MUTATE_FROZEN = "POST_FREEZE_MUTATION"


class ImmutableStore:
    """Content-addressed store with release-status lifecycle management."""

    def __init__(self, config=CONFIG):
        self.config = config
        self._objects: dict[str, dict[str, Any]] = {}      # id -> object
        self._frozen: dict[str, str] = {}                  # id -> frozen hash
        self._transitions: list[dict[str, Any]] = []       # audit log

    # -- object access ---------------------------------------------------
    def has(self, object_id: str) -> bool:
        return object_id in self._objects

    def get(self, object_id: str) -> dict[str, Any]:
        if object_id not in self._objects:
            raise ConformanceError("LINEAGE_BREAK", f"object {object_id!r} is not in the store")
        return copy.deepcopy(self._objects[object_id])

    def all(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(o) for o in self._objects.values()]

    def release_status(self, object_id: str) -> str:
        return self._objects[object_id]["statuses"]["release_status"]

    # -- mutation --------------------------------------------------------
    def put(self, obj: dict[str, Any]) -> str:
        """Insert a provisional object, or replace a provisional draft.

        Replacing a frozen/released/invalidated object raises
        POST_FREEZE_MUTATION.  Returns the object id.
        """
        oid = obj["id"]
        if oid in self._frozen:
            raise ConformanceError(
                self.config.require_code(REASON_MUTATE_FROZEN),
                f"cannot mutate {oid}: frozen at hash {self._frozen[oid]}",
            )
        status = obj["statuses"]["release_status"]
        if status not in ("provisional", "invalidated", "released"):
            raise ConformanceError("INVALID_TRANSITION", f"cannot insert {oid} in status {status}")
        if oid in self._objects and self._objects[oid]["statuses"]["release_status"] == "frozen":
            raise ConformanceError(
                self.config.require_code(REASON_MUTATE_FROZEN),
                f"cannot replace frozen {oid}",
            )
        self._objects[oid] = copy.deepcopy(obj)
        return oid

    def transition(self, object_id: str, to_status: str, reason_code: str) -> None:
        """Apply a release-status transition with a cataloged reason code."""
        self.config.require_code(reason_code)
        if object_id not in self._objects:
            raise ConformanceError("LINEAGE_BREAK", f"unknown object {object_id}")
        obj = self._objects[object_id]
        current = obj["statuses"]["release_status"]
        allowed = enums.RELEASE_TRANSITIONS.get(current, ())
        if to_status not in allowed:
            raise ConformanceError(
                "INVALID_TRANSITION",
                f"transition {current} -> {to_status} not allowed for {object_id}",
            )
        obj["statuses"]["release_status"] = to_status
        if to_status == "frozen":
            self._frozen[object_id] = self._snapshot_hash(obj)
        self._transitions.append(
            {"object_id": object_id, "from": current, "to": to_status, "reason_code": reason_code}
        )

    def freeze(self, object_id: str, reason_code: str) -> None:
        self.transition(object_id, "frozen", reason_code)

    def invalidate(self, object_id: str, reason_code: str) -> None:
        self.transition(object_id, "invalidated", reason_code)

    def release(self, object_id: str, reason_code: str) -> None:
        self.transition(object_id, "released", reason_code)

    # -- immutability enforcement ----------------------------------------
    def assert_unmodified(self, object_id: str) -> None:
        """Raise POST_FREEZE_MUTATION if a frozen object's content drifted."""
        if object_id in self._frozen:
            obj = self._objects[object_id]
            current = self._snapshot_hash(obj)
            if current != self._frozen[object_id]:
                raise ConformanceError(
                    self.config.require_code(REASON_MUTATE_FROZEN),
                    f"frozen object {object_id} drifted from its frozen hash",
                )

    def assert_all_immutable(self) -> None:
        for oid in self._frozen:
            self.assert_unmodified(oid)

    # -- lineage invalidation --------------------------------------------
    @staticmethod
    def _parent_ref_keys() -> dict[str, tuple[str, ...]]:
        """Field names that reference a parent object, per object type."""
        return {
            "CorpusCard": (),
            "EvidenceUnit": ("corpus_card_id",),
            "EvidenceBundle": ("corpus_card_id",),
            "CanonicalResponsibility": ("evidence_bundle_hash",),
            "Query": ("responsibility_id",),
            "Contract": ("query_id",),
            "OpportunityPredicate": ("contract_id",),
            "PhysicalProcess": (),
            "Episode": ("responsibility_id", "query_id", "contract_id", "physical_process_id"),
        }

    def _parent_refs(self, obj: dict[str, Any]) -> list[str]:
        keys = self._parent_ref_keys().get(obj.get("object_type"), ())
        return [obj[k] for k in keys if obj.get(k)]

    def invalidate_downstream(self, object_id: str, reason_code: str) -> list[str]:
        """Invalidate the object and every downstream object in lineage order.

        A downstream object is affected when one of its parent references is
        the invalidated object id or the hash of an invalidated object."""
        self.config.require_code(reason_code)
        affected: set[str] = {object_id}
        changed = True
        while changed:
            changed = False
            for oid in _lineage_order(list(self._objects.keys())):
                if oid in affected:
                    continue
                for ref in self._parent_refs(self._objects[oid]):
                    if ref in affected:
                        affected.add(oid)
                        changed = True
                        break
                    if any(self._objects[a].get("hash") == ref for a in affected):
                        affected.add(oid)
                        changed = True
                        break
        for oid in _lineage_order(sorted(affected)):
            if self._objects[oid]["statuses"]["release_status"] != "invalidated":
                self.transition(oid, "invalidated", reason_code)
        return sorted(affected)

    @staticmethod
    def _snapshot_hash(obj: dict[str, Any]) -> str:
        from conformance_v1 import hashing

        return hashing.object_hash(obj)


def _lineage_order(ids: list[str]) -> list[str]:
    """Deterministic stage order for lineage invalidation."""
    stage_rank = {
        "CorpusCard": 0,
        "EvidenceUnit": 1,
        "EvidenceBundle": 2,
        "CanonicalResponsibility": 3,
        "Query": 4,
        "Contract": 5,
        "OpportunityPredicate": 6,
        "PhysicalProcess": 7,
        "Episode": 8,
    }
    return sorted(ids, key=lambda o: (stage_rank.get(o, 9), o))
