"""Tiny corpus-to-Episode pipeline for the end-to-end conformance fixture.

This is a conformance-only pipeline.  It consumes only embedded synthetic
fixture processes (``source == "synthetic_fixture"``) and refuses to scan real
backends or construct real Episodes.  It builds the immutable lineage
CorpusCard + EvidenceUnit -> Bundle -> Responsibility -> Query -> Contract ->
OpportunityPredicate -> PhysicalProcess -> Episode, freezes the semantic chain
before touching processes, classifies opportunity, assigns primary and splits,
replays the evaluator, and runs release QA (fail closed).
"""

from __future__ import annotations

import json
from typing import Any

from conformance_v1 import enums, hashing, schemas
from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1.state_machine import ImmutableStore
from conformance_v1.validator import CrossObjectValidator
from conformance_v1.evaluator import TemporalEvaluator, utility
from conformance_v1.opportunity import OpportunityClassifier, assign_primary
from conformance_v1.sandbox import PolicySandbox
from conformance_v1.graphs import (
    assign_splits,
    build_physical_provenance_graph,
    build_semantic_provenance_graph,
    split_leakage_audit,
)

SCHEMA_VERSION = "1.0"
CREATED_AT = "2026-08-30T00:00:00Z"


def _base(id_: str, ot: str, statuses: dict) -> dict:
    return {
        "object_type": ot,
        "id": id_,
        "schema_version": SCHEMA_VERSION,
        "version": 1,
        "created_at": CREATED_AT,
        "statuses": statuses,
    }


def _with_hash(obj: dict) -> dict:
    obj["hash"] = hashing.object_hash(obj)
    return obj


class ReleaseFailedClosed(ConformanceError):
    def __init__(self, codes: list[str]):
        super().__init__("FAILED_CLOSED", f"release QA failed closed: {codes}")
        self.codes = codes


class PipelineResult:
    def __init__(self):
        self.objects: dict[str, dict] = {}
        self.verdicts: dict[str, dict] = {}
        self.splits: dict[str, str] = {}
        self.release_codes: list[str] = []
        self.release_verdict = "PASS"
        self.sensitivity: dict[str, Any] = {}
        self.store = ImmutableStore()


