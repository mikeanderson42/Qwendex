from __future__ import annotations

import importlib
import io
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]


def test_bridge_v2_package_exposes_contract_modules() -> None:
    bridge = importlib.import_module("scripts.local_qwen_bridge")

    assert bridge.BRIDGE_PACKAGE_VERSION == "local-qwen-bridge-v2"
    for name in (
        "responses",
        "sse",
        "tool_parsing",
        "exec_sanitizer",
        "synthetic",
        "status",
    ):
        module = importlib.import_module(f"scripts.local_qwen_bridge.{name}")
        assert module.__name__.endswith(name)


def test_tool_parsing_module_handles_xml_python_and_gemma_fragments() -> None:
    parsing = importlib.import_module("scripts.local_qwen_bridge.tool_parsing")

    xml_calls, xml_text = parsing.parse_tool_calls(
        "<function=exec_command><parameter=cmd>git status --short</parameter></function>"
    )
    py_calls, py_text = parsing.parse_tool_calls(
        'exec_command(cmd="printf ok", workdir="/tmp")'
    )
    gemma_calls, gemma_text = parsing.parse_tool_calls(
        '<|tool_call>exec_command(cmd="printf gemma")<tool_call|>'
    )

    assert xml_text == ""
    assert py_text == ""
    assert gemma_text == ""
    assert xml_calls[0]["function"]["name"] == "exec_command"
    assert json.loads(py_calls[0]["function"]["arguments"])["workdir"] == "/tmp"
    assert json.loads(gemma_calls[0]["function"]["arguments"])["cmd"] == "printf gemma"


def test_exec_sanitizer_module_bounds_dangerous_generated_commands() -> None:
    sanitizer = importlib.import_module("scripts.local_qwen_bridge.exec_sanitizer")

    cat_args = json.loads(
        sanitizer.normalize_function_arguments(
            "exec_command",
            {"cmd": "cat docs/generated/local_llm_stack/LOCAL_LLM_STACK.md"},
        )
    )
    heredoc_args = json.loads(
        sanitizer.normalize_function_arguments(
            "exec_command",
            {"cmd": "cat > report.md <<'EOF'\n```bash\necho hi\n```\nEOF"},
        )
    )

    assert cat_args["cmd"].startswith("sed -n '1,220p'")
    assert "LOCAL_MODEL_TOOL_CALL_TOO_LARGE" in heredoc_args["cmd"]


def test_response_and_sse_modules_keep_responses_contract() -> None:
    responses = importlib.import_module("scripts.local_qwen_bridge.responses")
    sse = importlib.import_module("scripts.local_qwen_bridge.sse")

    chat = responses.responses_payload_to_chat(
        {"model": "qwen-local", "input": "Reply OK."}
    )
    response = responses.chat_completion_to_response(
        {"model": "qwen-local", "choices": [{"message": {"content": "OK"}}]},
        {"input": "Reply OK."},
    )
    event = sse.sse_event({"type": "response.completed", "response": response})

    assert chat["model"] == "qwen-local"
    assert response["status"] == "completed"
    assert response["output"][0]["content"][0]["text"] == "OK"
    assert event.startswith(b"data: ")
    assert event.endswith(b"\n\n")


def test_responses_conversion_accepts_only_requested_tools() -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")
    request = {
        "model": "qwen-local",
        "input": "Inspect the repository.",
        "parallel_tool_calls": True,
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "description": "Run one bounded command.",
                "parameters": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                    "required": ["cmd"],
                },
            }
        ],
    }
    accepted = server.chat_completion_to_response(
        {
            "id": "chat_1",
            "model": "qwen-local",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "functions.exec_command",
                                    "arguments": json.dumps(
                                        {"cmd": "git status --short"}
                                    ),
                                },
                            }
                        ]
                    }
                }
            ],
        },
        request,
    )
    rejected = server.chat_completion_to_response(
        {
            "model": "qwen-local",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {
                                    "name": "invented_writer",
                                    "arguments": "{}",
                                },
                            }
                        ]
                    }
                }
            ],
        },
        request,
    )

    accepted_call = accepted["output"][0]
    assert accepted_call["type"] == "function_call"
    assert accepted_call["name"] == "exec_command"
    assert accepted_call["status"] == "completed"
    assert accepted["parallel_tool_calls"] is True
    assert all(item["type"] != "function_call" for item in rejected["output"])
    rejected_text = rejected["output"][0]["content"][0]["text"]
    assert "not present in the request tool list" in rejected_text
    assert "LOCAL_MODEL_" not in rejected_text


