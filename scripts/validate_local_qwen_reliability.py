#!/usr/bin/env python3
"""Run a small public-safe reliability probe against the local Qwendex bridge."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
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
        "max_output_tokens": 32,
        "temperature": 0,
    }
    try:
        status, content_type, text = request_text(f"{base_url.rstrip('/')}/v1/responses", payload=payload, timeout=60)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        return ProbeResult("exact_marker", False, time.monotonic() - started, {"error": str(exc)})
    marker_count = sum(text.count(marker) for marker in BAD_MARKERS)
    success = status == 200 and "QWENDEX_OK" in text and marker_count == 0
    return ProbeResult(
        "exact_marker",
        success,
        time.monotonic() - started,
        {
            "status": status,
            "content_type": content_type,
            "marker_count": marker_count,
            "response_chars": len(text),
            "contains_ok": "QWENDEX_OK" in text,
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
