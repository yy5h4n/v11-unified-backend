"""One provider-independent wire format for all native action shapes.

This module never selects, clamps, retries or evaluates a control action.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import json
from typing import Any

from .llm_conversation import (
    CompactObservationConversation, ConversationProtocolError, canonical_json,
    canonical_assistant_action, parse_assistant_action, public_action_result_from_receipt,
)
from .route_registry import PUBLIC_ROUTE_IDS, canonical_route_id, route_metadata
from .public_receipt import public_native_result
from .observation_contracts import observation_contract


ACTION_SEMANTICS = {
    "d0_exogenous_context": "One device command or {kind:'wait'} per native step. Wait issues no command; device state may still change through external events. Do not substitute an off command for waiting.",
    "d1_discrete_device_fault": "Commands are attempted now; running device processes and installed rules continue. An empty commands list advances one 60-second step without a new device command. Current device failure is execution feedback, not success. Rules are checked again when fired.",
    "d1_sustaingym_fault": "Submit the complete per-zone numeric vector for the next interval. Negative is cooling; permitted range and zero-required zones come from the schema. Zero means no requested HVAC power, not maintaining temperature. Positive heating is unavailable unless explicitly enabled in the schema.",
    "d1_citylearn_battery_fault": "Scalar fraction in [-1,1]: positive charges, negative discharges, zero requests neither. Submit every interval. Device faults may reduce or prevent actual delivery; check observed battery state.",
    "d1_ev2gym_fault": "SET_CHARGE_POWER requests kw for the next interval. WAIT retains the previous requested charging power (zero before any request); WAIT is not necessarily zero charging. Actual output depends on native vehicle and charger state.",
    "energyplus_iaq": "Scalar ventilation schedule fraction [0,1] for the next interval. Zero requests ventilation off; it does not mean hold the current air quality. Physical state evolves inside each interval.",
    "wntr_residential_water": "Scalar 0 closes the isolation valve, 1 opens it. Supply, tank and hydraulic state continue to evolve. Closing is not a general no-op. Pressure is metres of water head; flow is m3/s.",
    "fds_smoke_fire": "Scalar 0 closes the door, 1 opens it. The setting is held over the interval. Each step runs real full-history FDS prefix replay; this is not a persistent online FDS session. Temperature is Celsius and visibility is metres.",
    "modelica_buildings_aixlib": "Scalar radiator-valve fraction [0,1] held over the next interval. Zero closes the valve rather than freezing temperature. Rooms continue evolving through native FMI substeps.",
    "d3_citylearn_multi_system": "Submit battery_rate and hvac_rate each interval, both in [-1,1]. Battery positive charges and negative discharges. HVAC negative requests cooling, positive requests heating, zero requests neither; magnitude is normalized device input, not a temperature setpoint. Two channels do not imply verified cross-channel resource competition.",
    "d3_citylearn_multibuilding_competition": "Submit both native building IDs, each with battery_rate and hvac_rate. Battery positive charges and negative discharges; HVAC negative cools, positive heats, zero requests neither. Read native schema bounds. The shared-meter limit is an external budget over native energy, not native clipping. feasible_headroom_kwh is budget headroom, not physically allocated energy.",
    "d3_wntr_water_competition": "Submit both binary service-valve commands, 0 closed or 1 open. Tank and hydraulic state evolve continuously. Native leak/emitter flow is exposed as served_* in this prototype; it is not yet calibrated household demand satisfaction. Pressure is metres of water head and flow is m3/s.",
    "d3_modelica_shared_heat": "Submit both request fractions [0,1]. Full scales and the shared heat-pump capacity are published in the schema. When over-requested, the native model allocates proportionally. Requests are not delivered heat; read allocated_*_heat_w and temperatures.",
    "d3_ev2gym_electric_competition": "Submit both port request fractions [0,1] each interval. Zero requests no charging. Read connected vehicles and actual port power. Transformer headroom and overload are native shared-constraint signals; do not assume overload automatically clips port power. Departure results appear in action feedback.",
    "d3_energyplus_shared_ventilation": "Submit both airflow request fractions [0,1]. Native EnergyPlus EMS allocates the shared capacity; requested and actual airflow can differ. Observe actual m3/s, CO2 ppm, relative humidity percent and temperature Celsius.",
}
assert set(ACTION_SEMANTICS) == set(PUBLIC_ROUTE_IDS)


def encode_native_action(action: Any) -> str:
    return canonical_assistant_action({"action": action})["content"]


def decode_native_action(content: str) -> Any:
    if not isinstance(content, str):
        raise ConversationProtocolError("response must be text")
    text = content.strip()
    if not text.startswith("<answer>") or not text.endswith("</answer>"):
        raise ConversationProtocolError('reply exactly <answer>{"action": NATIVE_ACTION}</answer>')
    document = parse_assistant_action({"role": "assistant", "content": text})
    if set(document) != {"action"}:
        raise ConversationProtocolError("response must contain exactly action")
    canonical_json(document)  # Reject overflow such as 1e999, not just NaN tokens.
    return deepcopy(document["action"])


def build_native_conversation(route_id: str, *, query: str,
                              initial_observation: Mapping[str, Any],
                              legal_actions: Mapping[str, Any], example_action: Any,
                              initial_time_seconds: float = 0.,
                              runtime_limits: Mapping[str, Any] | None = None,
                              autonomous_wait: bool = False) -> CompactObservationConversation:
    route_id = canonical_route_id(route_id)
    metadata = route_metadata(route_id)
    system = {
        "route_id": route_id,
        "receipt_policy": "public-native-feedback-v1; native rewards, vendor diagnostic info and private identifiers are not model feedback",
        "role": "native_environment_controller",
        "response_format": 'Reply exactly <answer>{"action": NATIVE_ACTION}</answer>. No other fields or prose.',
        "example_is_format_only_not_a_recommended_policy": encode_native_action(example_action),
        "action_semantics": ACTION_SEMANTICS[route_id],
        "public_observation_contract": observation_contract(route_id),
        "time_protocol": {"cadence_seconds": metadata["cadence_seconds"], "declared_horizon_seconds": metadata["horizon_seconds"],
                          "initial_elapsed_seconds": initial_time_seconds, "clock_pauses_during_model_inference": True},
        "execution_rules": [
            "Use the public schema, not guessed action channels. The whole native action is inside the single action field, whether it is a scalar, array or object.",
            "An action requests execution, not a successful outcome. Check the next observation and action feedback.",
            "Missing fields in a delta are unchanged. Missing availability in the full schema/state is unspecified, not proof the device is healthy.",
            "No private future event schedules or evaluator answers are supplied. Public forecasts are only those explicitly present in the observation.",
            "No automatic action repair, control retry or environment reset is performed by this codec. A controller stops on terminal feedback.",
        ],
    }
    if runtime_limits is not None:
        system['runtime_limits'] = deepcopy(dict(runtime_limits))
    if autonomous_wait:
        from .decision_wait import WAIT_PROTOCOL
        system['decision_wait_protocol'] = deepcopy(WAIT_PROTOCOL)
        system['response_format'] = WAIT_PROTOCOL['response_format']
        system['example_is_format_only_not_a_recommended_policy'] = canonical_assistant_action({
            'action': example_action, 'wait': {'mode': 'for', 'duration_seconds': metadata['cadence_seconds']}})['content']
    if route_id in {"d1_citylearn_battery_fault", "d3_citylearn_multi_system", "d3_citylearn_multibuilding_competition"}:
        system["observation_time_semantics"] = (
            "After an action, battery SOC and controlled indoor temperature are the last completed native state; "
            "energy-consumption fields describe the completed interval, not a prediction of the next interval. "
            "Clock/source row, weather, exogenous load and setpoints are the next decision's available inputs. "
            "Do not combine next-interval load with previous-interval energy to check an energy balance; "
            "use quantities from the same completed effect record."
        )
    return CompactObservationConversation(canonical_json(system), query, {}, initial_observation,
                                          public_action_schema=legal_actions)


def append_native_transition(conversation: CompactObservationConversation, action: Any,
                             receipt: Mapping[str, Any]) -> None:
    if canonical_json(receipt.get("action")) != canonical_json(action):
        raise ConversationProtocolError("executed receipt action differs from submitted native action")
    if not isinstance(receipt.get("observation"), Mapping):
        raise ConversationProtocolError("receipt requires a public observation")
    route_id = json.loads(conversation.system_content).get("route_id")
    feedback = public_native_result(route_id, receipt)
    canonical_json(receipt["observation"])
    canonical_json(feedback)
    conversation.append_action({"action": deepcopy(action)})
    conversation.append_environment(feedback, receipt["observation"])
