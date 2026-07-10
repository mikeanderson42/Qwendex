import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_script_module(name):
    module_path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_tool_envelope_policy_classifies_and_suppresses_visible_markup():
    policy = load_script_module("local_qwen_tool_envelope")

    classified = policy.classify_visible_tool_markup(
        "I will run <function=exec_command><parameter=cmd>git status</parameter></function>"
    )

    assert classified.present is True
    assert classified.marker == "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED"
    assert classified.family == "xml_function"
    assert policy.suppress_visible_tool_markup("normal answer") == "normal answer"
    assert policy.suppress_visible_tool_markup("<tool_call>{}</tool_call>", parsed_tool_call=True) == ""
    assert "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED" in policy.suppress_visible_tool_markup(
        "<tool_call>{}</tool_call>"
    )


def test_tool_envelope_policy_formats_suppressed_exec_marker_with_safe_quotes():
    policy = load_script_module("local_qwen_tool_envelope")

    command = policy.suppressed_exec_marker("LOCAL_MODEL_TOOL_CALL_TRUNCATED: don't retry")

    assert command.startswith("printf '%s\\n' 'LOCAL_MODEL_TOOL_CALL_TRUNCATED:")
    assert "'\"'\"'" in command


def test_response_shaping_collapses_repeated_text_and_builds_responses():
    shaping = load_script_module("local_qwen_response_shaping")
    repeated = "One long enough final answer line for collapse.\nTwo long enough final answer line.\n"
    text = repeated + repeated

    collapsed = shaping.collapse_repeated_final_text(text)
    payload = shaping.response_payload_with_message(text, model="qwen-local")
    call_payload = shaping.response_payload_with_function_call(
        {"type": "function_call", "name": "exec_command", "arguments": "{}"},
        model="qwen-local",
    )

    assert collapsed == repeated.rstrip()
    assert payload["status"] == "completed"
    assert payload["usage"] == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    assert payload["output"][0]["content"][0]["text"] == repeated.rstrip()
    assert call_payload["output"][0]["name"] == "exec_command"


def test_bridge_status_payload_is_constructed_from_policy_module():
    status = load_script_module("local_qwen_bridge_status")
    guard = load_script_module("local_qwen_runtime_guard")
    config = guard.GuardConfig(profile="balanced", turn_tool_call_cap=17)

    payload = status.build_status_payload(
        version="test-version",
        runtime_guard=config,
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
        turn_tool_call_cap=17,
        global_duplicate_tool_call_threshold=6,
        alternating_tool_call_pattern_cycles=3,
        shell_command_stagnation_threshold=8,
        upstream_timeout_seconds=600,
        synthetic_response_handlers=["exact_helper_completion"],
    )

    assert payload["schema_version"] == "qwendex.responses_bridge.status.v1"
    assert payload["status"] == "ok"
    assert payload["version"] == "test-version"
    assert payload["context_limit_tokens"] == 65536
    assert payload["effective_thinking_budget"] == 0
    assert payload["runtime_guard_version"] == "local-qwen-runtime-guard-v1"
    assert payload["guard_thresholds"]["turn_tool_call_cap"] == 17
    assert payload["synthetic_response_handlers"] == ["exact_helper_completion"]


def test_document_section_recovery_parses_terminal_receipt_and_finalize_call():
    recovery = load_script_module("local_qwen_document_section_recovery")

    events = recovery.parse_section_upsert_progress_events(
        "ITEM_3_ALREADY_PRESENT docs/example.md "
        "bytes=13444 action=already_present next_item=None\n"
    )
    final_answer = recovery.terminal_section_upsert_final_answer(events)
    call = recovery.section_upsert_finalize_tool_call(
        json.dumps({"cmd": "python3 helper.py", "workdir": "/tmp/demo"}),
        index=2,
    )

    assert events == [
        {
            "marker": "ITEM_3_ALREADY_PRESENT",
            "file": "docs/example.md",
            "bytes": 13444,
            "action": "already_present",
            "next_item": None,
        }
    ]
    assert "mechanically complete" in final_answer
    assert call["call_id"] == "call_section_upsert_finalize_3"
    assert json.loads(call["arguments"])["workdir"] == "/tmp/demo"
