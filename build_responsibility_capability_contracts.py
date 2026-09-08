#!/usr/bin/env python3
"""Build stable, reviewable capability contracts before backend matching.

The builder joins catalog identities to the existing direct-adapter packet. It
never infers a domain from natural_query or delegated_outcome. Only contracts
backed by an independently reviewed release are marked REVIEWED.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from responsibility_backend_matcher import CapabilityVector, DIMENSIONS


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "responsibility_ai_coding_v1" / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
DIRECT_PACKET = ROOT / "generated" / "direct_adapter_packet_v1.json"
OUTPUT = ROOT / "responsibility_ai_coding_v1" / "RESPONSIBILITY_CAPABILITY_CONTRACTS_V1.json"
REVIEWED_RELEASES = {
    ROOT / "generated" / "simuhome_kitchen_evening_release_v1" / "build_report.json": "room.addressable",
    ROOT / "generated" / "simuhome_multiroom_evening_release_v1" / "build_report.json": "multiroom.addressable",
}


def vector(**values: Iterable[str]) -> CapabilityVector:
    return CapabilityVector(**{name: frozenset(values.get(name, ())) for name in DIMENSIONS})


PROCESS_FAMILY_CONTRACTS = {
    "temperature": ("thermal_temperature", vector(
        observations=("clock", "room.temperature"), actions=("thermal.setpoint", "wait"),
        dynamics=("thermal.multiroom",), events=("clock.window",),
        evaluators=("temperature.trajectory", "command.ack", "trajectory.complete"),
        spatial=("room.addressable",), temporal=("closed_loop", "scheduled.window"),
    )),
    "lighting_blinds": ("lighting", vector(
        observations=("clock", "room.illuminance", "device.light_state"), actions=("light.on_off",),
        dynamics=("lighting.state_machine",), events=("clock.window",),
        evaluators=("illuminance.trajectory", "command.ack"), spatial=("room.addressable",), temporal=("closed_loop",),
    )),
    "humidity_iaq_ventilation": ("humidity_air_quality", vector(
        observations=("clock", "room.humidity_or_air_quality", "device.air_control_state"), actions=("air_quality.control",),
        dynamics=("air_quality.multiroom",), events=("clock.window",),
        evaluators=("air_quality.trajectory", "command.ack"), spatial=("room.addressable",), temporal=("closed_loop",),
    )),
    "multi_source_heating": ("thermal_energy", vector(
        observations=("clock", "room.temperature", "energy.source_state", "tariff"), actions=("energy.source_select", "thermal.setpoint"),
        dynamics=("thermal.multiroom", "household.energy_flow"), events=("clock.window",),
        evaluators=("temperature.trajectory", "energy.cost"), spatial=("house.addressable",), temporal=("closed_loop",),
    )),
    "occupancy_energy": ("energy", vector(
        observations=("clock", "occupancy", "household.load"), actions=("authorized.energy_control",),
        dynamics=("household.energy_flow",), events=("occupancy.change",),
        evaluators=("energy.integral",), spatial=("house.addressable",), temporal=("closed_loop",),
    )),
}


def add_context_requirements(capabilities: CapabilityVector, dependencies: list[str]) -> CapabilityVector:
    values = {name: set(getattr(capabilities, name)) for name in DIMENSIONS}
    for dependency in dependencies:
        if dependency == "care-recipient state":
            values["observations"].add("care_recipient.state")
            values["events"].add("care_recipient.change")
        elif dependency == "occupancy and presence state":
            values["observations"].add("occupancy")
            values["events"].add("occupancy.change")
        elif dependency == "device and energy state":
            values["observations"].add("device.energy_state")
            values["evaluators"].add("energy.integral")
        elif dependency == "clock, calendar, and lifecycle state":
            values["observations"].add("clock")
            values["events"].add("clock.window")
        elif dependency == "responsibility-relevant observable state":
            values["observations"].add("responsibility_specific.state")
    return CapabilityVector(**{name: frozenset(values[name]) for name in DIMENSIONS})


def reviewed_contracts() -> dict[str, dict]:
    reviewed = {}
    for path, scope in REVIEWED_RELEASES.items():
        report = json.loads(path.read_text(encoding="utf-8"))
        for responsibility_id in report["responsibility_episode_counts"]:
            reviewed[responsibility_id] = {"scope": scope, "evidence_ref": str(path.relative_to(ROOT))}
    return reviewed


def build() -> dict:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["queries"]
    packet = json.loads(DIRECT_PACKET.read_text(encoding="utf-8"))["candidates"]
    candidates = {row["responsibility_id"]: row for row in packet}
    reviewed = reviewed_contracts()
    contracts = []
    for row in catalog:
        responsibility_id = row["responsibility_id"]
        candidate = candidates.get(responsibility_id)
        if candidate is None or candidate["process_family"] == "contract_underspecified":
            contracts.append({
                "responsibility_id": responsibility_id,
                "contract_status": "MISSING_OPERATIONAL_CONTRACT",
                "domain": None,
                "capabilities": {name: [] for name in DIMENSIONS},
                "episode_inputs": [],
                "source_refs": [str(CATALOG.relative_to(ROOT))],
            })
            continue
        domain, base = PROCESS_FAMILY_CONTRACTS[candidate["process_family"]]
        capabilities = add_context_requirements(base, candidate["context_dependencies"])
        status = "PROVISIONAL_AI_STRUCTURED"
        refs = [str(DIRECT_PACKET.relative_to(ROOT))]
        if responsibility_id in reviewed:
            status = "REVIEWED"
            values = {name: set(getattr(capabilities, name)) for name in DIMENSIONS}
            values["spatial"] = {reviewed[responsibility_id]["scope"]}
            capabilities = CapabilityVector(**{name: frozenset(values[name]) for name in DIMENSIONS})
            refs.append(reviewed[responsibility_id]["evidence_ref"])
        else:
            values = {name: set(getattr(capabilities, name)) for name in DIMENSIONS}
            values["spatial"] = {"scope.unresolved"}
            capabilities = CapabilityVector(**{name: frozenset(values[name]) for name in DIMENSIONS})
        contracts.append({
            "responsibility_id": responsibility_id,
            "contract_status": status,
            "domain": domain,
            "capabilities": capabilities.as_dict(),
            "episode_inputs": sorted(f"profile:{item}" for item in candidate["profile_dependencies"]),
            "dynamic_context_requirements": list(candidate["context_dependencies"]),
            "source_refs": refs,
        })
    return {
        "schema_version": "responsibility-capability-contracts-v1",
        "policy": {
            "matcher_may_parse_natural_language": False,
            "full_requires_contract_status": "REVIEWED",
            "missing_contract_fails_closed": True,
        },
        "summary": {
            "responsibility_count": len(contracts),
            "reviewed": sum(row["contract_status"] == "REVIEWED" for row in contracts),
            "provisional": sum(row["contract_status"] == "PROVISIONAL_AI_STRUCTURED" for row in contracts),
            "missing": sum(row["contract_status"] == "MISSING_OPERATIONAL_CONTRACT" for row in contracts),
        },
        "contracts": contracts,
    }


def validate_source_refs(artifact: dict) -> None:
    """Fail closed when a Contract cites an absent or non-file project source."""

    missing: list[str] = []
    for contract in artifact["contracts"]:
        for reference in contract["source_refs"]:
            path = ROOT / reference
            if not path.is_file():
                missing.append(f"{contract['responsibility_id']}:{reference}")
    if missing:
        raise FileNotFoundError("missing responsibility Contract source refs: " + ", ".join(missing))


def serialized_output() -> str:
    artifact = build()
    validate_source_refs(artifact)
    return json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the generated artifact differs")
    args = parser.parse_args()
    content = serialized_output()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != content:
            raise SystemExit(f"stale responsibility capability Contract artifact: {OUTPUT}")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
