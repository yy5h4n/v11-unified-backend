import json
from pathlib import Path

from tools.prepare_evidence_query_agent_packet import build_packet


ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "generated/query_construction_v2"
BATCH = DIR / "core_transfer_batch_v1.json"
DIAGNOSTIC = DIR / "core_transfer_thermal_native_v1.json"
SAVED = DIR / "core_transfer_agent_packet_v1.json"


def test_saved_agent_packet_is_reproducible_and_not_a_model_run():
    expected = build_packet(BATCH, "core-transfer-multiroom-comfort-01", DIAGNOSTIC)
    saved = json.loads(SAVED.read_text())
    assert saved == expected
    assert saved["provider_calls"] == 0
    assert saved["execution_status"] == "not_authorized_not_run"


def test_agent_sees_same_query_contract_schema_and_initial_state():
    packet = json.loads(SAVED.read_text())
    payload = json.loads(packet["query_payload"])
    assert payload["user_responsibility"] == packet["query"]
    assert payload["public_evaluation_conditions"] == packet["public_contract"]
    assert packet["snapshot"]["legal_actions"] == {
        "name": "radiator_valve", "legal_range": [0.0, 1.0], "unit": "1",
    }
    messages = packet["model_messages_at_first_call"]
    assert [message["role"] for message in messages] == ["system", "user"]
    system = json.loads(messages[0]["content"])
    assert system["decision_wait_protocol"]["semantics"]
    assert system["public_observation_contract"]
    assert "NATIVE_ACTION_OR_NULL" in system["response_format"]
    assert "public_evaluation_conditions" in messages[1]["content"]
