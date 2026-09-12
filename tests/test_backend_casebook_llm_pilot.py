from tools.run_backend_casebook_llm_pilot import parse_action, decision_points, summarize_model_usage


def test_parse_native_action_envelope():
    assert parse_action('<answer>{"action": 0.5}</answer>') == 0.5
    assert parse_action('{"action":{"a":1}}') == {"a": 1}


def test_decision_points_cover_beginning_and_hold_to_end():
    assert decision_points(24, 8, "continuous") == [0, 3, 7, 10, 13, 16, 20, 23]
    assert decision_points(4, 8, "fds_smoke_fire") == [0, 1, 2, 3]


def test_usage_names_are_not_collapsed():
    result = summarize_model_usage([
        {"usage": {"prompt_tokens": 10, "completion_tokens": 2, "cache_read_tokens": 4}, "latency_ms": 5, "request_bytes": 30, "response_bytes": 4},
        {"usage": {"prompt_tokens": 20, "completion_tokens": 3, "cache_read_tokens": 7}, "latency_ms": 6, "request_bytes": 40, "response_bytes": 5},
    ])
    assert result["prompt_tokens"] == 30
    assert result["completion_tokens"] == 5
    assert result["cache_read_tokens"] == 11
    assert result["calls"] == 2


def test_unknown_measurements_are_not_zero():
    result = summarize_model_usage([{'response_bytes': 12}, {}])
    assert result['response_bytes_total'] is None
    assert result['response_bytes_known_subtotal'] == 12
    assert result['response_bytes_available_calls'] == 1
    assert result['latency_ms_total'] is None
    assert summarize_model_usage([])['response_bytes_total'] == 0


def test_client_measures_body_bytes_and_retains_finish_reason(monkeypatch):
    import io
    import json
    from tools import run_backend_casebook_llm_pilot as module
    raw = json.dumps({'choices': [{'message': {'content': 'example'}, 'finish_reason': 'length'}], 'usage': {}}).encode()
    monkeypatch.setattr(module, 'urlopen', lambda request, timeout: io.BytesIO(raw))
    result = module.ChatClient('https://example.invalid', 'test', 'unit-key', retries=0).complete([])
    assert result['response_bytes'] == len(raw)
    assert result['finish_reason'] == 'length'