def test_responses_conversion_suppresses_guard_markers_and_malformed_arguments() -> (
    None
):
    server = importlib.import_module("scripts.local_qwen_bridge.server")
    request = {
        "model": "qwen-local",
        "input": "Run one bounded command.",
        "tools": [
            {
                "type": "function",
                "name": "exec_command",
                "parameters": {
                    "type": "object",
                    "properties": {"cmd": {"type": "string"}},
                    "required": ["cmd"],
                },
            }
        ],
    }
    marked = server.chat_completion_to_response(
        {
            "model": "qwen-local",
            "choices": [
                {
                    "message": {
                        "content": "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED: internal detail"
                    }
                }
            ],
        },
        request,
    )
    malformed = server.chat_completion_to_response(
        {
            "model": "qwen-local",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "bad_call",
                                "type": "function",
                                "function": {
                                    "name": "exec_command",
                                    "arguments": "{bad-json",
                                },
                            }
                        ]
                    }
                }
            ],
        },
        request,
    )
    truncated = server.chat_completion_to_response(
        {
            "model": "qwen-local",
            "choices": [
                {
                    "message": {
                        "content": (
                            "<tool_call><function=exec_command>"
                            "<parameter=cmd>printf unsafe-partial"
                        )
                    }
                }
            ],
        },
        request,
    )
    missing_required = server.chat_completion_to_response(
        {
            "model": "qwen-local",
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {
                                "id": "missing_call",
                                "type": "function",
                                "function": {"name": "exec_command", "arguments": "{}"},
                            }
                        ]
                    }
                }
            ],
        },
        request,
    )

    marked_text = marked["output"][0]["content"][0]["text"]
    malformed_text = malformed["output"][0]["content"][0]["text"]
    truncated_text = truncated["output"][0]["content"][0]["text"]
    missing_text = missing_required["output"][0]["content"][0]["text"]
    assert "LOCAL_MODEL_" not in marked_text
    assert "internal guard diagnostic" in marked_text
    assert "malformed JSON arguments" in malformed_text
    assert all(item["type"] != "function_call" for item in malformed["output"])
    assert "LOCAL_MODEL_" not in truncated_text
    assert all(item["type"] != "function_call" for item in truncated["output"])
    assert "missing required parameters: cmd" in missing_text
    assert all(item["type"] != "function_call" for item in missing_required["output"])


def test_responses_input_groups_consecutive_function_calls() -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")
    messages = server.responses_input_to_messages(
        {
            "input": [
                {"type": "message", "role": "user", "content": "Run both checks."},
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "exec_command",
                    "arguments": {"cmd": "pwd"},
                },
                {
                    "type": "function_call",
                    "call_id": "call_2",
                    "name": "exec_command",
                    "arguments": {"cmd": "git status --short"},
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "/tmp\n",
                },
                {"type": "function_call_output", "call_id": "call_2", "output": ""},
            ]
        }
    )

    assistant = next(message for message in messages if message.get("tool_calls"))
    assert [call["id"] for call in assistant["tool_calls"]] == ["call_1", "call_2"]
    assert sum(message.get("role") == "tool" for message in messages) == 2


class FakeResponseHandler:
    def __init__(self, body: bytes = b"") -> None:
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status = 0
        self.response_headers: dict[str, str] = {}

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.response_headers[name.lower()] = value

    def end_headers(self) -> None:
        return None


def test_response_sender_honors_stream_flag() -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")
    payload = server.response_payload_with_message("OK", model="qwen-local")
    json_handler = FakeResponseHandler()
    stream_handler = FakeResponseHandler()

    server.send_responses_payload(json_handler, payload, stream=False)
    server.send_responses_payload(stream_handler, payload, stream=True)

    assert json_handler.status == 200
    assert json_handler.response_headers["content-type"] == "application/json"
    assert json.loads(json_handler.wfile.getvalue())["status"] == "completed"
    assert stream_handler.status == 200
    assert stream_handler.response_headers["content-type"] == "text/event-stream"
    events = stream_handler.wfile.getvalue()
    assert b'"type":"response.completed"' in events
    assert events.endswith(b"data: [DONE]\n\n")


