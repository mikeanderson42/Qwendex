#!/usr/bin/env bash
set -euo pipefail

LITELLM_BASE="${LITELLM_BASE:-http://127.0.0.1:4000}"
BACKEND_API_BASE="${LOCAL_LLM_UPSTREAM_BASE:-${TEXTGEN_API_BASE:-http://127.0.0.1:5000}}"
CODEX_BASE="${LOCAL_LLM_CODEX_BASE:-http://127.0.0.1:1234}"

echo "=== listeners ==="
ss -ltnp | grep -E ':1234|:4000|:5000|:7860' || true

echo "=== GPU memory ==="
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu --format=csv,noheader,nounits || true
else
  echo "nvidia-smi not available"
fi

echo "=== local model backend API (TextGen or vLLM) ==="
if body="$(curl -fsS --max-time 3 "$BACKEND_API_BASE/v1/models" 2>/dev/null)"; then
  printf '%s\n' "$body" | python3 -m json.tool | head -80
else
  echo "Local model backend API not running at $BACKEND_API_BASE"
fi

echo "=== LiteLLM proxy (optional) ==="
if body="$(curl -fsS --max-time 3 "$LITELLM_BASE/v1/models" 2>/dev/null)"; then
  printf '%s\n' "$body" | python3 -m json.tool | head -80
else
  echo "LiteLLM not running at $LITELLM_BASE"
fi

echo "=== Codex responses bridge (optional) ==="
if body="$(curl -fsS --max-time 3 "$CODEX_BASE/v1/models" 2>/dev/null)"; then
  printf '%s\n' "$body" | python3 -m json.tool | head -80
else
  echo "Codex bridge not running at $CODEX_BASE"
fi

echo "=== Codex responses bridge status ==="
if body="$(curl -fsS --max-time 3 "$CODEX_BASE/status" 2>/dev/null)"; then
  if printf '%s\n' "$body" | python3 -c '
import json
import sys

try:
    payload = json.load(sys.stdin)
except (json.JSONDecodeError, UnicodeDecodeError):
    raise SystemExit(1)
raise SystemExit(
    0
    if isinstance(payload, dict)
    and payload.get("schema_version") == "qwendex.responses_bridge.status.v1"
    and payload.get("status") == "ok"
    else 1
)
'; then
    printf '%s\n' "$body" | python3 -m json.tool | head -80
  else
    echo "Codex bridge returned an invalid status contract at $CODEX_BASE/status"
  fi
else
  echo "Codex bridge status endpoint not available at $CODEX_BASE"
fi
