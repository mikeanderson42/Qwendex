import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]


def load_stack_module():
    module_path = ROOT / "scripts" / "local_llm_stack.py"
    spec = importlib.util.spec_from_file_location("local_llm_stack_status_health_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def bridge_service(stack, *, timeout=5):
    return stack.ServiceDefinition(
        name="bridge",
        display_name="Responses bridge",
        window_name="bridge",
        provider_type="responses_bridge",
        working_dir=str(ROOT),
        command="true",
        health_url="http://127.0.0.1:7777/health",
        status_url="http://127.0.0.1:7777/status",
        port=7777,
        startup_timeout_seconds=timeout,
    )


def result(stack, url, *, ready):
    return stack.HealthCheckResult(
        state="healthy" if ready else "unhealthy",
        endpoint_responding=ready,
        port_occupied=True,
        url=url,
        status_code=200 if ready else 404,
        error="" if ready else "HTTP 404",
    )


def test_status_url_is_part_of_runtime_health_and_json_status(monkeypatch):
    stack = load_stack_module()
    service = bridge_service(stack)
    health = result(stack, service.health_url, ready=True)
    status = result(stack, service.status_url, ready=False)
    monkeypatch.setattr(stack, "http_health", lambda url, port: health if url == service.health_url else status)
    session = stack.TmuxSessionState(
        "qwendex",
        True,
        {"bridge": stack.TmuxWindowState("bridge", True, True, False, count=1)},
    )

    state = stack.service_runtime_state(service, session)

    assert state.runtime_state == "unhealthy"
    assert state.last_error == "HTTP 404"
    assert state.status_health == status
    cfg = stack.load_config(ROOT / "config" / "local_llm_stack" / "stack_manager.json")
    payload = stack.service_state_to_dict(cfg, state)
    assert payload["status_url"] == service.status_url
    assert payload["status_health"]["state"] == "unhealthy"
    assert payload["status_health"]["status_code"] == 404


def test_status_url_must_be_ready_before_external_service_is_accepted(monkeypatch):
    stack = load_stack_module()
    service = bridge_service(stack)
    health = result(stack, service.health_url, ready=True)
    status = result(stack, service.status_url, ready=False)
    monkeypatch.setattr(stack, "http_health", lambda url, port: health if url == service.health_url else status)
    session = stack.TmuxSessionState("qwendex", False, {})

    state = stack.service_runtime_state(service, session)

    assert state.runtime_state == "unhealthy"


def test_wait_for_service_requires_status_endpoint(monkeypatch):
    stack = load_stack_module()
    service = bridge_service(stack, timeout=1)
    health = result(stack, service.health_url, ready=True)
    status = result(stack, service.status_url, ready=False)
    clock = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(stack, "http_health", lambda url, port: health if url == service.health_url else status)
    monkeypatch.setattr(stack.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(stack.time, "sleep", lambda seconds: None)

    waited = stack.wait_for_service(service)

    assert waited.ok is False
    assert service.health_url in waited.message
    assert service.status_url in waited.message


def test_start_refuses_unmanaged_process_with_broken_status_endpoint(monkeypatch):
    stack = load_stack_module()
    service = bridge_service(stack)
    health = result(stack, service.health_url, ready=True)
    status = result(stack, service.status_url, ready=False)
    session = stack.TmuxSessionState("qwendex", False, {})
    state = stack.ServiceRuntimeState(
        service,
        health,
        stack.TmuxWindowState("bridge", False, False, False),
        "unhealthy",
        "HTTP 404",
        status,
    )
    monkeypatch.setattr(stack, "collect_runtime", lambda cfg: (session, [state]))

    started = stack.start_one(SimpleNamespace(services=[service]), "bridge")

    assert started.ok is False
    assert "unmanaged process with unhealthy endpoints" in started.message


def test_http_health_requires_the_canonical_status_contract():
    stack = load_stack_module()

    class StatusHandler(BaseHTTPRequestHandler):
        payload = {"status": "ok"}

        def do_GET(self):  # noqa: N802
            body = json.dumps(self.payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), StatusHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/status"
    try:
        invalid = stack.http_health(url, server.server_port)
        StatusHandler.payload = {
            "schema_version": "qwendex.responses_bridge.status.v1",
            "status": "ok",
        }
        valid = stack.http_health(url, server.server_port)
    finally:
        server.shutdown()
        server.server_close()

    assert invalid.state == "unhealthy"
    assert invalid.error == "status payload did not report ready"
    assert valid.state == "healthy"
    assert valid.error == ""
