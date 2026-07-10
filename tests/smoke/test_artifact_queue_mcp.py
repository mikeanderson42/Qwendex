from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SERVER = ROOT / "scripts" / "artifact_queue_mcp.py"
SECTION_HELPER = ROOT / "scripts" / "local_harness_document_section_upsert.py"


def run_mcp(messages: list[dict], trusted_root: Path) -> list[dict]:
    env = os.environ.copy()
    env["ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS"] = str(trusted_root)
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def tool_payload(response: dict) -> dict:
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


def test_mcp_instructions_start_with_actionable_codex_guidance(tmp_path: Path) -> None:
    responses = run_mcp(
        [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}],
        tmp_path,
    )

    instructions = responses[0]["result"]["instructions"]
    assert len(instructions[:512]) <= 512
    assert instructions.startswith("Use queue tools only for TASK_QUEUE.md artifact queues.")
    assert "queue_next reports blocked" in instructions[:512]
    assert "bounded outputs" in instructions[:512]


def test_mcp_queue_lifecycle(tmp_path: Path) -> None:
    target = tmp_path / "artifacts"
    responses = run_mcp(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "queue_init",
                    "arguments": {
                        "dir": str(target),
                        "items": ["one.md::First artifact", "two.md::Second artifact"],
                    },
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "queue_start", "arguments": {"dir": str(target), "file": "one.md"}},
            },
        ],
        tmp_path,
    )

    tools = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert {"queue_init", "queue_done", "search_web", "local_qwen_run_report"} <= tools
    started = tool_payload(responses[3])
    assert started["counts"]["in_progress"] == 1
    assert started["started"]["file"] == "one.md"

    (target / "one.md").write_text("# One\n\nDone.\n", encoding="utf-8")
    responses = run_mcp(
        [
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {
                    "name": "queue_done",
                    "arguments": {"dir": str(target), "file": "one.md", "min_bytes": 5},
                },
            },
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "queue_next", "arguments": {"dir": str(target)}},
            },
        ],
        tmp_path,
    )
    completed = tool_payload(responses[0])
    next_item = tool_payload(responses[1])
    assert completed["counts"]["completed"] == 1
    assert next_item["status"] == "next"
    assert next_item["next"]["file"] == "two.md"


def test_mcp_local_qwen_run_report_never_executes_repo_script(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    executed = tmp_path / "repo-script-executed"
    (scripts / "local_qwen_run_report.py").write_text(
        f"from pathlib import Path\nPath({str(executed)!r}).write_text('unsafe')\n",
        encoding="utf-8",
    )

    responses = run_mcp(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "local_qwen_run_report",
                    "arguments": {"repo": str(repo), "max_chars": 2000},
                },
            },
        ],
        tmp_path,
    )

    payload = tool_payload(responses[1])
    assert payload["schema"] == "local_harness_mcp.run_report.v1"
    assert payload["status"] == "pass"
    assert payload["repo"] == str(repo)
    assert payload["source"] == "mcp_builtin"
    assert "# Local Qwen Run Report" in payload["report"]
    assert not executed.exists()


def test_mcp_local_qwen_run_report_has_builtin_fallback(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "TASK_QUEUE.md").write_text(
        "# Task Queue\n\n- [ ] `one.md` | First artifact\n- [!] `two.md` | Missing source\n",
        encoding="utf-8",
    )

    responses = run_mcp(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "local_qwen_run_report",
                    "arguments": {"repo": str(repo), "max_chars": 2000, "timeout_seconds": 5},
                },
            },
        ],
        tmp_path,
    )

    payload = tool_payload(responses[1])
    assert payload["schema"] == "local_harness_mcp.run_report.v1"
    assert payload["status"] == "pass"
    assert payload["repo"] == str(repo)
    assert payload["source"] == "mcp_builtin"
    assert "# Local Qwen Run Report" in payload["report"]
    assert "pending=1" in payload["report"]
    assert "blocked=1" in payload["report"]


def test_explicit_trusted_root_does_not_implicitly_trust_server_cwd(
    tmp_path: Path,
) -> None:
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    responses = run_mcp(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "queue_status",
                    "arguments": {"dir": str(ROOT)},
                },
            }
        ],
        trusted,
    )

    payload = tool_payload(responses[0])
    assert responses[0]["result"]["isError"] is True
    assert "outside trusted roots" in payload["error"]


