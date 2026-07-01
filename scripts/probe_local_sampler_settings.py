#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_BASE = "http://127.0.0.1:4000/v1"
DEFAULT_MODEL = "Qwopucode-full-v15-27B-FP8-Block-i1-Q4_K_M"


PROBES = {
    "exact_json": {
        "prompt": 'Return JSON only: {"ok":true}',
        "max_tokens": 2048,
        "expected": {"ok": True},
    },
    "blocker_json": {
        "prompt": (
            "Return JSON only. Do not mention tools, commands, terminals, or files.\n"
            "Rows: ["
            '{"id":"a","status":"complete_validated","blocker":""},'
            '{"id":"b","status":"complete_validated","blocker":"source hold"},'
            '{"id":"c","status":"pending","blocker":""},'
            '{"id":"d","status":"complete_validated","blocker":"manual review"}]\n'
            "A row is blocked if and only if its blocker field is non-empty, regardless of status.\n"
            'Return this schema exactly: {"blocked_ids":[...],"blocked_count":number,"rule":"..."}'
        ),
        "max_tokens": 2048,
        "expected": {"blocked_ids": ["b", "d"], "blocked_count": 2},
    },
}


def parse_variant(raw: str) -> tuple[str, dict[str, Any]]:
    name, sep, body = raw.partition(":")
    if not sep or not name.strip():
        raise ValueError(f"variant must look like name:key=value,...: {raw}")
    params: dict[str, Any] = {}
    for item in body.split(","):
        item = item.strip()
        if not item:
            continue
        key, eq, value = item.partition("=")
        if not eq:
            raise ValueError(f"variant item must look like key=value: {item}")
        key = key.strip()
        value = value.strip()
        if value.lower() in {"true", "false"}:
            params[key] = value.lower() == "true"
        else:
            try:
                params[key] = int(value) if re.fullmatch(r"-?\d+", value) else float(value)
            except ValueError:
                params[key] = value
    return name.strip(), params


def post_json(url: str, payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": "Bearer no-key"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        value = json.loads(stripped)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


def reasoning_text(message: dict[str, Any]) -> str:
    fields = message.get("provider_specific_fields") or {}
    return str(message.get("reasoning_content") or fields.get("reasoning_content") or "")


def expected_passed(name: str, parsed: dict[str, Any]) -> bool:
    expected = PROBES[name]["expected"]
    for key, value in expected.items():
        if parsed.get(key) != value:
            return False
    return True


def run_probe(base_url: str, model: str, variant_name: str, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    result: dict[str, Any] = {"params": params, "probes": {}}
    chat_url = f"{base_url.rstrip('/')}/chat/completions"
    for probe_name, probe in PROBES.items():
        max_tokens = int(params.get("max_tokens", probe["max_tokens"]))
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": probe["prompt"]}],
            "max_tokens": max_tokens,
        }
        for key in ["temperature", "top_p", "top_k", "min_p", "repetition_penalty", "presence_penalty", "reasoning_effort"]:
            if key in params:
                payload[key] = params[key]
        started = time.time()
        response = post_json(chat_url, payload, timeout)
        elapsed = time.time() - started
        message = response["choices"][0]["message"]
        content = str(message.get("content") or "")
        reasoning = reasoning_text(message)
        parsed = parse_json_object(content)
        result["probes"][probe_name] = {
            "passed": expected_passed(probe_name, parsed),
            "elapsed_s": elapsed,
            "usage": response.get("usage"),
            "visible_think": "<think>" in content or "</think>" in content,
            "reasoning_chars": len(reasoning),
            "content_prefix": content[:800],
            "parsed": parsed,
        }
    result["passed"] = all(item["passed"] and not item["visible_think"] for item in result["probes"].values())
    result["variant"] = variant_name
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument(
        "--variant",
        action="append",
        default=[],
        help="name:key=value,... for request-level settings; can be repeated",
    )
    args = parser.parse_args()

    raw_variants = args.variant or [
        "qwen36_precise:temperature=0.6,top_p=0.95,top_k=20,min_p=0,repetition_penalty=1.0,reasoning_effort=high,max_tokens=2048",
        "qwen36_general:temperature=1.0,top_p=0.95,top_k=20,min_p=0,repetition_penalty=1.0,reasoning_effort=high,max_tokens=2048",
        "codex_lowtemp:temperature=0.15,top_p=0.95,top_k=20,min_p=0,repetition_penalty=1.0,reasoning_effort=high,max_tokens=2048",
    ]
    variants = [parse_variant(raw) for raw in raw_variants]
    output = {
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_url": args.base_url,
        "model": args.model,
        "variants": {},
    }
    for name, params in variants:
        output["variants"][name] = run_probe(args.base_url, args.model, name, params, args.timeout)
    passed_variants = {
        name: data
        for name, data in output["variants"].items()
        if data.get("passed")
    }
    output["passed"] = bool(passed_variants)
    output["best_by_completion_tokens"] = None
    if passed_variants:
        def completion_tokens(item: tuple[str, dict[str, Any]]) -> int:
            total = 0
            for probe in item[1]["probes"].values():
                usage = probe.get("usage") or {}
                total += int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)
            return total

        output["best_by_completion_tokens"] = min(passed_variants.items(), key=completion_tokens)[0]
    out_path = Path(args.output)
    out_path.write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps({
        "passed": output["passed"],
        "best_by_completion_tokens": output["best_by_completion_tokens"],
        "output": str(out_path),
        "variant_results": {
            name: {
                "passed": data["passed"],
                "completion_tokens": sum(
                    int((probe.get("usage") or {}).get("completion_tokens") or (probe.get("usage") or {}).get("output_tokens") or 0)
                    for probe in data["probes"].values()
                ),
                "visible_think": any(probe["visible_think"] for probe in data["probes"].values()),
            }
            for name, data in output["variants"].items()
        },
    }, indent=2))
    return 0 if output["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
