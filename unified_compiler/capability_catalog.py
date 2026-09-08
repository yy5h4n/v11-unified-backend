from __future__ import annotations

from .types import BackendCapability, CapabilityStatus

CITYLEARN_2_5_0_REF = "external://citylearn@2.5.0"

CITYLEARN_DHW_STORAGE = BackendCapability(
    capability_id="citylearn.dhw_storage",
    backend="citylearn",
    provides=("storage.soc", "storage.charge_discharge_action", "dhw.demand_profile"),
    status=CapabilityStatus.API_VERIFIED_NOT_DATA_PROBED,
    evidence=(
        f"{CITYLEARN_2_5_0_REF}/building.py#L39 dhw_storage : StorageTank",
        f"{CITYLEARN_2_5_0_REF}/building.py#L86-L97 dhw_storage constructor param and assignment",
        f"{CITYLEARN_2_5_0_REF}/energy_model.py#L805 class StorageTank(StorageDevice)",
    ),
)

CITYLEARN_ELECTRICAL_STORAGE = BackendCapability(
    capability_id="citylearn.electrical_storage",
    backend="citylearn",
    provides=("storage.soc", "storage.charge_discharge_action"),
    status=CapabilityStatus.API_VERIFIED_NOT_DATA_PROBED,
    evidence=(
        f"{CITYLEARN_2_5_0_REF}/building.py#L45 electrical_storage : Battery",
        f"{CITYLEARN_2_5_0_REF}/building.py#L87-L100 electrical_storage constructor param and assignment",
        f"{CITYLEARN_2_5_0_REF}/energy_model.py#L872 class Battery(StorageDevice, ElectricDevice)",
    ),
)

CITYLEARN_SOLAR_GENERATION = BackendCapability(
    capability_id="citylearn.solar_generation",
    backend="citylearn",
    provides=("pv.generation_profile",),
    status=CapabilityStatus.API_VERIFIED_NOT_DATA_PROBED,
    evidence=(
        f"{CITYLEARN_2_5_0_REF}/building.py#L628 property solar_generation",
        f"{CITYLEARN_2_5_0_REF}/building.py#L327-L330 net_electricity_consumption uses solar_generation",
        f"{CITYLEARN_2_5_0_REF}/energy_model.py#L452 class PV(ElectricDevice)",
    ),
)

CITYLEARN_THERMAL_STORAGE = BackendCapability(
    capability_id="citylearn.thermal_storage",
    backend="citylearn",
    provides=("storage.soc", "storage.charge_discharge_action", "thermal.cooling_load"),
    status=CapabilityStatus.API_VERIFIED_NOT_DATA_PROBED,
    evidence=(
        f"{CITYLEARN_2_5_0_REF}/building.py#L87 cooling_storage/heating_storage constructor params",
        f"{CITYLEARN_2_5_0_REF}/energy_model.py#L805 class StorageTank(StorageDevice)",
    ),
)

LEGACY_HVAC = BackendCapability(
    capability_id="legacy.hvac",
    backend="legacy_prototypes",
    provides=("thermal.zone_temperature", "thermal.hvac_action"),
    status=CapabilityStatus.LEGACY_EXECUTABLE_PENDING_MIGRATION,
    evidence=(
        "prototypes/v10_diversity_aware_compiler/DESIGN.md",
        "prototypes/v9_scaled_responsibility_dataset/responsibility_catalog.json",
    ),
)

LEGACY_EV = BackendCapability(
    capability_id="legacy.ev",
    backend="legacy_prototypes",
    provides=("ev.soc", "ev.charge_action"),
    status=CapabilityStatus.LEGACY_EXECUTABLE_PENDING_MIGRATION,
    evidence=(
        "prototypes/v4_ev_charging",
        "prototypes/v6_ev2gym_miner",
    ),
)

STATIC_CAPABILITIES: tuple[BackendCapability, ...] = (
    CITYLEARN_DHW_STORAGE,
    CITYLEARN_ELECTRICAL_STORAGE,
    CITYLEARN_SOLAR_GENERATION,
    CITYLEARN_THERMAL_STORAGE,
    LEGACY_HVAC,
    LEGACY_EV,
)

_UNVERIFIED_DOMAINS: tuple[str, ...] = (
    "indoor_air_quality",
    "humidity",
    "daylighting",
    "appliances",
    "refrigeration",
    "robotics",
)

UNVERIFIED_DOMAIN_STATUS: dict[str, CapabilityStatus] = {
    domain: CapabilityStatus.CAPABILITY_UNVERIFIED for domain in _UNVERIFIED_DOMAINS
}


def default_capabilities() -> tuple[BackendCapability, ...]:
    return STATIC_CAPABILITIES
