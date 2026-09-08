"""Reference policies for the deterministic household-workflow backend.

The policies only consume the public Harness view.  In particular,
``QueryConditionedReferencePolicy`` does not receive a responsibility id: it
selects a scenario through an explicit Query-to-scenario table supplied by the
release package.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Mapping


TRIGGERS: dict[str, str] = {
    "laundry_completion_notification": "laundry_cycle_finished",
    "laundry_backlog_management": "laundry_loaded",
    "fridge_door_left_open": "fridge_door_opened",
    "weekly_floor_cleaning": "weekly_cleaning_due",
    "away_intercom_notification": "visitor_rang",
    "mail_arrival_notification": "mail_delivered",
    "away_visitor_monitoring": "visitor_rang",
    "unoccupied_visitor_acknowledgement": "visitor_rang",
    "unattended_stove_guard": "stove_became_unattended",
    "departure_lockdown": "occupants_departed",
    "unoccupied_home_security": "occupants_departed",
    "failed_entry_notification": "failed_entry_attempt",
    "expected_delivery_gate_and_notice": "delivery_arrived",
    "remote_visitor_gate_access": "visitor_rang",
    "post_parking_garage_closure": "vehicle_parked",
    "bathroom_occupancy_indicator": "bathroom_occupied",
    "television_curfew": "television_curfew_started",
    "keyless_resident_entry": "resident_arrived",
    "full_bin_collection_notice": "bin_collection_due",
    "departure_key_reminder": "departure_started",
    "intended_wake_alarm": "intended_wake_time",
    "shower_music_availability": "shower_started",
    "coffee_ready_at_wake": "intended_wake_time",
    "post_use_toilet_flush": "toilet_use_finished",
    "toilet_paper_depletion_notice": "toilet_paper_depleted",
    "shower_news_delivery": "shower_started",
    "supported_emergency_call": "emergency_detected",
    "authorized_vehicle_gate_entry": "vehicle_arrived",
}


def _command(device: str, capability: str, operation: str, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "device_id": device,
        "capability": capability,
        "operation": operation,
        "parameters": parameters or {},
    }


def _notify(message: str, channel: str, recipients: list[str]) -> dict[str, Any]:
    return _command(
        "notification.service",
        "notification.send",
        "send",
        {"message": message, "channel": channel, "recipients": list(recipients)},
    )


class NoOpPolicy:
    """A deterministic baseline that never changes the household."""

    def __init__(self, wait_seconds: int | None = None):
        self.wait_seconds = wait_seconds

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        if self.wait_seconds is not None:
            duration = self.wait_seconds
        else:
            observation = view.get("observation", {})
            step = observation.get("step", 0)
            tick = observation.get("tick_seconds", 60)
            horizon = view.get("horizon_seconds", 3600)
            elapsed = step * tick if isinstance(step, int) and isinstance(tick, int) else 0
            remaining = horizon - elapsed if isinstance(horizon, int) else 3600
            duration = max(1, min(604800, remaining))
        return {"kind": "wait", "mode": "for", "duration_seconds": duration}


class WorkflowReferencePolicy:
    """Feasible, scenario-specific reference policy used for release gating."""

    def __init__(self, scenario: str, query_present: bool = True):
        self.scenario = scenario
        self.query_present = query_present
        self.handled: set[str] = set()

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        if not self.query_present:
            return NoOpPolicy().decide(view)
        obs = view["observation"]
        devices = obs["devices"]
        event_types = {event["type"] for event in obs["events"]}
        profile = {**obs.get("public_context", {}), **view.get("public_profile", {})}
        channel = profile.get("notification_channel", "app")
        recipients = profile.get("notification_recipients", ["resident.primary"])
        commands: list[dict[str, Any]] = []
        scenario = self.scenario

        if scenario in {"laundry_completion_notification", "laundry_backlog_management"}:
            state = devices["laundry.washer"]["state"]
            if state == "loaded":
                commands.append(_command("laundry.washer", "laundry.control", "start"))
            elif scenario == "laundry_completion_notification" and "laundry_cycle_finished" in event_types and "notified" not in self.handled:
                self.handled.add("notified")
                commands.append(_notify("Laundry is finished.", channel, recipients))
            elif scenario == "laundry_backlog_management" and state == "done":
                commands.append(_command("laundry.washer", "laundry.control", "unload"))
        elif scenario == "weekly_floor_cleaning" and "weekly_cleaning_due" in event_types and devices["vacuum.robot"]["state"] == "docked" and "started" not in self.handled:
            self.handled.add("started")
            commands.append(_command("vacuum.robot", "vacuum.control", "start_cleaning"))
        elif scenario in {"away_intercom_notification", "away_visitor_monitoring"} and "visitor_rang" in event_types and "notified" not in self.handled:
            self.handled.add("notified")
            commands.append(_notify("A visitor is at the door.", channel, recipients))
        elif scenario == "mail_arrival_notification" and "mail_delivered" in event_types and "notified" not in self.handled:
            self.handled.add("notified")
            commands.append(_notify("Mail has arrived.", channel, recipients))
        elif scenario == "unoccupied_visitor_acknowledgement" and "visitor_rang" in event_types:
            commands.append(_command("visitor_intercom.main", "intercom.control", "answer"))
        elif scenario == "fridge_door_left_open" and devices["fridge_door.main"]["state"] == "open":
            threshold = int(profile.get("fridge_intervention_after_seconds", 120))
            open_seconds = int(devices["fridge_door.main"].get("attributes", {}).get("open_seconds", 0))
            if open_seconds >= threshold:
                commands.append(_command("fridge_door.main", "fridge.door", "close"))
            else:
                return {"kind": "wait", "mode": "for", "duration_seconds": max(1, threshold - open_seconds)}
        elif scenario == "unattended_stove_guard" and "stove_became_unattended" in event_types and devices["stove.main"]["state"] == "on":
            commands.append(_command("stove.main", "stove.control", "extinguish"))
        elif scenario in {"departure_lockdown", "unoccupied_home_security"} and "occupants_departed" in event_types and devices["front_door_lock.main"]["state"] == "unlocked":
            commands.append(_command("front_door_lock.main", "lock.control", "lock"))
        elif scenario == "failed_entry_notification" and "failed_entry_attempt" in event_types and "notified" not in self.handled:
            self.handled.add("notified")
            commands.append(_notify("Failed entry attempts detected.", channel, recipients))
        elif scenario == "expected_delivery_gate_and_notice":
            authorized_delivery = any(
                event.get("type") == "delivery_arrived" and event.get("authorized") is True
                for event in obs.get("events", [])
            )
            if authorized_delivery and "opened" not in self.handled:
                self.handled.add("opened")
                commands.extend([
                    _command("garage_door.main", "garage.door", "open"),
                    _notify("Expected delivery admitted.", channel, recipients),
                ])
            elif "opened" in self.handled and devices["garage_door.main"]["state"] == "open" and "closed" not in self.handled:
                self.handled.add("closed")
                commands.append(_command("garage_door.main", "garage.door", "close"))
        elif scenario == "remote_visitor_gate_access":
            authorized_visitor = any(
                event.get("type") == "visitor_rang" and event.get("authorized") is True
                for event in obs.get("events", [])
            )
            if authorized_visitor and "opened" not in self.handled:
                self.handled.add("opened")
                commands.extend([
                    _command("garage_door.main", "garage.door", "open"),
                    _command("visitor_intercom.main", "intercom.control", "answer"),
                ])
            elif "opened" in self.handled and devices["garage_door.main"]["state"] == "open" and "closed" not in self.handled:
                self.handled.add("closed")
                commands.append(_command("garage_door.main", "garage.door", "close"))
        elif scenario == "post_parking_garage_closure" and (
            "vehicle_parked" in event_types or "garage_door_obstruction" in event_types or "closing_started" in self.handled
        ) and devices["garage_door.main"]["state"] == "open":
            self.handled.add("closing_started")
            commands.append(_command("garage_door.main", "garage.door", "close"))
        elif scenario == "bathroom_occupancy_indicator":
            if "bathroom_occupied" in event_types and "occupied" not in self.handled:
                self.handled.add("occupied")
                commands.append(_notify("Bathroom is occupied.", channel, recipients))
            elif "bathroom_vacated" in event_types and "vacant" not in self.handled:
                self.handled.add("vacant")
                commands.append(_notify("Bathroom is vacant.", channel, recipients))
        elif scenario == "television_curfew" and (
            "television_curfew_started" in event_types or "media_resume_attempt" in event_types
        ) and devices["media_player.main"]["state"] == "playing":
            commands.append(_command("media_player.main", "media.control", "stop"))
        elif scenario == "keyless_resident_entry" and any(
            event.get("type") == "resident_arrived" and event.get("authorized") is True
            for event in obs.get("events", [])
        ) and devices["front_door_lock.main"]["state"] == "locked":
            commands.append(_command("front_door_lock.main", "lock.control", "unlock"))
        elif scenario == "full_bin_collection_notice" and "bin_collection_due" in event_types and "notified" not in self.handled:
            self.handled.add("notified")
            commands.append(_notify("The full bin is due for collection.", channel, recipients))
        elif scenario == "departure_key_reminder" and "departure_started" in event_types and "notified" not in self.handled:
            self.handled.add("notified")
            commands.append(_notify("Take your keys before leaving.", channel, recipients))
        elif scenario == "intended_wake_alarm" and "intended_wake_time" in event_types and devices["alarm_clock.main"]["state"] == "idle":
            commands.append(_command("alarm_clock.main", "alarm.control", "ring"))
        elif scenario == "shower_music_availability":
            if "shower_started" in event_types and devices["media_player.main"]["state"] == "off" and profile.get("shower_music_opt_in") is True:
                commands.append(_command("media_player.main", "media.control", "play", {"content": profile.get("shower_music_content", "music")}))
            elif "shower_ended" in event_types and devices["media_player.main"]["state"] == "playing":
                commands.append(_command("media_player.main", "media.control", "stop"))
        elif scenario in {"pellet_supply_guard", "beer_dispenser_stock"}:
            item = "pellets" if scenario == "pellet_supply_guard" else "beer"
            device_id = f"supply.{item}"
            prefix = "pellet" if item == "pellets" else "beer"
            if devices[device_id]["state"] in {"low", "empty"} and "ordered" not in self.handled:
                self.handled.add("ordered")
                commands.append(_command(device_id, "supply.order", "place", {
                    "item": item,
                    "supplier": profile[f"{prefix}_suppliers"][0],
                    "quantity": int(profile[f"{prefix}_order_quantity"]),
                    "max_cost": profile[f"{prefix}_budget"],
                }))
        elif scenario == "coffee_ready_at_wake" and devices["coffee_maker.main"]["state"] == "idle" and "started" not in self.handled:
            now = datetime.fromisoformat(str(obs["time"]).replace("Z", "+00:00"))
            wake = datetime.fromisoformat(str(profile["intended_wake_at"]).replace("Z", "+00:00"))
            brew_seconds = int(profile.get("coffee_brew_seconds", 300))
            if now >= wake - timedelta(seconds=brew_seconds):
                self.handled.add("started")
                commands.append(_command("coffee_maker.main", "coffee.control", "start"))
            else:
                return {"kind": "wait", "mode": "until", "timestamp": (wake - timedelta(seconds=brew_seconds)).isoformat().replace("+00:00", "Z")}
        elif scenario == "post_use_toilet_flush" and "toilet_use_finished" in event_types and devices["toilet.main"]["state"] == "unflushed":
            commands.append(_command("toilet.main", "toilet.control", "flush"))
        elif scenario == "toilet_paper_depletion_notice" and "toilet_paper_depleted" in event_types and "notified" not in self.handled:
            self.handled.add("notified")
            commands.append(_notify("Toilet paper has run out.", channel, recipients))
        elif scenario == "shower_news_delivery":
            if "shower_started" in event_types and devices["media_player.main"]["state"] == "off":
                commands.append(_command("media_player.main", "media.control", "play", {"content": "news"}))
            elif "shower_ended" in event_types and devices["media_player.main"]["state"] == "playing":
                commands.append(_command("media_player.main", "media.control", "stop"))
        elif scenario == "supported_emergency_call" and any(
            event.get("type") == "emergency_detected" and event.get("supported") is True
            for event in obs.get("events", [])
        ):
            commands.append(_command("emergency_call.service", "emergency.call", "call", {"service": "emergency"}))
        elif scenario == "authorized_vehicle_gate_entry":
            authorized_vehicle = any(
                event.get("type") == "vehicle_arrived" and event.get("authorized") is True
                for event in obs.get("events", [])
            )
            if authorized_vehicle and "opened" not in self.handled and devices["garage_door.main"]["state"] == "closed":
                self.handled.add("opened")
                commands.append(_command("garage_door.main", "garage.door", "open"))
            elif "opened" in self.handled and devices["garage_door.main"]["state"] == "open" and "closed" not in self.handled:
                self.handled.add("closed")
                commands.append(_command("garage_door.main", "garage.door", "close"))

        if commands:
            return {"kind": "act", "commands": commands}
        completed = {
            "weekly_floor_cleaning": "vacuum_docked",
            "expected_delivery_gate_and_notice": "garage_door_closed",
            "remote_visitor_gate_access": "garage_door_closed",
            "post_parking_garage_closure": "garage_door_closed",
            "keyless_resident_entry": "front_door_locked",
            "shower_music_availability": "media_stopped",
            "pellet_supply_guard": "supply_delivered",
            "beer_dispenser_stock": "supply_delivered",
            "shower_news_delivery": "media_stopped",
            "authorized_vehicle_gate_entry": "garage_door_closed",
        }
        if completed.get(scenario) in event_types:
            self.handled.add("completed")
        if "completed" in self.handled:
            return NoOpPolicy().decide(view)
        trigger = TRIGGERS.get(scenario, "workflow_progress")
        if scenario == "laundry_backlog_management" and devices["laundry.washer"]["state"] == "washing":
            trigger = "laundry_cycle_finished"
        elif scenario == "bathroom_occupancy_indicator" and "occupied" in self.handled and "vacant" not in self.handled:
            trigger = "bathroom_vacated"
        elif scenario in {"shower_music_availability", "shower_news_delivery"} and devices["media_player.main"]["state"] == "playing":
            trigger = "shower_ended"
        elif scenario == "television_curfew":
            trigger = "media_resume_attempt" if "television_curfew_ended" not in event_types else "television_curfew_ended"
        elif scenario == "post_parking_garage_closure" and "closing_started" in self.handled:
            trigger = "garage_door_obstruction"
        observation = view.get("observation", {})
        elapsed = int(observation.get("step", 0)) * int(observation.get("tick_seconds", 60))
        remaining = max(1, int(view.get("horizon_seconds", 3600)) - elapsed)
        return {
            "kind": "wait",
            "mode": "until_event",
            "event_filter": {"type": trigger},
            "timeout_seconds": min(604800, remaining),
        }


class QueryConditionedReferencePolicy:
    """Reference baseline whose only scenario signal is the public Query."""

    def __init__(self, query_to_scenario: Mapping[str, str]):
        self.query_to_scenario = dict(query_to_scenario)
        self.delegate: WorkflowReferencePolicy | None = None
        self._selected_scenario: str | None = None

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        query = view.get("query")
        scenario = self.query_to_scenario.get(query) if isinstance(query, str) else None
        if scenario is None:
            return NoOpPolicy().decide(view)
        if self.delegate is None or scenario != self._selected_scenario:
            self.delegate = WorkflowReferencePolicy(scenario)
            self._selected_scenario = scenario
        return self.delegate.decide(view)


__all__ = [
    "NoOpPolicy",
    "QueryConditionedReferencePolicy",
    "TRIGGERS",
    "WorkflowReferencePolicy",
]
