"""Public adapter surface.

Existing exports are imported eagerly (stdlib-only modules). The four
first-wave claim-aligned adapters are exported lazily via module
``__getattr__`` so that importing this package never pulls optional heavy
backend dependencies (e.g. ``numpy``) at import time.
"""

from typing import Any

from .citylearn_shared import (
    CityLearnSharedAdapter,
    CityLearnSourceIndex,
    SourceBundle,
)
from .citylearn_battery import BatteryPoolError, CityLearnBatteryPVAdapter
from .legacy_v10 import (
    CityLearnV10Adapter,
    EV2GymV10Adapter,
    LegacyArtifactError,
    V10ArtifactIndex,
)
from .legacy_v10_runtime import (
    ReplayActionError,
    ReplayError,
    UnknownEpisodeError,
    UnknownLegacyProcessError,
    V10RuntimeBridge,
)

_LAZY_EXPORTS = {
    "D0ExogenousContextAdapter": ".d0_exogenous_context",
    "D0ContextError": ".d0_exogenous_context",
    "D0ActionError": ".d0_exogenous_context",
    "ExternalContextEvent": ".d0_exogenous_context",
    "ExogenousContextSchedule": ".d0_exogenous_context",
    "BopTestRestAdapter": ".boptest",
    "BopTestRestError": ".boptest",
    "CityLearnClaimAdapter": ".citylearn_claim",
    "EV2GymClaimAdapter": ".ev2gym_claim",
    "EV2GymClaimError": ".ev2gym_claim",
    "UnknownClaimEpisodeError": ".ev2gym_claim",
    "EV2GymFaultAdapter": ".ev2gym_fault",
    "EV2GymFaultError": ".ev2gym_fault",
    "EV2GymFaultActionError": ".ev2gym_fault",
    "EV2GymFaultTrajectory": ".ev2gym_fault",
    "ChargerFaultSchedule": ".ev2gym_fault",
    "ChargerFaultWindow": ".ev2gym_fault",
    "EV2GymChargerFaultAdapter": ".ev2gym_fault",
    "EV2GymChargerFaultError": ".ev2gym_fault",
    "EV2GymChargerFaultActionError": ".ev2gym_fault",
    "EV2GymChargerFaultTrajectory": ".ev2gym_fault",
    "SustainGymAdapterError": ".sustaingym_building",
    "SustainGymBuildingAdapter": ".sustaingym_building",
    "CityLearnBatteryFaultAdapter": ".citylearn_battery_fault",
    "CityLearnBatteryFaultError": ".citylearn_battery_fault",
    "BatteryFaultActionError": ".citylearn_battery_fault",
    "BatteryFaultWindow": ".citylearn_battery_fault",
    "BatteryFaultSchedule": ".citylearn_battery_fault",
    "BatteryHealthState": ".citylearn_battery_fault",
    "battery_fault_profile": ".citylearn_battery_fault",
    # D1 discrete household route stays lazy: importing the public adapter
    # namespace must not import the Harness V2 workflow backend (or its
    # optional runtime dependencies) unless one of these names is requested.
    "D1DiscreteActionError": ".d1_discrete_device_fault",
    "D1DiscreteFaultError": ".d1_discrete_device_fault",
    "D1DiscreteDeviceFaultAdapter": ".d1_discrete_device_fault",
    "D1DiscreteDeviceFaultBackend": ".d1_discrete_device_fault",
    "DiscreteDeviceFaultAdapter": ".d1_discrete_device_fault",
    "DiscreteDeviceFaultBackend": ".d1_discrete_device_fault",
    "DiscreteFaultSchedule": ".d1_discrete_device_fault",
    "DiscreteFaultWindow": ".d1_discrete_device_fault",
}

__all__ = [
    "BatteryPoolError",
    "D0ActionError",
    "D0ContextError",
    "D0ExogenousContextAdapter",
    "BopTestRestAdapter",
    "BopTestRestError",
    "CityLearnBatteryPVAdapter",
    "CityLearnBatteryFaultAdapter",
    "CityLearnBatteryFaultError",
    "BatteryFaultActionError",
    "BatteryFaultWindow",
    "BatteryFaultSchedule",
    "BatteryHealthState",
    "battery_fault_profile",
    "D1DiscreteActionError",
    "D1DiscreteFaultError",
    "D1DiscreteDeviceFaultAdapter",
    "D1DiscreteDeviceFaultBackend",
    "DiscreteDeviceFaultAdapter",
    "DiscreteDeviceFaultBackend",
    "DiscreteFaultSchedule",
    "DiscreteFaultWindow",
    "CityLearnClaimAdapter",
    "CityLearnSharedAdapter",
    "CityLearnSourceIndex",
    "CityLearnV10Adapter",
    "SourceBundle",
    "EV2GymClaimAdapter",
    "EV2GymClaimError",
    "EV2GymFaultActionError",
    "EV2GymFaultAdapter",
    "EV2GymFaultError",
    "EV2GymFaultTrajectory",
    "ChargerFaultSchedule",
    "ChargerFaultWindow",
    "EV2GymChargerFaultAdapter",
    "EV2GymChargerFaultError",
    "EV2GymChargerFaultActionError",
    "EV2GymChargerFaultTrajectory",
    "EV2GymV10Adapter",
    "ExternalContextEvent",
    "ExogenousContextSchedule",
    "LegacyArtifactError",
    "ReplayActionError",
    "ReplayError",
    "SustainGymAdapterError",
    "SustainGymBuildingAdapter",
    "UnknownClaimEpisodeError",
    "UnknownEpisodeError",
    "UnknownLegacyProcessError",
    "V10ArtifactIndex",
    "V10RuntimeBridge",
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(module_name, __name__)
    return getattr(module, name)
