#!/usr/bin/env python3
"""Run a small public-safe reliability probe against the local Qwendex bridge."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DEFAULT_BASE = os.environ.get("LOCAL_QWEN_BASE", os.environ.get("LOCAL_LLM_CODEX_BASE", "http://127.0.0.1:1234"))
BAD_MARKERS = (
    "LOCAL_MODEL_TOOL_CALL_TOO_LARGE",
    "LOCAL_MODEL_TOOL_CALL_TRUNCATED",
    "LOCAL_MODEL_TOOL_MARKUP_SUPPRESSED",
    "LOCAL_MODEL_LOOP_DETECTED",
    "QWENDEX_TIMEOUT",
)


@dataclass(frozen=True)
class ProbeResult:
    name: str
    success: bool
    duration_seconds: float
    details: dict[str, Any]


def request_json(url: str, *, payload: dict[str, Any] | None = None, timeout: int = 20) -> tuple[int, dict[str, Any]]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Authorization": "Bearer no-key"},
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        return response.status, json.loads(body) if body.strip() else {}


def request_text(url: str, *, payload: dict[str, Any], timeout: int = 60) -> tuple[int, str, str]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer no-key"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", errors="replace")
        return response.status, response.headers.get("content-type", ""), body


def response_output_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    texts: list[str] = []
    output = payload.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        if item.get("role") not in {None, "assistant"}:
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if (
                isinstance(part, dict)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            ):
                texts.append(part["text"])
    return "".join(texts)


def final_assistant_text(content_type: str, raw: str) -> tuple[str, str]:
    if "text/event-stream" not in content_type.lower():
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return "", "invalid_json"
        return response_output_text(payload), "json"

    completed_text = ""
    done_text = ""
    deltas: list[str] = []
    parsed_event = False
    for line in raw.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        parsed_event = True
        event_type = event.get("type")
        if event_type == "response.completed":
            candidate = response_output_text(event.get("response"))
            if candidate:
                completed_text = candidate
        elif event_type == "response.output_text.done" and isinstance(
            event.get("text"), str
        ):
            done_text = event["text"]
        elif event_type == "response.output_text.delta" and isinstance(
            event.get("delta"), str
        ):
            deltas.append(event["delta"])
    if completed_text:
        return completed_text, "sse_completed"
    if done_text:
        return done_text, "sse_done"
    return ("".join(deltas), "sse_delta") if parsed_event else ("", "invalid_sse")


def probe_models(base_url: str) -> ProbeResult:
    started = time.monotonic()
    try:
        status, payload = request_json(f"{base_url.rstrip('/')}/v1/models", timeout=5)
    except (OSError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return ProbeResult("models_endpoint", False, time.monotonic() - started, {"error": str(exc)})
    models = payload.get("data") if isinstance(payload, dict) else []
    model_ids = [str(item.get("id")) for item in models if isinstance(item, dict) and item.get("id")]
    return ProbeResult(
        "models_endpoint",
        status == 200 and bool(model_ids),
        time.monotonic() - started,
        {"status": status, "models": model_ids[:10]},
    )


def probe_exact_marker(base_url: str) -> ProbeResult:
    started = time.monotonic()
    payload = {
        "model": "qwen-local",
        "input": [{"role": "user", "content": "Reply exactly QWENDEX_OK."}],
        "max_output_tokens": 64,
        "temperature": 0,
    }
    try:
        status, content_type, text = request_text(f"{base_url.rstrip('/')}/v1/responses", payload=payload, timeout=60)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return ProbeResult("exact_marker", False, time.monotonic() - started, {"error": str(exc)})
    assistant_text, parse_mode = final_assistant_text(content_type, text)
    normalized = assistant_text.strip()
    marker_count = sum(assistant_text.count(marker) for marker in BAD_MARKERS)
    exact_match = normalized == "QWENDEX_OK"
    success = status == 200 and exact_match and marker_count == 0
    return ProbeResult(
        "exact_marker",
        success,
        time.monotonic() - started,
        {
            "status": status,
            "content_type": content_type,
            "marker_count": marker_count,
            "response_chars": len(text),
            "assistant_text_chars": len(assistant_text),
            "assistant_text_sha256": hashlib.sha256(
                assistant_text.encode("utf-8")
            ).hexdigest(),
            "exact_match": exact_match,
            "parse_mode": parse_mode,
            "sse": "text/event-stream" in content_type,
        },
    )


def run(*, base_url: str, require_live_bridge: bool) -> dict[str, Any]:
    probes = [probe_models(base_url)]
    if probes[0].success:
        probes.append(probe_exact_marker(base_url))
    elif require_live_bridge:
        probes.append(ProbeResult("exact_marker", False, 0.0, {"skipped": "models endpoint unavailable"}))
    success = all(probe.success for probe in probes) if require_live_bridge else probes[0].success
    return {
        "schema_version": "qwendex.reliability_probe.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "pass" if success else "fail",
        "base_url": base_url,
        "require_live_bridge": require_live_bridge,
        "probes": [probe.__dict__ for probe in probes],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--require-live-bridge", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    payload = run(base_url=args.base_url, require_live_bridge=args.require_live_bridge)
    print(json.dumps(payload, indent=2, sort_keys=True) if args.json else payload["status"])
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
