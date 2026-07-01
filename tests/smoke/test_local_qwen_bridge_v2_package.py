from __future__ import annotations

import importlib
import json
from pathlib import Path

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
        "exec_command(cmd=\"printf ok\", workdir=\"/tmp\")"
    )
    gemma_calls, gemma_text = parsing.parse_tool_calls(
        "<|tool_call>exec_command(cmd=\"printf gemma\")<tool_call|>"
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

    chat = responses.responses_payload_to_chat({"model": "qwen-local", "input": "Reply OK."})
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

    assert "exact_helper_completion" in synthetic.handler_names()
    assert payload["bridge_package_version"] == "local-qwen-bridge-v2"
    assert payload["synthetic_response_handlers"] == synthetic.handler_names()


def test_proxy_file_is_thin_package_facade() -> None:
    proxy = ROOT / "scripts" / "tabbyapi_responses_proxy.py"
    text = proxy.read_text(encoding="utf-8")

    assert "from local_qwen_bridge.server import *" in text
    assert "from local_qwen_bridge.server import main" in text
    assert len(text.splitlines()) <= 40