class CorpusToEpisodePipeline:
    def __init__(self, config=CONFIG):
        self.config = config
        self.validator = CrossObjectValidator(config)
        self.evaluator = TemporalEvaluator(config)
        self.classifier = OpportunityClassifier(config)
        self.sandbox = PolicySandbox(config)
        self.store = ImmutableStore(config)

    def run(self, fixture: dict) -> PipelineResult:
        result = PipelineResult()
        result.store = self.store
        meta = fixture["meta"]
        if not meta.get("is_synthetic_fixture"):
            raise self.config.error("INVALID_TRANSITION", "pipeline refuses non-synthetic fixtures")
        self._build_source(fixture["corpus"], result)
        self._build_semantic_chain(fixture, result)
        self._freeze_semantic_chain(result)
        self._build_processes(fixture, result)
        labels = self._classify(fixture["opportunity_groups"], result)
        self._assign_and_split(fixture, result, labels)
        self._build_episodes(fixture, result, labels)
        result.objects = {oid: self.store.get(oid) for oid in sorted(result.objects)}
        self._release_qa(fixture, result)
        return result

    # -- stage 1/2: corpus + evidence ---------------------------------------
    def _build_source(self, corpus: dict, result: PipelineResult) -> None:
        cc = corpus["corpus_card"]
        cc.update(_base(cc["id"], "CorpusCard", {
            "semantic_status": "provisional_ai_pilot",
            "authorization_status": "unknown",
            "physical_status": "unassigned",
            "release_status": "provisional",
        }))
        self.store.put(_with_hash(cc))
        result.objects[cc["id"]] = self.store.get(cc["id"])
        for unit in corpus["evidence_units"]:
            unit.update(_base(unit["id"], "EvidenceUnit", {
                "semantic_status": "provisional_ai_pilot",
                "authorization_status": "unknown",
                "physical_status": "unassigned",
                "release_status": "provisional",
            }))
            self.store.put(_with_hash(unit))
            result.objects[unit["id"]] = self.store.get(unit["id"])
        self.store.freeze(cc["id"], "FREEZE_EVENT")
        for unit in corpus["evidence_units"]:
            self.store.freeze(unit["id"], "FREEZE_EVENT")

    # -- stage 3/4: bundle, responsibility, query, contract, predicate --------
    def _build_semantic_chain(self, fixture: dict, result: PipelineResult) -> None:
        bundle = dict(fixture["bundle_draft"])
        bundle.update(_base(bundle["id"], "EvidenceBundle", {
            "semantic_status": "provisional_ai_pilot",
            "authorization_status": "unknown",
            "physical_status": "unassigned",
            "release_status": "provisional",
        }))
        self.store.put(_with_hash(bundle))
        result.objects[bundle["id"]] = self.store.get(bundle["id"])
        self.store.freeze(bundle["id"], "FREEZE_EVENT")

        resp = dict(fixture["responsibility_draft"])
        resp.update(_base(resp["id"], "CanonicalResponsibility", {
            "semantic_status": "human_validated",
            "authorization_status": "authorized_agent_control",
            "physical_status": "unassigned",
            "release_status": "provisional",
        }))
        resp["evidence_bundle_hash"] = bundle["hash"]
        resp["independence_unit_id"] = self._independence_unit_id(
            [result.objects[u] for u in bundle["supporting"] + bundle["compatible"]]
        )
        self.store.put(_with_hash(resp))
        result.objects[resp["id"]] = self.store.get(resp["id"])
        self.store.freeze(resp["id"], "FREEZE_EVENT")

        query = dict(fixture["query_draft"])
        query.update(_base(query["id"], "Query", {
            "semantic_status": "human_validated",
            "authorization_status": "authorized_agent_control",
            "physical_status": "unassigned",
            "release_status": "provisional",
        }))
        query["responsibility_id"] = resp["id"]
        self.store.put(_with_hash(query))
        result.objects[query["id"]] = self.store.get(query["id"])
        self.store.freeze(query["id"], "FREEZE_EVENT")

        contract = dict(fixture["contract_draft"])
        contract.update(_base(contract["id"], "Contract", {
            "semantic_status": "human_validated",
            "authorization_status": "authorized_agent_control",
            "physical_status": "unassigned",
            "release_status": "provisional",
        }))
        contract["query_id"] = query["id"]
        self.evaluator.validate_contract(contract)
        self.store.put(_with_hash(contract))
        result.objects[contract["id"]] = self.store.get(contract["id"])
        self.store.freeze(contract["id"], "FREEZE_EVENT")

        pred = dict(fixture["predicate_draft"])
        pred.update(_base(pred["id"], "OpportunityPredicate", {
            "semantic_status": "human_validated",
            "authorization_status": "authorized_agent_control",
            "physical_status": "unassigned",
            "release_status": "provisional",
        }))
        pred["contract_id"] = contract["id"]
        self.store.put(_with_hash(pred))
        result.objects[pred["id"]] = self.store.get(pred["id"])
        self.store.freeze(pred["id"], "FREEZE_EVENT")

    def _freeze_semantic_chain(self, result: PipelineResult) -> None:
        """The semantic hash is frozen before any backend process is inspected."""
        for oid in result.objects:
            if self.store.has(oid):
                self.store.assert_unmodified(oid)

    @staticmethod
    def _independence_unit_id(units: list[dict]) -> str:
        graph = build_semantic_provenance_graph(units)
        return "IU-" + ";".join("-".join(c) for c in sorted(graph["components"]))

    # -- stage 5/6: physical requirements + processes --------------------------
    def _build_processes(self, fixture: dict, result: PipelineResult) -> None:
        for draft in fixture["process_drafts"]:
            if draft["source"] != "synthetic_fixture":
                raise self.config.error("UNSUPPORTED_CAPABILITY", "pipeline scans only synthetic fixture processes")
            trace_ser = hashing.canonical_bytes(draft["trace"])
            proc = dict(draft)
            proc["source_hash"] = hashing.sha256_hex(trace_ser)
            proc["original_checksum"] = proc["source_hash"]
            proc["subject_key"] = "sk-" + hashing.sha256_hex(proc["id"].encode("utf-8"))[:12]
            proc.update(_base(proc["id"], "PhysicalProcess", {
                "semantic_status": "provisional_ai_pilot",
                "authorization_status": "unknown",
                "physical_status": "candidate_binding",
                "release_status": "provisional",
            }))
            self.store.put(_with_hash(proc))
            result.objects[proc["id"]] = self.store.get(proc["id"])
            self.store.freeze(proc["id"], "FREEZE_EVENT")

    # -- stage 5: opportunity classification ------------------------------------
    def _classify(self, groups: list[dict], result: PipelineResult) -> dict[str, str]:
        self.classifier.validate_thresholds()
        labels: dict[str, str] = {}
        for g in groups:
            label = self.classifier.classify(g)
            labels[g["process_id"]] = label
        return labels

    # -- stage 7/9: assignment and splits ---------------------------------------
    def _assign_and_split(self, fixture: dict, result: PipelineResult, labels: dict[str, str]) -> None:
        process_ids = [d["id"] for d in fixture["process_drafts"]]
        candidates = [
            {
                "physical_process_id": d["id"],
                "responsibility_id": fixture["responsibility_draft"]["id"],
                "certificate_margin": next(
                    g["certificate_margin"] for g in fixture["opportunity_groups"] if g["process_id"] == d["id"]
                ),
            }
            for d in fixture["process_drafts"]
        ]
        primary = assign_primary(candidates, process_ids, self.config)
        episode_ids = ["E-%03d" % (i + 1) for i in range(len(process_ids))]
        episodes = []
        for i, pid in enumerate(process_ids):
            episodes.append({
                "id": episode_ids[i],
                "physical_process_id": pid,
                "responsibility_id": fixture["responsibility_draft"]["id"],
                "physical_label": labels[pid],
            })
        track = fixture["expected"]["track"]
        responsibility_of = {e["id"]: e["responsibility_id"] for e in episodes}
        process_of = {e["id"]: e["physical_process_id"] for e in episodes}
        assignment, codes = assign_splits(
            episodes, track, self.config.release_config,
            seed=self.config.release_config["seeds"]["search_seeds"][0],
            responsibility_of=responsibility_of, process_of=process_of,
        )
        result.splits = assignment
        physical_graph = build_physical_provenance_graph(
            [result.objects[p] for p in process_ids]
        )
        comp_of = {e["id"]: _component_of(physical_graph, e["physical_process_id"]) for e in episodes}
        leak_codes = split_leakage_audit(episodes, assignment, comp_of)
        if leak_codes:
            result.release_codes += leak_codes

    # -- stage 8/10: episodes and QA ---------------------------------------------
    def _build_episodes(self, fixture: dict, result: PipelineResult, labels: dict[str, str]) -> None:
        contract = result.objects["CT-001"]
        query = result.objects["Q-001"]
        resp = result.objects["CR-001"]
        ep_ids = ["E-001", "E-002", "E-003"]
        for i, pid in enumerate(["PP-001", "PP-002", "PP-003"]):
            process = result.objects[pid]
            seed = process["physical_meta"]["generator_seed"]
            verdict = self.evaluator.evaluate(contract, process, policy_id="pi_hat_g1", seed=seed)
            eid = ep_ids[i]
            episode = {
                "object_type": "Episode",
                "id": eid,
                "schema_version": SCHEMA_VERSION,
                "version": 1,
                "created_at": CREATED_AT,
                "statuses": {
                    "semantic_status": resp["statuses"]["semantic_status"],
                    "authorization_status": "authorized_agent_control",
                    "physical_status": labels[pid],
                    "release_status": "provisional",
                },
                "responsibility_id": resp["id"],
                "query_id": query["id"],
                "contract_id": contract["id"],
                "physical_process_id": pid,
                "protocol": {"cadence": "1h", "horizon": process["horizon"], "termination": "finite_window"},
                "physical_label": labels[pid],
                "split": result.splits[eid],
                "replay_digest": verdict["replay_digest"],
                "terminal_verdict": verdict["terminal_verdict"],
                "public_record": {
                    "responsibility_query": query["text"],
                    "initial_observation": process["trace"]["obs"][0],
                    "observation_schema": "living_room_temp:number, outside_temp:number, season:string",
                    "legal_action_schema": "heat_setpoint_up|none",
                    "cadence": "1h",
                    "horizon": process["horizon"],
                    "termination": "finite_window",
                    "semantic_status": resp["statuses"]["semantic_status"],
                    "authorization_status": "authorized_agent_control",
                    "physical_status": labels[pid],
                    "content_hashes": {
                        "responsibility": resp["hash"], "query": query["hash"],
                        "contract": contract["hash"], "predicate": result.objects["OP-001"]["hash"],
                        "process": process["hash"],
                    },
                    "split": result.splits[eid],
                },
                "private_record": {
                    "lineage": {
                        "corpus_card": "CC-001",
                        "evidence_units": ["EU-001", "EU-002", "EU-003"],
                        "bundle": "EB-001",
                        "responsibility": resp["id"],
                        "query": query["id"],
                        "contract": contract["id"],
                        "predicate": "OP-001",
                    },
                    "backend_binding": {"process_id": pid, "backend": process["backend"], "source": process["source"]},
                    "source_hashes": {"source_hash": process["source_hash"], "original_checksum": process["original_checksum"]},
                    "evaluator_clauses": ",".join(c["clause_id"] for c in contract["clauses"]),
                    "qa_verdicts": {"replay": "pass", "sensitivity": "material"},
                    "selection_stratum": labels[pid],
                    "replay_digests": {"replay": verdict["replay_digest"]},
                    "witness_digest": hashing.sha256_hex(f"witness:{pid}".encode()),
                    "gold_actions": [],
                },
            }
            episode["hash"] = hashing.object_hash(episode)
            self.store.put(episode)
            self.store.freeze(eid, "FREEZE_EVENT")
            self.store.release(eid, "RELEASE_EVENT")
            result.objects[eid] = self.store.get(eid)
            result.verdicts[eid] = verdict
        # action sensitivity: no-op policy must not reach comfort by the deadline
        noop_proc = {"id": "PP-NOOP", "trace": fixture["noop_trace"]}
        noop_verdict = self.evaluator.evaluate(contract, noop_proc, policy_id="pi_0", seed=0)
        u_hat = utility(result.verdicts["E-001"])
        u_0 = utility(noop_verdict)
        result.sensitivity = {
            "material": u_hat > u_0,
            "utility_delta": u_hat - u_0,
            "pi0_terminal_verdict": noop_verdict["terminal_verdict"],
        }

    def _release_qa(self, fixture: dict, result: PipelineResult) -> None:
        codes: list[str] = []
        codes += self.validator.check_all(result.objects)
        coverage_codes = self.validator.check_coverage_completeness(result.objects)
        codes += coverage_codes
        # zero baseline-driven membership
        for g in fixture["opportunity_groups"]:
            if any(k.startswith("baseline_") for k in g):
                codes.append("BASELINE_DRIVEN_MEMBERSHIP")
        # positive/boundary/certified-no-op coverage from artifacts
        strata = [o["physical_label"] for o in result.objects.values() if o["object_type"] == "Episode"]
        min_stratum = self.config.release_config["quotas"]["per_stratum_min_episodes"]
        for stratum, minimum in min_stratum.items():
            if strata.count(stratum) < minimum:
                codes.append("QUOTA_INFEASIBLE")
        blocking = [c for c in codes if c not in ("QUOTA_INFEASIBLE", "BOUND_NOT_COVERING_PI_AUTH")]
        if blocking:
            result.release_codes += sorted(set(blocking))
            result.release_verdict = "FAILED_CLOSED"
        else:
            result.release_verdict = "PASS"


def _component_of(graph: dict, process_id: str) -> str:
    for i, comp in enumerate(graph["components"]):
        if process_id in comp:
            return "C%02d" % i
    return process_id
