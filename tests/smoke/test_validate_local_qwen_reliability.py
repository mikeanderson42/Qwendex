import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def response_body(text: str) -> str:
    return __import__("json").dumps(
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ],
        }
    )


def load_validator():
    module_path = ROOT / "scripts" / "validate_local_qwen_reliability.py"
    spec = importlib.util.spec_from_file_location("validate_local_qwen_reliability_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_exact_marker_probe_uses_conservative_output_budget(monkeypatch):
    validator = load_validator()
    seen_payloads = []

    def fake_request_text(url, *, payload, timeout):
        seen_payloads.append((url, payload, timeout))
        if payload["max_output_tokens"] < 64:
            return 200, "application/json", ""
        return 200, "application/json", response_body("QWENDEX_OK")

    monkeypatch.setattr(validator, "request_text", fake_request_text)

    result = validator.probe_exact_marker("http://bridge.local/")

    assert result.success is True
    assert seen_payloads
    assert seen_payloads[0][0] == "http://bridge.local/v1/responses"
    assert seen_payloads[0][1]["max_output_tokens"] == 64
    assert seen_payloads[0][2] == 60
    assert result.details["exact_match"] is True
    assert result.details["parse_mode"] == "json"


def test_exact_marker_requires_exact_assistant_output(monkeypatch):
    validator = load_validator()

    for invalid in (
        "not QWENDEX_OK",
        "QWENDEX_OK extra",
        "input echo: Reply exactly QWENDEX_OK",
        "LOCAL_MODEL_LOOP_DETECTED QWENDEX_OK",
    ):
        monkeypatch.setattr(
            validator,
            "request_text",
            lambda *args, value=invalid, **kwargs: (
                200,
                "application/json",
                response_body(value),
            ),
        )
        assert validator.probe_exact_marker("http://bridge.local/").success is False


def test_exact_marker_parses_sse_completed_response(monkeypatch):
    validator = load_validator()
    completed = {
        "type": "response.completed",
        "response": __import__("json").loads(response_body("QWENDEX_OK")),
    }
    raw = "data: " + __import__("json").dumps(completed) + "\n\ndata: [DONE]\n\n"
    monkeypatch.setattr(
        validator,
        "request_text",
        lambda *args, **kwargs: (200, "text/event-stream", raw),
    )

    result = validator.probe_exact_marker("http://bridge.local/")

    assert result.success is True
    assert result.details["parse_mode"] == "sse_completed"


def test_run_uses_stubbed_requests_without_live_bridge(monkeypatch):
    validator = load_validator()

    def fake_request_json(url, *, payload=None, timeout):
        assert payload is None
        assert url == "http://bridge.local/v1/models"
        assert timeout == 5
        return 200, {"data": [{"id": "qwen-local"}]}

    def fake_request_text(url, *, payload, timeout):
        assert url == "http://bridge.local/v1/responses"
        assert payload["max_output_tokens"] == 64
        assert timeout == 60
        return 200, "application/json", response_body("QWENDEX_OK")

    monkeypatch.setattr(validator, "request_json", fake_request_json)
    monkeypatch.setattr(validator, "request_text", fake_request_text)

    payload = validator.run(base_url="http://bridge.local/", require_live_bridge=True)

    assert payload["status"] == "pass"
    assert [probe["name"] for probe in payload["probes"]] == ["models_endpoint", "exact_marker"]