def test_search_web_rejects_non_loopback_configured_origin(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    env["ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS"] = str(tmp_path)
    env["SEARXNG_URL"] = "http://169.254.169.254"
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "search_web", "arguments": {"query": "test"}},
    }
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input=json.dumps(message) + "\n",
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )

    response = json.loads(proc.stdout)
    payload = tool_payload(response)
    assert response["result"]["isError"] is True
    assert "exact loopback" in payload["error"]


def test_search_web_schema_does_not_accept_caller_selected_base_url(
    tmp_path: Path,
) -> None:
    responses = run_mcp(
        [{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}],
        tmp_path,
    )
    search = next(
        tool for tool in responses[0]["result"]["tools"] if tool["name"] == "search_web"
    )

    assert "base_url" not in search["inputSchema"]["properties"]


def test_mcp_document_section_upsert_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    doc = repo / "docs" / "example.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Example Guide\n\n## Existing\n\nKeep this.\n", encoding="utf-8")
    args = {
        "dir": str(repo),
        "file": "docs/example.md",
        "section_title": "Workflow Steps",
        "body": "1. Inspect the input.\n2. Update the artifact.\n3. Run the verifier.",
        "item_number": 2,
        "total_items": 10,
        "min_bytes": 50,
    }

    responses = run_mcp(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "document_section_upsert", "arguments": args},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "document_section_upsert", "arguments": args},
            },
        ],
        tmp_path,
    )

    tools = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert "document_section_upsert" in tools
    first = tool_payload(responses[2])
    second = tool_payload(responses[3])
    text = doc.read_text(encoding="utf-8")

    assert first["schema"] == "local_harness_mcp.document_section_upsert.v1"
    assert first["status"] == "pass"
    assert first["action"] == "inserted"
    assert first["section"] == "Workflow Steps"
    assert first["next_actions"] == ["continue_item_update"]
    assert first["artifacts"] == [str(doc)]
    assert first["item_number"] == 2
    assert first["next_item"] == 3
    assert first["next_action"] == "continue_item_update"
    assert "## Workflow Steps" in text
    assert text.count("## Workflow Steps") == 1
    assert second["action"] == "already_present"


def test_mcp_document_section_upsert_rejects_path_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()

    responses = run_mcp(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "document_section_upsert",
                    "arguments": {
                        "dir": str(repo),
                        "file": "../outside.md",
                        "section_title": "Unsafe",
                        "body": "Do not write this.",
                    },
                },
            },
        ],
        tmp_path,
    )

    payload = tool_payload(responses[0])
    assert responses[0]["result"]["isError"] is True
    assert payload["status"] == "error"
    assert "invalid markdown file path" in payload["error"]
    assert not (tmp_path / "outside.md").exists()


def test_document_section_upsert_cli_is_idempotent(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    doc = repo / "docs" / "example.md"
    doc.parent.mkdir(parents=True)
    doc.write_text("# Example Guide\n", encoding="utf-8")
    env = os.environ.copy()
    env["ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS"] = str(tmp_path)
    args = [
        sys.executable,
        str(SECTION_HELPER),
        "--dir",
        str(repo),
        "--file",
        "docs/example.md",
        "--section-title",
        "Workflow Steps",
        "--body",
        "1. Research.\n2. Build.\n3. Verify.",
        "--item-number",
        "2",
        "--total-items",
        "10",
        "--done-marker",
        "ITEM_2_DONE",
        "--already-marker",
        "ITEM_2_ALREADY_PRESENT",
    ]

    first = subprocess.run(args, env=env, text=True, capture_output=True, timeout=10, check=True)
    second = subprocess.run(args, env=env, text=True, capture_output=True, timeout=10, check=True)
    text = doc.read_text(encoding="utf-8")

    assert first.stdout.startswith("ITEM_2_DONE ")
    assert "next_item=3" in first.stdout
    assert second.stdout.startswith("ITEM_2_ALREADY_PRESENT ")
    assert text.count("## Workflow Steps") == 1
