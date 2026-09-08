#!/usr/bin/env python3
"""Build a search-evidence-only survey of additional responsibility backends."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "responsibility_ai_coding_v1" / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
OUTPUT = ROOT / "generated" / "expanded_backend_survey_v1.json"
REPORT = ROOT / "generated" / "expanded_backend_survey_report_v1.md"

DIRECT = {
    "rd_08ee0b241675", "rd_37104b57370a", "rd_54bdb4d1d881", "rd_5677579cc63f",
    "rd_670cf48458c9", "rd_80682ef0d394", "rd_94d666a58c83", "rd_b5071d572e3d",
    "rd_ebd968ad40ce", "rd_f0bc2c668699", "rd_split_016151c7c038",
    "rd_split_39d32cc7fd22", "rd_split_42d866d7dbea", "rd_split_5c776896ad46",
    "rd_split_7b8e77e75605", "rd_split_b8457e559b4d", "rd_split_be2cdd7acdff",
    "rd_45f013e7ba1d", "rd_b29cce0b1017", "rd_b62dd368cce0", "rd_bc53f8b79068",
    "rd_cf2389fcc9f1", "rd_split_4053e031415b", "rd_split_c828da9fc975",
    "rd_split_57dc1a5ccd89",
}

COMPOSITE = {
    "rd_a73f6a96c2fb", "rd_f4162b3d081d", "rd_02fee0f03674", "rd_05920f8dade6",
    "rd_0f0211760666", "rd_2126fdb0eb1b", "rd_48cb6e53506a", "rd_701d4b0275df",
    "rd_9fa396d3191a", "rd_a948a16db4ab", "rd_d813bb2cad2c", "rd_e0dd1723fdde",
    "rd_f61b22e412c4", "rd_split_0feef23c4030", "rd_split_94be9427a8c4",
    "rd_7232f3cd346d", "rd_86794f200f16", "rd_d061b1bb96d7", "rd_ddf75b21d41e",
    "rd_split_af088787b04c", "rd_120b0762bd34", "rd_68c81fbf8bad", "rd_89ce1d2593d3",
    "rd_952c27c5b039", "rd_b4f36043ef87", "rd_bc735bdc916e", "rd_c6fef22c7368",
    "rd_afa9de7bd514", "rd_a07aecceb886", "rd_bd6dfd6a2c77", "rd_425a5dee5607",
    "rd_1fb2eead6103", "rd_3cd183cf73e1",
}

EMBODIED = {
    "rd_05920f8dade6", "rd_0f0211760666", "rd_2126fdb0eb1b", "rd_48cb6e53506a",
    "rd_701d4b0275df", "rd_9fa396d3191a", "rd_a948a16db4ab", "rd_d813bb2cad2c",
    "rd_e0dd1723fdde", "rd_f61b22e412c4", "rd_split_0feef23c4030",
    "rd_split_94be9427a8c4", "rd_a07aecceb886", "rd_bd6dfd6a2c77",
    "rd_425a5dee5607", "rd_1fb2eead6103",
}
BIOPHYSICAL = {
    "rd_7232f3cd346d", "rd_86794f200f16", "rd_d061b1bb96d7", "rd_ddf75b21d41e",
    "rd_split_af088787b04c",
}
HAZARD = {
    "rd_120b0762bd34", "rd_68c81fbf8bad", "rd_89ce1d2593d3", "rd_952c27c5b039",
    "rd_b4f36043ef87", "rd_bc735bdc916e", "rd_c6fef22c7368", "rd_afa9de7bd514",
    "rd_3cd183cf73e1",
}
WATER_HAZARD = {"rd_68c81fbf8bad", "rd_b4f36043ef87", "rd_3cd183cf73e1"}
FIRE_GAS_HAZARD = {"rd_89ce1d2593d3", "rd_952c27c5b039", "rd_bc735bdc916e", "rd_c6fef22c7368"}

DIRECT_LIGHTING = {
    "rd_split_5c776896ad46", "rd_split_7b8e77e75605", "rd_45f013e7ba1d",
    "rd_b29cce0b1017", "rd_b62dd368cce0", "rd_bc53f8b79068", "rd_cf2389fcc9f1",
    "rd_split_4053e031415b", "rd_split_c828da9fc975",
}
DIRECT_ENVIRONMENT_ONLY = {
    "rd_54bdb4d1d881", "rd_670cf48458c9", "rd_b5071d572e3d", "rd_ebd968ad40ce",
    "rd_f0bc2c668699", "rd_split_57dc1a5ccd89",
} | DIRECT_LIGHTING


def candidates(rid: str, status: str) -> tuple[list[str], list[dict[str, str]]]:
    if status == "DIRECT_ADAPTER_CANDIDATE":
        if rid in DIRECT_ENVIRONMENT_ONLY:
            return ["EnergyPlus/OpenStudio"], []
        return ["EnergyPlus/OpenStudio", "BOPTEST"], []
    if rid in EMBODIED:
        return ["BEHAVIOR-1K/OmniGibson"], [{"backend": "Home Assistant", "backend_role": "event_bridge"}]
    if rid in BIOPHYSICAL:
        return ["AquaCrop/GreenLight"], [{"backend": "EnergyPlus or OmniGibson extension", "backend_role": "coupled_physics_extension"}]
    if rid in HAZARD:
        if rid in WATER_HAZARD:
            primary = ["EPANET"]
        elif rid in FIRE_GAS_HAZARD:
            primary = ["FDS"]
        elif rid == "rd_120b0762bd34":
            primary = ["GridLAB-D", "EnergyPlus/OpenStudio"]
        else:
            primary = ["EnergyPlus/OpenStudio"]
        return primary, [
            {"backend": "Home Assistant", "backend_role": "event_bridge"},
            {"backend": "responsibility-specific household hazard model", "backend_role": "coupled_physics_extension"},
        ]
    if rid == "rd_02fee0f03674":
        return ["EnergyPlus/OpenStudio", "GridLAB-D"], []
    return ["EnergyPlus/OpenStudio", "BOPTEST"], [{"backend": "responsibility-specific thin model", "backend_role": "coupled_physics_extension"}]


def build() -> tuple[dict, str]:
    rows = json.loads(CATALOG.read_text(encoding="utf-8"))["queries"]
    ids = {row["responsibility_id"] for row in rows}
    if len(ids) != len(rows) or len(ids) != 129:
        raise RuntimeError("source catalog must contain 129 unique responsibility IDs")
    if len(DIRECT) != 25 or len(COMPOSITE) != 33:
        raise RuntimeError("expanded backend classification counts changed")
    if DIRECT & COMPOSITE or not (DIRECT | COMPOSITE) <= ids:
        raise RuntimeError("expanded backend classification sets are invalid")
    event_only = ids - DIRECT - COMPOSITE
    if len(event_only) != 71 or (DIRECT | COMPOSITE | event_only) != ids:
        raise RuntimeError("expanded backend classification is not a complete partition")
    mappings = []
    for row in rows:
        rid = row["responsibility_id"]
        if rid in DIRECT:
            status = "DIRECT_ADAPTER_CANDIDATE"
            gap = "Backend is not installed/probed in V11; contract and replay gates remain required."
        elif rid in COMPOSITE:
            status = "COMPOSITE_OR_THIN_EXTENSION"
            gap = "No single backend provides the full state/action/dynamics/evaluator tuple."
        else:
            status = "EVENT_OR_LOGIC_ONLY_NOT_PHYSICALLY_SATISFIED"
            gap = "Available systems provide rules, notifications or traces but no credible complete physical process."
        primary, supporting = candidates(rid, status) if status != "EVENT_OR_LOGIC_ONLY_NOT_PHYSICALLY_SATISFIED" else ([], [])
        mappings.append({
            "responsibility_id": rid,
            "natural_query": row["natural_query"],
            "family": row["family"],
            "potential_status": status,
            "primary_backends": primary,
            "supporting_backends": supporting,
            "not_backend_satisfied": status == "EVENT_OR_LOGIC_ONLY_NOT_PHYSICALLY_SATISFIED",
            "remaining_gap": gap,
            "currently_v11_verified": rid in {"rd_37104b57370a", "rd_94d666a58c83"},
        })
    counts = {status: sum(x["potential_status"] == status for x in mappings) for status in (
        "DIRECT_ADAPTER_CANDIDATE", "COMPOSITE_OR_THIN_EXTENSION", "EVENT_OR_LOGIC_ONLY_NOT_PHYSICALLY_SATISFIED"
    )}
    artifact = {
        "schema_version": "expanded-backend-survey-v1",
        "research_status": "SEARCH_EVIDENCE_ONLY_NOT_INSTALLED_OR_REPLAY_VERIFIED",
        "claim_boundary": "Potential coverage is not benchmark support. A responsibility becomes supported only after adapter probing, contract binding, causal replay and evaluator validation.",
        "summary": {
            "responsibility_count": len(mappings),
            "current_v11_verified_responsibilities": 2,
            "potential_direct_adapter_responsibilities": counts["DIRECT_ADAPTER_CANDIDATE"],
            "potential_composite_or_extension_responsibilities": counts["COMPOSITE_OR_THIN_EXTENSION"],
            "potential_physical_path_total": counts["DIRECT_ADAPTER_CANDIDATE"] + counts["COMPOSITE_OR_THIN_EXTENSION"],
            "event_or_logic_only_not_satisfied": counts["EVENT_OR_LOGIC_ONLY_NOT_PHYSICALLY_SATISFIED"],
            "potential_physical_path_fraction": round((counts["DIRECT_ADAPTER_CANDIDATE"] + counts["COMPOSITE_OR_THIN_EXTENSION"]) / len(mappings), 4),
        },
        "candidate_backend_evidence": [
            {"backend": "EnergyPlus", "backend_role": "physical", "supports": "building energy, HVAC, ventilation, lighting and selected water-use models", "url": "https://energyplus.net/", "search_source": "duckduckgo"},
            {"backend": "OpenStudio", "supports": "open-source EnergyPlus workflow and Radiance daylight analysis", "url": "https://openstudio.net/", "search_source": "duckduckgo"},
            {"backend": "BOPTEST", "backend_role": "physical_control_testbed", "supports": "standardized building-control sensor/actuator API and KPI evaluation for its released building test cases; not a general lighting or household-task backend", "url": "https://ibpsa.github.io/project1-boptest/", "search_source": "project_official"},
            {"backend": "GridLAB-D", "backend_role": "physical", "supports": "time-series distribution, residential load and distributed-energy dynamics", "url": "https://gridlab-d.readthedocs.io/en/latest/docs/1.0%20-%20Prospective%20Users/Technical_Overview/", "search_source": "project_official"},
            {"backend": "FDS", "backend_role": "physical", "supports": "fire-driven fluid flow, heat, smoke and combustion-product transport", "url": "https://pages.nist.gov/fds-smv/", "search_source": "project_official"},
            {"backend": "EPANET", "backend_role": "physical", "supports": "pressurized water-distribution hydraulic and water-quality simulation; household fixture geometry still requires adaptation", "url": "https://www.epa.gov/water-research/epanet", "search_source": "project_official"},
            {"backend": "BEHAVIOR-1K/OmniGibson", "backend_role": "physical_embodied", "supports": "1,000 human-centered everyday household activities in realistic simulation", "url": "https://arxiv.org/abs/2403.09227", "search_source": "arxiv"},
            {"backend": "VirtualHome", "supports": "household activities represented as executable programs", "url": "https://arxiv.org/abs/1806.07011", "search_source": "arxiv"},
            {"backend": "GreenLight-Gym", "supports": "open-source dynamic greenhouse production control", "url": "https://arxiv.org/abs/2410.05336", "search_source": "arxiv"},
            {"backend": "AquaCrop-OSPy", "supports": "open crop-water and irrigation dynamics", "url": "https://aquacropos.github.io/aquacrop/", "search_source": "duckduckgo"},
            {"backend": "AI2-THOR/iGibson", "supports": "interactive household scenes and object interaction", "url": "https://arxiv.org/abs/1712.05474", "search_source": "arxiv"},
            {"backend": "Home Assistant/openHAB", "backend_role": "event_bridge", "supports": "device integration and automation execution only; not a standalone physical backend", "url": "https://www.home-assistant.io/", "search_source": "duckduckgo"},
        ],
        "mappings": mappings,
    }
    report = "\n".join([
        "# Expanded Backend Survey v1", "",
        "This survey estimates credible backend paths beyond the currently installed V11 adapters. It is not a support or Episode-release claim.", "",
        "## Result", "",
        f"- Current replay-verified responsibilities: 2 / {len(mappings)}",
        f"- Direct adapter candidates: {counts['DIRECT_ADAPTER_CANDIDATE']}",
        f"- Composite/thin-extension candidates: {counts['COMPOSITE_OR_THIN_EXTENSION']}",
        f"- Credible physical path total: {artifact['summary']['potential_physical_path_total']} / {len(mappings)} ({artifact['summary']['potential_physical_path_fraction']:.1%})",
        f"- Event/rule/trace only and therefore not physically satisfied: {counts['EVENT_OR_LOGIC_ONLY_NOT_PHYSICALLY_SATISFIED']}", "",
        "## Recommended integration order", "",
        "1. EnergyPlus/OpenStudio: largest direct gain in thermal, humidity, ventilation, lighting and occupancy-conditioned energy; use BOPTEST mainly for HVAC/building-control test cases.",
        "2. BEHAVIOR-1K/OmniGibson: embodied cleaning, laundry, rubbish and meal-preparation processes.",
        "3. GreenLight/AquaCrop: soil moisture and irrigation.",
        "4. FDS/EPANET/GridLAB-D composites: high-risk safety and whole-home energy, kept as separate tracks.", "",
        "Home Assistant/openHAB and CASAS-like traces are event/context bridges only; they cannot by themselves satisfy the benchmark's physical-process gate.", "",
    ])
    return artifact, report


def main() -> None:
    artifact, report = build()
    OUTPUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT.write_text(report, encoding="utf-8")


if __name__ == "__main__":
    main()
