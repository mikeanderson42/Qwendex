#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.request
from pathlib import Path
from typing import Any


def get_json(base_url: str, path: str, timeout: int) -> dict[str, Any]:
    with urllib.request.urlopen(base_url.rstrip("/") + path, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {}


def post_chat(
    base_url: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> tuple[float, dict[str, Any]]:
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer no-key"},
        method="POST",
    )
    started = time.time()
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = json.loads(response.read().decode("utf-8"))
    return time.time() - started, body if isinstance(body, dict) else {}


def content_from_chat(payload: dict[str, Any]) -> str:
    try:
        value = payload["choices"][0]["message"].get("content", "")
    except (KeyError, IndexError, TypeError, AttributeError):
        return ""
    return value if isinstance(value, str) else ""


def compact_perf(perf: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "last_process_time",
        "last_process_speed",
        "last_input_count",
        "last_eval_time",
        "last_eval_speed",
        "last_token_count",
        "total_gens",
        "stop_reason",
    ]
    return {key: perf.get(key) for key in keys}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-tokens", type=int, default=80)
    parser.add_argument("--temperature", type=float, default=0.1)
    args = parser.parse_args()

    shared = "Shared prefix cache probe. Remember anchor ALPHA-773 and answer with compact JSON only. "
    messages1 = [
        {
            "role": "user",
            "content": shared + 'Step 1: return {"step":1,"anchor":"ALPHA-773"}.',
        }
    ]
    elapsed1, body1 = post_chat(
        args.base_url,
        args.model,
        messages1,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        timeout=args.timeout,
    )
    perf1 = get_json(args.base_url, "/api/extra/perf", args.timeout)

    assistant1 = content_from_chat(body1)
    messages2 = messages1 + [
        {"role": "assistant", "content": assistant1},
        {"role": "user", "content": 'Step 2: return {"step":2,"anchor":"ALPHA-773"}.',},
    ]
    elapsed2, body2 = post_chat(
        args.base_url,
        args.model,
        messages2,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        timeout=args.timeout,
    )
    perf2 = get_json(args.base_url, "/api/extra/perf", args.timeout)

    elapsed3, body3 = post_chat(
        args.base_url,
        args.model,
        messages2,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        timeout=args.timeout,
    )
    perf3 = get_json(args.base_url, "/api/extra/perf", args.timeout)

    first_process = float(perf1.get("last_process_time") or 0.0)
    followup_process = float(perf2.get("last_process_time") or 0.0)
    retry_process = float(perf3.get("last_process_time") or 0.0)
    result: dict[str, Any] = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": args.model,
        "base_url": args.base_url,
        "probe": "same-session repeated-prefix SmartCache/fast-forward behavior",
        "elapsed_s": [elapsed1, elapsed2, elapsed3],
        "perf": [compact_perf(perf1), compact_perf(perf2), compact_perf(perf3)],
        "visible_think": ["<think>" in content_from_chat(body).lower() for body in [body1, body2, body3]],
        "content_prefix": [content_from_chat(body)[:500] for body in [body1, body2, body3]],
        "passed": (
            first_process > 0.0
            and followup_process <= first_process
            and retry_process <= followup_process
        ),
    }

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": result["passed"],
                "process_times": [first_process, followup_process, retry_process],
                "input_counts": [item.get("last_input_count") for item in result["perf"]],
                "output": str(output),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
