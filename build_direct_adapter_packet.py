#!/usr/bin/env python3
"""Build the contract packet for responsibilities with direct adapter paths.

This artifact describes adapter requirements only.  It intentionally makes no
claim that a backend has been installed, probed, or validated.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "responsibility_ai_coding_v1" / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
SURVEY = ROOT / "generated" / "expanded_backend_survey_v1.json"
OUTPUT = ROOT / "generated" / "direct_adapter_packet_v1.json"

DIRECT_STATUS = "DIRECT_ADAPTER_CANDIDATE"
PACKET_STATUS = "ADAPTER_CANDIDATE_NOT_YET_PROBED"


def _kind(row: dict) -> str:
    if row["responsibility_id"] == "rd_f0bc2c668699":
        return "multi_source_heating"
    if row["responsibility_id"] == "rd_split_39d32cc7fd22":
        return "contract_underspecified"
    q = (row["natural_query"] + " " + row["delegated_outcome"]).lower()
    if any(x in q for x in ("blind", "daylight", "lit", "lighting", "bright")):
        return "lighting_blinds"
    if any(x in q for x in ("humidity", "healthy", "ventilat", "air inside")):
        return "humidity_iaq_ventilation"
    if any(x in q for x in ("energy", "wasting")) and "warm" not in q and "temperature" not in q:
        return "occupancy_energy"
    return "temperature"


def _requirements(kind: str) -> dict[str, list[str] | str | dict]:
    common = {"opportunity_predicate": {
        "all": ["responsibility context is observable", "at least one legal action can change a required state", "the evaluation horizon contains an active responsibility window"],
        "exclude": ["required observation missing", "action-insensitive process", "contract target undefined"],
    }}
    if kind == "temperature":
        return {
            "physical_topology": {"zones": ["target zone"], "state": ["air temperature", "thermal mass"], "actuators": ["heating/cooling plant", "airflow"], "exogenous": ["outdoor weather", "occupancy/internal gains"]},
            "required_observations": ["zone air temperature", "outdoor temperature", "occupancy or care-recipient context", "clock and schedule", "plant state and delivered power"],
            "required_actions": ["set or constrain thermal plant operating mode", "set zone temperature or plant request", "acknowledge actuator command"],
            "dynamics_clauses": ["Propagate zone temperature through plant response, thermal mass, weather, and internal gains.", "Preserve causal ordering between observation, command, and resulting state."],
            "evaluator_clauses": ["HARD: required temperature band holds during the responsibility-active interval.", "HARD: trajectory and actuator acknowledgements are complete.", "SOFT: cumulative HVAC energy, subordinate to comfort."],
            **common,
        }
    if kind == "humidity_iaq_ventilation":
        return {
            "physical_topology": {"zones": ["target zone", "source zone or outdoors"], "state": ["relative humidity", "CO2/IAQ proxy", "air temperature"], "actuators": ["mechanical ventilation", "exhaust or outdoor-air damper"], "exogenous": ["occupancy", "cooking/shower moisture and pollutants", "outdoor air"]},
            "required_observations": ["relative humidity", "CO2 or IAQ pollutant proxy", "zone temperature", "occupancy/activity context", "ventilation flow or fan state", "clock and schedule"],
            "required_actions": ["set ventilation or exhaust level", "open or close outdoor-air path", "acknowledge airflow command"],
            "dynamics_clauses": ["Propagate moisture and pollutant concentration through source generation, airflow exchange, and zone volume.", "Keep temperature, humidity, and air-quality channels distinct even when coupled."],
            "evaluator_clauses": ["HARD: configured humidity/IAQ safety bounds hold when the responsibility is active.", "HARD: required pollutant/moisture observations and actuator acknowledgements are complete.", "SOFT: cumulative ventilation/HVAC energy."],
            **common,
        }
    if kind == "lighting_blinds":
        return {
            "physical_topology": {"zones": ["target zone"], "state": ["illuminance", "daylight contribution", "privacy/blind position"], "actuators": ["electric lighting", "blind or shade position"], "exogenous": ["solar irradiance", "occupancy/presence", "clock"]},
            "required_observations": ["zone illuminance or daylight level", "presence/occupancy", "blind position", "lighting load/state", "solar condition", "clock and arrival window"],
            "required_actions": ["set lighting level or state", "set blind position", "acknowledge actuator command"],
            "dynamics_clauses": ["Propagate illuminance from daylight, blind position, and electric lighting.", "Preserve the distinction between visual comfort, privacy, and lighting energy."],
            "evaluator_clauses": ["HARD: configured illuminance/privacy condition holds during the active context.", "HARD: lighting/blind action acknowledgement and trajectory are complete.", "SOFT: cumulative electric-lighting energy."],
            **common,
        }
    if kind == "multi_source_heating":
        return {
            "physical_topology": {"zones": ["target thermal zone", "whole-home energy boundary"], "state": ["zone temperature", "source-specific delivered heat", "source-specific energy input"], "actuators": ["heating source selector", "source modulation"], "exogenous": ["weather", "occupancy", "tariff or efficiency curves"]},
            "required_observations": ["zone temperature", "heating demand", "source availability", "source-specific energy and efficiency", "clock and context"],
            "required_actions": ["select an available heating source", "modulate selected source", "acknowledge source command"],
            "dynamics_clauses": ["Propagate comfort and source-specific energy use under identical demand and weather."],
            "evaluator_clauses": ["HARD: comfort and equipment constraints remain feasible.", "SOFT: minimize configured energy/resource objective among feasible source choices."],
            **common,
        }
    if kind == "contract_underspecified":
        return {
            "physical_topology": {"zones": ["kitchen"], "state": ["UNRESOLVED readiness state"], "actuators": [], "exogenous": ["cooking deadline"]},
            "required_observations": ["evidence-defined kitchen readiness state"],
            "required_actions": ["evidence-authorized readiness action"],
            "dynamics_clauses": ["Cannot be frozen until readiness is operationally defined without backend inference."],
            "evaluator_clauses": ["BLOCKED: no evidence-grounded measurable readiness predicate."],
            **common,
        }
    return {
        "physical_topology": {"zones": ["target zone", "whole-home electrical boundary"], "state": ["occupancy/presence", "end-use load", "aggregate power"], "actuators": ["controllable end-use loads"], "exogenous": ["weather", "tariff or carbon signal", "resident activity"]},
        "required_observations": ["zone occupancy/presence", "device or end-use power", "aggregate electrical power", "device availability", "clock and tariff context"],
        "required_actions": ["enable, disable, defer, or modulate eligible load", "acknowledge device command"],
        "dynamics_clauses": ["Propagate occupancy-conditioned demand and device response into aggregate load.", "Respect protected loads, availability constraints, and the responsibility time window."],
        "evaluator_clauses": ["HARD: protected loads and service constraints remain satisfied.", "SOFT: cumulative controllable energy while the target space is unoccupied."],
        **common,
    }


def build() -> dict:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))["queries"]
    survey = json.loads(SURVEY.read_text(encoding="utf-8"))["mappings"]
    by_id = {r["responsibility_id"]: r for r in catalog}
    direct = [m for m in survey if m["potential_status"] == DIRECT_STATUS]
    if len(direct) != 25:
        raise RuntimeError(f"expected 25 direct candidates, got {len(direct)}")
    rows = []
    for m in direct:
        r = by_id[m["responsibility_id"]]
        kind = _kind(r)
        req = _requirements(kind)
        backends = ["EnergyPlus/OpenStudio"]
        if kind == "temperature":
            backends = ["EnergyPlus/OpenStudio", "BOPTEST"]
        if kind == "contract_underspecified":
            backends = []
        readiness = "DATA_SOURCE_PROBE_ELIGIBLE"
        if kind == "multi_source_heating":
            readiness = "MODEL_VARIANT_REQUIRED"
        elif kind == "contract_underspecified":
            readiness = "CONTRACT_UNDERSPECIFIED"
        rows.append({
            "responsibility_id": r["responsibility_id"],
            "responsibility": r["delegated_outcome"],
            "query": r["natural_query"],
            "family": r["family"],
            "beneficiary": r["beneficiary"],
            "lifecycle": r["lifecycle"],
            "physical_topology": req["physical_topology"],
            "required_observations": req["required_observations"],
            "required_actions": req["required_actions"],
            "dynamics_clauses": req["dynamics_clauses"],
            "evaluator_clauses": req["evaluator_clauses"],
            "opportunity_predicate": req["opportunity_predicate"],
            "primary_backends": backends,
            "process_family": kind,
            "adapter_readiness": readiness,
            "profile_dependencies": r.get("profile_dependencies", []),
            "context_dependencies": r.get("episode_context_dependencies", []),
            "status": PACKET_STATUS,
        })
    return {"schema_version": "direct-adapter-packet-v1", "status": PACKET_STATUS, "candidate_count": len(rows), "candidates": rows}


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
