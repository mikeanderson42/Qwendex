#!/usr/bin/env bash
set -euo pipefail

LOGDIR="$HOME/.codex_lmstudio_safe/logs"
mkdir -p "$LOGDIR"

echo "=== listeners ==="
ss -ltnp | grep -E ':1233|:1234|:12345' || {
  echo "Missing one or more required listeners."
  exit 1
}

echo "=== model checks ==="
curl -fsS http://127.0.0.1:12345/v1/models >/dev/null
curl -fsS http://127.0.0.1:1233/v1/models >/dev/null
curl -fsS http://127.0.0.1:1234/v1/models >/dev/null

echo "=== think strip check ==="
OUT="$(curl -fsS http://127.0.0.1:1234/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{"model":"qwen3.6-27b-4.26gguf","input":"Output exactly this text: <think>hidden</think>VISIBLE","max_output_tokens":32}' \
  | jq -r '.output[0].content[0].text')"

echo "$OUT"

if echo "$OUT" | grep -qi '<think'; then
  echo "FAIL: think tags reached front endpoint."
  exit 2
fi

echo "=== recent proxy errors ==="
grep -RniE 'Traceback|ERROR|Failed to parse|Address already in use|unsupported Responses tool|Invalid API Key' "$LOGDIR" 2>/dev/null | tail -40 || true

echo "OK"