def test_http_bridge_converts_upstream_tool_markup_to_responses_sse() -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            payload = {
                "id": "chat_fixture",
                "created": 1,
                "model": "qwen-local",
                "choices": [
                    {
                        "message": {
                            "content": (
                                "<tool_call><function=exec_command>"
                                "<parameter=cmd>git status --short</parameter>"
                                "</function></tool_call>"
                            )
                        }
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return None

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    old_target = server.ProxyHandler.target_base
    old_log = server.ProxyHandler.log_path
    server.ProxyHandler.target_base = f"http://127.0.0.1:{upstream.server_port}"
    server.ProxyHandler.log_path = None
    bridge = ThreadingHTTPServer(("127.0.0.1", 0), server.ProxyHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    upstream_thread.start()
    bridge_thread.start()
    try:
        payload = {
            "model": "qwen-local",
            "input": "Inspect status.",
            "stream": True,
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                }
            ],
        }
        request = Request(
            f"http://127.0.0.1:{bridge.server_port}/v1/responses",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            body = response.read().decode()
            content_type = response.headers.get("Content-Type", "")
    finally:
        bridge.shutdown()
        upstream.shutdown()
        bridge.server_close()
        upstream.server_close()
        server.ProxyHandler.target_base = old_target
        server.ProxyHandler.log_path = old_log

    assert "text/event-stream" in content_type
    assert '"type":"response.function_call_arguments.done"' in body
    assert '"name":"exec_command"' in body
    events = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: {")
    ]
    arguments_done = next(
        event
        for event in events
        if event["type"] == "response.function_call_arguments.done"
    )
    assert json.loads(arguments_done["arguments"])["cmd"] == "git status --short"
    assert "<tool_call>" not in body
    assert "LOCAL_MODEL_" not in body


def test_http_bridge_returns_structured_502_for_non_object_upstream_json() -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")

    class UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = b"[]"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return None

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    old_target = server.ProxyHandler.target_base
    old_log = server.ProxyHandler.log_path
    server.ProxyHandler.target_base = f"http://127.0.0.1:{upstream.server_port}"
    server.ProxyHandler.log_path = None
    bridge = ThreadingHTTPServer(("127.0.0.1", 0), server.ProxyHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    upstream_thread.start()
    bridge_thread.start()
    try:
        request = Request(
            f"http://127.0.0.1:{bridge.server_port}/v1/responses",
            data=json.dumps(
                {"model": "qwen-local", "input": "Hello", "stream": False}
            ).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urlopen(request, timeout=5)
        except HTTPError as exc:
            status = exc.code
            payload = json.loads(exc.read())
        else:
            raise AssertionError("non-object upstream JSON did not fail closed")
    finally:
        bridge.shutdown()
        upstream.shutdown()
        bridge.server_close()
        upstream.server_close()
        server.ProxyHandler.target_base = old_target
        server.ProxyHandler.log_path = old_log

    assert status == 502
    assert payload["error"]["type"] == "proxy_error"
    assert "expected an object" in payload["error"]["message"]


def test_request_body_parser_rejects_oversize_and_invalid_lengths() -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")
    valid_body = json.dumps({"model": "qwen-local", "input": "OK"}).encode()
    valid = FakeResponseHandler(valid_body)
    oversize = FakeResponseHandler(valid_body)
    invalid = SimpleNamespace(headers={"Content-Length": "NaN"}, rfile=io.BytesIO())

    parsed, raw = server.load_json_body(valid, max_body_bytes=len(valid_body))
    assert parsed == {"model": "qwen-local", "input": "OK"}
    assert raw == valid_body
    try:
        server.load_json_body(oversize, max_body_bytes=len(valid_body) - 1)
    except server.RequestBodyError as exc:
        assert exc.status_code == 413
    else:
        raise AssertionError("oversized request body was accepted")
    try:
        server.load_json_body(invalid, max_body_bytes=1024)
    except server.RequestBodyError as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("invalid Content-Length was accepted")


def test_launcher_and_proxy_share_bridge_protocol_version() -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")
    launcher = (ROOT / "scripts/run_local_qwen_codex.sh").read_text(encoding="utf-8")
    proxy = (ROOT / "scripts/qwendex_responses_bridge.py").read_text(encoding="utf-8")

    assert server.BRIDGE_VERSION in launcher
    assert f'BRIDGE_VERSION = "{server.BRIDGE_VERSION}"' in proxy
    server_text = (ROOT / "scripts/local_qwen_bridge/server.py").read_text(
        encoding="utf-8"
    )
    assert "raw_message_start" not in server_text
    assert "raw_message_end" not in server_text


def test_synthetic_registry_and_status_modules_are_discoverable() -> None:
    synthetic = importlib.import_module("scripts.local_qwen_bridge.synthetic")
    status = importlib.import_module("scripts.local_qwen_bridge.status")
    guard = importlib.import_module("scripts.local_qwen_runtime_guard")

    payload = status.build_status_payload(
        version="bridge-test",
        runtime_guard=guard.GuardConfig(profile="balanced"),
        target_base="http://127.0.0.1:4000",
        native_tools=True,
        system_prompt_file="",
        max_output_tokens=2048,
        context_limit_tokens=65536,
        max_forward_body_bytes=600000,
        tool_temperature=0.15,
        tool_top_p=None,
        tool_top_k=None,
        tool_min_p=None,
        tool_reasoning_effort="",
        enable_thinking=False,
        preserve_thinking=False,
        max_heredoc_command_chars=3500,
        max_exec_command_chars=8000,
        repeated_tool_call_threshold=3,
        turn_tool_call_cap=100,
        global_duplicate_tool_call_threshold=6,
        alternating_tool_call_pattern_cycles=3,
        shell_command_stagnation_threshold=8,
        upstream_timeout_seconds=600,
        synthetic_response_handlers=synthetic.handler_names(),
    )

    assert synthetic.handler_names() == ["runtime_guard"]
    assert payload["schema_version"] == "qwendex.responses_bridge.status.v1"
    assert payload["status"] == "ok"
    assert payload["bridge_package_version"] == "local-qwen-bridge-v2"
    assert payload["synthetic_response_handlers"] == synthetic.handler_names()


def test_bridge_exposes_canonical_status_and_legacy_alias() -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")
    old_target = server.ProxyHandler.target_base
    old_log = server.ProxyHandler.log_path
    server.ProxyHandler.target_base = "http://127.0.0.1:4000"
    server.ProxyHandler.log_path = None
    bridge = ThreadingHTTPServer(("127.0.0.1", 0), server.ProxyHandler)
    bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    bridge_thread.start()
    try:
        payloads = []
        for path in ("/status", "/__tabby_proxy_status"):
            with urlopen(
                f"http://127.0.0.1:{bridge.server_port}{path}", timeout=5
            ) as response:
                payloads.append(json.loads(response.read()))
    finally:
        bridge.shutdown()
        bridge.server_close()
        server.ProxyHandler.target_base = old_target
        server.ProxyHandler.log_path = old_log

    assert payloads[0]["version"] == server.BRIDGE_VERSION
    assert payloads[0]["schema_version"] == "qwendex.responses_bridge.status.v1"
    assert payloads[0]["status"] == "ok"
    assert payloads[1] == payloads[0]


def test_public_stack_status_url_matches_bridge_and_probes() -> None:
    stack = json.loads(
        (ROOT / "config/local_llm_stack/stack_manager.json").read_text(
            encoding="utf-8"
        )
    )
    bridge = next(
        service for service in stack["services"] if service["name"] == "bridge"
    )
    launcher = (ROOT / "scripts/run_local_qwen_codex.sh").read_text(encoding="utf-8")
    check = (ROOT / "scripts/check_local_llm_stack.sh").read_text(encoding="utf-8")

    assert bridge["status_url"].endswith("/status")
    assert 'curl -fsS "$base/status"' in launcher
    assert '"$CODEX_BASE/status"' in check


def test_bridge_launcher_has_no_hidden_system_prompt_dependency(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "python-args.txt"
    fake_curl = fake_bin / "curl"
    fake_python = fake_bin / "python3"
    fake_curl.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_python.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\" > \"$CAPTURE_ARGS\"\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)
    fake_python.chmod(0o755)
    clean_home = tmp_path / "home"
    clean_home.mkdir()
    env = {
        **os.environ,
        "HOME": str(clean_home),
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "CAPTURE_ARGS": str(capture),
        "CODEX_TEXTGEN_TARGET_BASE": "http://127.0.0.1:4000",
        "CODEX_TEXTGEN_SYSTEM_PROMPT_FILE": "",
        "CODEX_TEXTGEN_LOG_PATH": str(tmp_path / "bridge.jsonl"),
    }

    result = subprocess.run(
        [str(ROOT / "scripts/run_codex_textgen_bridge.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    args = capture.read_text(encoding="utf-8").splitlines()
    assert "--system-prompt-file" not in args
    assert "--target-base" in args
    assert args[args.index("--target-base") + 1] == "http://127.0.0.1:4000"


def test_local_launcher_binds_codex_to_the_verified_bridge_origin(
    tmp_path: Path,
) -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")

    class ModelsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps({"data": [{"id": "qwen-local"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return None

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), ModelsHandler)
    old_target = server.ProxyHandler.target_base
    old_log = server.ProxyHandler.log_path
    old_context = server.ProxyHandler.context_limit_tokens
    server.ProxyHandler.target_base = f"http://127.0.0.1:{upstream.server_port}"
    server.ProxyHandler.log_path = None
    server.ProxyHandler.context_limit_tokens = 65536
    bridge = ThreadingHTTPServer(("127.0.0.1", 0), server.ProxyHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    bridge_thread = threading.Thread(target=bridge.serve_forever, daemon=True)
    upstream_thread.start()
    bridge_thread.start()
    capture = tmp_path / "codex-env-and-args.txt"
    fake_codex = tmp_path / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$CODEX_OSS_BASE_URL\" > \"$CAPTURE\"\n"
        "printf '%s\\n' \"$@\" >> \"$CAPTURE\"\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)
    context = server.ProxyHandler.context_limit_tokens
    guard = server.runtime_guard_config()
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "CODEX_BIN": str(fake_codex),
        "CAPTURE": str(capture),
        "LOCAL_QWEN_BASE": f"http://127.0.0.1:{bridge.server_port}",
        "LITELLM_BASE": f"http://127.0.0.1:{upstream.server_port}",
        "LOCAL_QWEN_MODEL": "qwen-local",
        "LOCAL_QWEN_CODEX_CONTEXT_WINDOW": str(context),
        "LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT": str(min(56000, context - 1)),
        "LOCAL_QWEN_GUARD_PROFILE": guard.profile,
        "LOCAL_QWEN_CODEX_MAX_TOOL_CALLS": str(guard.run_max_tool_calls),
        "LOCAL_QWEN_CHECK_MCP_BINS": "0",
        "LOCAL_QWEN_CODEX_SKIP_GIT_REPO_CHECK": "1",
    }
    env.pop("CODEX_OSS_BASE_URL", None)
    try:
        result = subprocess.run(
            [
                str(ROOT / "scripts/run_local_qwen_codex.sh"),
                "--cwd",
                str(tmp_path),
                "--fresh-home",
                str(tmp_path / "fresh-home"),
                "--minimal",
                "--ephemeral",
                "--exec",
                "Reply OK.",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
    finally:
        bridge.shutdown()
        upstream.shutdown()
        bridge.server_close()
        upstream.server_close()
        server.ProxyHandler.target_base = old_target
        server.ProxyHandler.log_path = old_log
        server.ProxyHandler.context_limit_tokens = old_context

    assert result.returncode == 0, result.stderr
    captured = capture.read_text(encoding="utf-8").splitlines()
    assert captured[0] == f"http://127.0.0.1:{bridge.server_port}/v1"
    assert "--local-provider" in captured
    assert captured[captured.index("--local-provider") + 1] == "lmstudio"
    assert "-C" in captured
    assert captured[captured.index("-C") + 1] == str(tmp_path)


def test_local_launcher_rejects_conflicting_codex_oss_base(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "HOME": str(tmp_path),
        "LOCAL_QWEN_BASE": "http://127.0.0.1:1234",
        "CODEX_OSS_BASE_URL": "http://127.0.0.1:9999/v1",
    }

    result = subprocess.run(
        [str(ROOT / "scripts/run_local_qwen_codex.sh"), "--check"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )

    assert result.returncode == 2
    assert "conflicts with the verified Qwendex bridge" in result.stderr


def test_canonical_bridge_file_is_thin_package_facade() -> None:
    proxy = ROOT / "scripts" / "qwendex_responses_bridge.py"
    text = proxy.read_text(encoding="utf-8")

    assert "from local_qwen_bridge.server import *" in text
    assert "from local_qwen_bridge.server import main" in text
    assert len(text.splitlines()) <= 40


def test_legacy_tabby_entrypoint_is_compatibility_only() -> None:
    legacy = (ROOT / "scripts/tabbyapi_responses_proxy.py").read_text(
        encoding="utf-8"
    )
    launcher = (ROOT / "scripts/run_codex_textgen_bridge.sh").read_text(
        encoding="utf-8"
    )

    assert "Legacy compatibility entrypoint" in legacy
    assert "qwendex_responses_bridge.py" in launcher
    assert "tabbyapi_responses_proxy.py" not in launcher


def test_bridge_runtime_surface_is_protocol_focused() -> None:
    server = importlib.import_module("scripts.local_qwen_bridge.server")
    server_path = ROOT / "scripts/local_qwen_bridge/server.py"
    artifact_server = (ROOT / "scripts/artifact_queue_mcp.py").read_text(
        encoding="utf-8"
    )

    assert len(server_path.read_text(encoding="utf-8").splitlines()) < 3500
    assert server.synthetic_response_handler_names() == ["runtime_guard"]
    assert "docs/example.md" in artifact_server
