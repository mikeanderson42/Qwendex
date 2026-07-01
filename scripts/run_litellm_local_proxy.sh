#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/config/local_llm_stack/local_harness.env"
CALLER_LOCAL_LLM_UPSTREAM_BASE="${LOCAL_LLM_UPSTREAM_BASE+x}${LOCAL_LLM_UPSTREAM_BASE-}"
CALLER_LITELLM_CONFIG="${LITELLM_CONFIG+x}${LITELLM_CONFIG-}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
[[ "$CALLER_LOCAL_LLM_UPSTREAM_BASE" == x* ]] && LOCAL_LLM_UPSTREAM_BASE="${CALLER_LOCAL_LLM_UPSTREAM_BASE#x}"
[[ "$CALLER_LITELLM_CONFIG" == x* ]] && LITELLM_CONFIG="${CALLER_LITELLM_CONFIG#x}"

LITELLM_HOME="${LITELLM_HOME:-$HOME/litellm}"
VENV_PY="$LITELLM_HOME/venv/bin/python"
LITELLM_BIN="$LITELLM_HOME/venv/bin/litellm"
CONFIG="${LITELLM_CONFIG:-$LITELLM_HOME/config.yaml}"
HOST="${LITELLM_HOST:-127.0.0.1}"
PORT="${LITELLM_PORT:-4000}"
UPSTREAM="${LOCAL_LLM_UPSTREAM_BASE:-http://127.0.0.1:5000}"

if [[ ! -x "$VENV_PY" || ! -x "$LITELLM_BIN" ]]; then
  echo "Missing LiteLLM install under $LITELLM_HOME/venv" >&2
  echo "Install first: python3 -m venv ~/litellm/venv && ~/litellm/venv/bin/python -m pip install 'litellm[proxy]'" >&2
  exit 1
fi

if [[ ! -f "$CONFIG" ]]; then
  echo "Missing LiteLLM config: $CONFIG" >&2
  echo "Run: scripts/setup_local_llm_stack.sh" >&2
  exit 1
fi

if ! curl -fsS --max-time 3 "$UPSTREAM/v1/models" >/dev/null; then
  echo "Upstream local harness is not healthy at $UPSTREAM/v1/models" >&2
  echo "This script will not start or load a model." >&2
  exit 1
fi

echo "Starting LiteLLM proxy on http://$HOST:$PORT -> $UPSTREAM/v1"
exec "$LITELLM_BIN" --config "$CONFIG" --host "$HOST" --port "$PORT"
