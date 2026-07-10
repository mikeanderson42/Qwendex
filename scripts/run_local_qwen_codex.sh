#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCH_CWD="$(pwd)"
ENV_FILE="$ROOT/config/local_llm_stack/local_harness.env"
CALLER_LOCAL_QWEN_MODEL="${LOCAL_QWEN_MODEL+x}${LOCAL_QWEN_MODEL-}"
CALLER_LOCAL_QWEN_BASE="${LOCAL_QWEN_BASE+x}${LOCAL_QWEN_BASE-}"
CALLER_LOCAL_QWEN_CODEX_CONTEXT_WINDOW="${LOCAL_QWEN_CODEX_CONTEXT_WINDOW+x}${LOCAL_QWEN_CODEX_CONTEXT_WINDOW-}"
CALLER_LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT="${LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT+x}${LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT-}"
CALLER_LOCAL_QWEN_GUARD_PROFILE="${LOCAL_QWEN_GUARD_PROFILE+x}${LOCAL_QWEN_GUARD_PROFILE-}"
CALLER_LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS="${LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS+x}${LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS-}"
CALLER_LOCAL_QWEN_CODEX_MAX_TOOL_CALLS="${LOCAL_QWEN_CODEX_MAX_TOOL_CALLS+x}${LOCAL_QWEN_CODEX_MAX_TOOL_CALLS-}"
CALLER_LOCAL_QWEN_CODEX_SANDBOX_MODE="${LOCAL_QWEN_CODEX_SANDBOX_MODE+x}${LOCAL_QWEN_CODEX_SANDBOX_MODE-}"
CALLER_LOCAL_QWEN_HEALTH_LOG="${LOCAL_QWEN_HEALTH_LOG+x}${LOCAL_QWEN_HEALTH_LOG-}"
CALLER_CODEX_TEXTGEN_CONTEXT_LIMIT_TOKENS="${CODEX_TEXTGEN_CONTEXT_LIMIT_TOKENS+x}${CODEX_TEXTGEN_CONTEXT_LIMIT_TOKENS-}"
if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi
[[ "$CALLER_LOCAL_QWEN_MODEL" == x* ]] && LOCAL_QWEN_MODEL="${CALLER_LOCAL_QWEN_MODEL#x}"
[[ "$CALLER_LOCAL_QWEN_BASE" == x* ]] && LOCAL_QWEN_BASE="${CALLER_LOCAL_QWEN_BASE#x}"
[[ "$CALLER_LOCAL_QWEN_CODEX_CONTEXT_WINDOW" == x* ]] && LOCAL_QWEN_CODEX_CONTEXT_WINDOW="${CALLER_LOCAL_QWEN_CODEX_CONTEXT_WINDOW#x}"
[[ "$CALLER_LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT" == x* ]] && LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT="${CALLER_LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT#x}"
[[ "$CALLER_LOCAL_QWEN_GUARD_PROFILE" == x* ]] && LOCAL_QWEN_GUARD_PROFILE="${CALLER_LOCAL_QWEN_GUARD_PROFILE#x}"
[[ "$CALLER_LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS" == x* ]] && LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS="${CALLER_LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS#x}"
[[ "$CALLER_LOCAL_QWEN_CODEX_MAX_TOOL_CALLS" == x* ]] && LOCAL_QWEN_CODEX_MAX_TOOL_CALLS="${CALLER_LOCAL_QWEN_CODEX_MAX_TOOL_CALLS#x}"
[[ "$CALLER_LOCAL_QWEN_CODEX_SANDBOX_MODE" == x* ]] && LOCAL_QWEN_CODEX_SANDBOX_MODE="${CALLER_LOCAL_QWEN_CODEX_SANDBOX_MODE#x}"
[[ "$CALLER_LOCAL_QWEN_HEALTH_LOG" == x* ]] && LOCAL_QWEN_HEALTH_LOG="${CALLER_LOCAL_QWEN_HEALTH_LOG#x}"
[[ "$CALLER_CODEX_TEXTGEN_CONTEXT_LIMIT_TOKENS" == x* ]] && CODEX_TEXTGEN_CONTEXT_LIMIT_TOKENS="${CALLER_CODEX_TEXTGEN_CONTEXT_LIMIT_TOKENS#x}"

SAFE_HOME="${CODEX_HOME:-$HOME/.codex_qwendex_local_safe}"
CODEX_CWD="${LOCAL_QWEN_CODEX_CWD:-$ROOT}"

# Use the stable bridge alias for Codex repo-agent work. The loaded backend may
# expose a longer chat/model alias, but Codex handles tool use more reliably
# through qwen-local plus explicit context limits.
MODEL="${LOCAL_QWEN_MODEL:-qwen-local}"

# Codex must talk to the Responses bridge here, not raw TextGen or LiteLLM.
CODEX_BASE="${LOCAL_QWEN_BASE:-${LOCAL_LLM_CODEX_BASE:-http://127.0.0.1:1234}}"
CODEX_BASE="${CODEX_BASE%/}"
EXPECTED_CODEX_OSS_BASE_URL="$CODEX_BASE/v1"
if [[ -n "${CODEX_OSS_BASE_URL:-}" && "${CODEX_OSS_BASE_URL%/}" != "$EXPECTED_CODEX_OSS_BASE_URL" ]]; then
  echo "CODEX_OSS_BASE_URL conflicts with the verified Qwendex bridge: $CODEX_OSS_BASE_URL != $EXPECTED_CODEX_OSS_BASE_URL" >&2
  exit 2
fi
export CODEX_OSS_BASE_URL="$EXPECTED_CODEX_OSS_BASE_URL"

HEALTH_LOG="${LOCAL_QWEN_HEALTH_LOG:-$SAFE_HOME/logs/local_qwen_codex.health.log}"
TOOL_OUTPUT_TOKEN_LIMIT="${LOCAL_QWEN_TOOL_OUTPUT_TOKEN_LIMIT:-${CODEX_TEXTGEN_TOOL_OUTPUT_TOKEN_LIMIT:-1200}}"
CODEX_CONTEXT_WINDOW="${LOCAL_QWEN_CODEX_CONTEXT_WINDOW:-${CODEX_TEXTGEN_CONTEXT_LIMIT_TOKENS:-32768}}"
CODEX_AUTO_COMPACT_LIMIT="${LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT:-28672}"
MODEL_CATALOG_JSON="${LOCAL_QWEN_MODEL_CATALOG_JSON:-$ROOT/config/local_llm_stack/qwen_local_model_catalog.json}"
MCP_BIN_ROOT="${LOCAL_QWEN_MCP_BIN_ROOT:-$HOME/.codex/mcp-servers/node_modules/.bin}"
LOCAL_HARNESS_MCP="${LOCAL_QWEN_LOCAL_HARNESS_MCP:-$ROOT/scripts/artifact_queue_mcp.py}"
LOCAL_QWEN_CHECK_MCP_BINS="${LOCAL_QWEN_CHECK_MCP_BINS:-1}"
CODEX_GOAL_MODE="${LOCAL_QWEN_CODEX_GOAL_MODE:-guarded}"
LOCAL_QWEN_GUARD_PROFILE="${LOCAL_QWEN_GUARD_PROFILE:-balanced}"
LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS="${LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS:--1}"
LOCAL_QWEN_CODEX_MAX_TOOL_CALLS="${LOCAL_QWEN_CODEX_MAX_TOOL_CALLS:--1}"
LOCAL_QWEN_CODEX_SANDBOX_MODE="${LOCAL_QWEN_CODEX_SANDBOX_MODE:-workspace-write}"
LOCAL_QWEN_CODEX_ADD_DIRS="${LOCAL_QWEN_CODEX_ADD_DIRS:-}"
EXPECTED_BRIDGE_VERSION="${LOCAL_QWEN_EXPECTED_BRIDGE_VERSION:-qwendex-local-qwen-responses-v2}"
if [[ -n "${LOCAL_QWEN_CODEX_ENABLE_GOALS+x}" ]]; then
  case "$LOCAL_QWEN_CODEX_ENABLE_GOALS" in
    0|false|FALSE|no|NO) CODEX_GOAL_MODE="off" ;;
    *) CODEX_GOAL_MODE="guarded" ;;
  esac
fi
CODEX_SKIP_GIT_REPO_CHECK="${LOCAL_QWEN_CODEX_SKIP_GIT_REPO_CHECK:-0}"
CODEX_EXEC_EPHEMERAL="${LOCAL_QWEN_CODEX_EPHEMERAL:-0}"
CODEX_EXEC_MINIMAL=0
CODEX_EXEC_JSON=0
CODEX_OUTPUT_SCHEMA=""
CODEX_OUTPUT_LAST_MESSAGE=""
BRIDGE_STATUS_JSON=""
HEALTH_LOG_FROM_CALLER=0
[[ "$CALLER_LOCAL_QWEN_HEALTH_LOG" == x* ]] && HEALTH_LOG_FROM_CALLER=1

refresh_safe_home_paths() {
  if [[ "$HEALTH_LOG_FROM_CALLER" -eq 0 ]]; then
    HEALTH_LOG="$SAFE_HOME/logs/local_qwen_codex.health.log"
  fi
}

prepare_safe_home() {
  mkdir -p "$SAFE_HOME/logs"
}

set_safe_home() {
  SAFE_HOME="$1"
  refresh_safe_home_paths
  prepare_safe_home
}

find_linux_codex() {
  local candidate

  if [[ -n "${CODEX_BIN:-}" ]]; then
    if [[ "$CODEX_BIN" == /mnt/c/* ]]; then
      echo "CODEX_BIN points to a Windows Codex shim: $CODEX_BIN" >&2
      return 1
    fi
    if [[ ! -x "$CODEX_BIN" ]]; then
      echo "CODEX_BIN is not executable: $CODEX_BIN" >&2
      return 1
    fi
    printf '%s\n' "$CODEX_BIN"
    return 0
  fi

  while IFS= read -r candidate; do
    if [[ -n "$candidate" && "$candidate" != /mnt/c/* && -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done < <(type -P -a codex 2>/dev/null || true)

  if [[ -d "$HOME/.nvm/versions/node" ]]; then
    candidate="$(
      find "$HOME/.nvm/versions/node" -path '*/bin/codex' -type f -executable 2>/dev/null \
        | sort -V \
        | tail -n 1
    )"
    if [[ -n "$candidate" && "$candidate" != /mnt/c/* ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  fi

  return 1
}

resolve_codex_bin() {
  local resolved
  if ! resolved="$(find_linux_codex)"; then
    echo "Could not find a Linux Codex executable." >&2
    echo "The local-Qwen launcher must not use the Windows npm Codex shim from /mnt/c." >&2
    echo "Install or repair Codex inside WSL, or set CODEX_BIN to the Linux executable." >&2
    echo "" >&2
    echo "Current PATH:" >&2
    printf '%s\n' "$PATH" >&2
    echo "" >&2
    echo "Visible codex commands:" >&2
    type -a codex >&2 || true
    exit 1
  fi
  printf '%s\n' "$resolved"
}

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_local_qwen_codex.sh
  scripts/run_local_qwen_codex.sh --cwd /path/to/project
  scripts/run_local_qwen_codex.sh --exec 'Reply exactly QWENDEX_OK'
  scripts/run_local_qwen_codex.sh --minimal --ephemeral --output-last-message out.md --exec 'Reply with one paragraph'
  scripts/run_local_qwen_codex.sh --fresh-home /tmp/codex-home --exec 'Reply OK'
  scripts/run_local_qwen_codex.sh --mcp-list
  scripts/run_local_qwen_codex.sh --mcp-login <server>
  scripts/run_local_qwen_codex.sh --check

Behavior:
  - verifies the Codex-facing Responses bridge at 127.0.0.1:1234
  - does NOT point Codex directly at raw TextGen, raw LiteLLM, or raw GGUF loaders
  - launches Codex against the stable qwen-local bridge alias by default
  - uses Codex workspace-write sandboxing by default; --sandbox read-only is supported

Environment overrides:
  LOCAL_QWEN_MODEL       default: qwen-local
  LOCAL_QWEN_BASE        default: http://127.0.0.1:1234
  LOCAL_QWEN_CODEX_CWD   default: this repo
  LOCAL_QWEN_TOOL_OUTPUT_TOKEN_LIMIT default: 1200
  LOCAL_QWEN_CODEX_CONTEXT_WINDOW default: 32768
  LOCAL_QWEN_CODEX_AUTO_COMPACT_LIMIT default: 28672
  LOCAL_QWEN_MODEL_CATALOG_JSON default: config/local_llm_stack/qwen_local_model_catalog.json
  LOCAL_QWEN_CODEX_GOAL_MODE default: guarded; set off if the goal layer loops
  LOCAL_QWEN_CODEX_ENABLE_GOALS legacy override: 0 disables goals, any other value enables guarded goals
  LOCAL_QWEN_CODEX_SKIP_GIT_REPO_CHECK default: 0; set 1 only for isolated non-repo cwd benchmarks
  LOCAL_QWEN_CODEX_EPHEMERAL default: 0; set 1 for one-shot exec runs that should not persist sessions
  LOCAL_QWEN_GUARD_PROFILE default: balanced; set max_safety for stricter runtime guard behavior
  LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS default: -1 unlimited; positive integer wraps --exec in timeout
  LOCAL_QWEN_CODEX_MAX_TOOL_CALLS default: -1 unlimited; must match the already-running bridge status
  LOCAL_QWEN_CODEX_SANDBOX_MODE default: workspace-write; read-only is also supported
  LOCAL_QWEN_CHECK_MCP_BINS default: 1; set 0 only for isolated bridge/model probes
  LOCAL_QWEN_MCP_BIN_ROOT default: ~/.codex/mcp-servers/node_modules/.bin
  LOCAL_QWEN_LOCAL_HARNESS_MCP default: scripts/artifact_queue_mcp.py in this repo
  --minimal             exec only: ignore user config/MCP for tiny packet checks
  --sandbox MODE        Codex sandbox mode: workspace-write or read-only
  --ephemeral           exec only: do not persist the Codex session
  --json                exec only: emit Codex JSON event stream
  --output-schema FILE  exec only: request structured output validated by Codex
  --output-last-message FILE
                        exec only: write the final assistant message to FILE
  --fresh-home DIR      create/use a fresh CODEX_HOME A/B lane for this launch
  CODEX_HOME             default: ~/.codex_qwendex_local_safe

Before running this script, start:
  scripts/llm start
USAGE
}

check_base() {
  local base="$1"
  curl -fsS "$base/v1/models" >/dev/null
}

show_models() {
  local base="$1"
  curl -fsS "$base/v1/models" 2>"$HEALTH_LOG" || return 1
}

check_mcp_bins() {
  case "$LOCAL_QWEN_CHECK_MCP_BINS" in
    0|false|FALSE|no|NO|off|OFF) return 0 ;;
  esac

  local missing=0
  local bin
  for bin in context7-mcp mcp-server-github mcp-server-memory playwright-mcp mcp-server-sequential-thinking; do
    if [[ ! -x "$MCP_BIN_ROOT/$bin" ]]; then
      echo "Missing pinned MCP binary: $MCP_BIN_ROOT/$bin" >&2
      missing=1
    fi
  done
  if [[ "$missing" -ne 0 ]]; then
    echo "Repair with: npm install --prefix ${MCP_BIN_ROOT%/node_modules/.bin} @modelcontextprotocol/server-memory @modelcontextprotocol/server-sequential-thinking @upstash/context7-mcp@latest @playwright/mcp@latest @modelcontextprotocol/server-github" >&2
    echo "For isolated bridge/model probes only, bypass this check with LOCAL_QWEN_CHECK_MCP_BINS=0." >&2
    exit 1
  fi
  if [[ ! -f "$LOCAL_HARNESS_MCP" ]]; then
    echo "Missing local harness MCP script: $LOCAL_HARNESS_MCP" >&2
    echo "For isolated bridge/model probes only, bypass this check with LOCAL_QWEN_CHECK_MCP_BINS=0." >&2
    exit 1
  fi
}

bridge_status() {
  local base="$1"
  local payload
  payload="$(curl -fsS "$base/status" 2>/dev/null)" || return 1
  if ! python3 -c '
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
' <<<"$payload"; then
    return 1
  fi
  printf '%s\n' "$payload"
}

ensure_backend() {
  mkdir -p "$(dirname "$HEALTH_LOG")"

  if ! check_base "$CODEX_BASE" 2>"$HEALTH_LOG"; then
    echo "Codex-facing endpoint is not healthy at $CODEX_BASE" >&2
    echo "Start the local stack in another terminal:" >&2
    echo "  scripts/llm start" >&2
    echo "" >&2
    echo "Then test:" >&2
    echo "  curl -sS $CODEX_BASE/v1/models -H 'Authorization: Bearer no-key' | jq" >&2
    echo "" >&2
    echo "--- health log ---" >&2
    tail -n 20 "$HEALTH_LOG" >&2 || true
    exit 1
  fi
}

ensure_codex_bridge() {
  local status_json
  if ! status_json="$(bridge_status "$CODEX_BASE")"; then
    echo "Codex-facing Responses bridge is not running at $CODEX_BASE" >&2
    echo "Do not point Codex directly at raw TextGen, raw LiteLLM, or a GGUF loader; the bridge provides Codex Responses compatibility." >&2
    echo "Start the stack in separate terminals:" >&2
    echo "  scripts/llm start" >&2
    exit 1
  fi
  BRIDGE_STATUS_JSON="$status_json"
  if ! BRIDGE_STATUS_JSON="$status_json" python3 - <<'PY'
import json
import os
import urllib.parse

payload = json.loads(os.environ["BRIDGE_STATUS_JSON"])
target = urllib.parse.urlsplit(str(payload.get("target_base") or ""))
if target.scheme not in {"http", "https"} or not target.hostname:
    raise SystemExit(1)
PY
  then
    echo "Codex bridge does not report a valid OpenAI-compatible target:" >&2
    printf '%s\n' "$status_json" >&2
    exit 1
  fi
}

ensure_model_visible() {
  local models_json
  models_json="$(show_models "$CODEX_BASE")"

  if ! printf '%s\n' "$models_json" | grep -q "\"$MODEL\""; then
    echo "Backend is healthy, but expected model name was not found." >&2
    echo "Expected model: $MODEL" >&2
    echo "Backend models:" >&2
    printf '%s\n' "$models_json" >&2
    echo "" >&2
    echo "Use scripts/llm model-list and restart LiteLLM/bridge if aliases were just changed." >&2
    exit 1
  fi
}

run_check_only() {
  validate_runtime_budgets
  prepare_safe_home
  ensure_backend
  ensure_model_visible
  ensure_codex_bridge
  ensure_bridge_runtime_guard_matches
  check_mcp_bins
  echo "Codex-facing endpoint ready: $CODEX_BASE"
  echo "Model visible as: $MODEL"
  echo "Codex Responses bridge is active in front of an OpenAI-compatible target."
  echo "Context window: $CODEX_CONTEXT_WINDOW"
  echo "Auto compact limit: $CODEX_AUTO_COMPACT_LIMIT"
  echo "Tool output token limit: $TOOL_OUTPUT_TOKEN_LIMIT"
  echo "Goal mode: $CODEX_GOAL_MODE"
  echo "Runtime guard profile: $LOCAL_QWEN_GUARD_PROFILE"
  echo "Exec wall-time budget seconds: $LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS"
  echo "Exec tool-call budget: $LOCAL_QWEN_CODEX_MAX_TOOL_CALLS"
  echo "Model catalog: $MODEL_CATALOG_JSON"
  echo "CODEX_HOME: $SAFE_HOME"
  echo "Codex cwd: $CODEX_CWD"
  echo "Pinned MCP binaries: $MCP_BIN_ROOT"
  echo "Local harness MCP: $LOCAL_HARNESS_MCP"
  local codex_bin
  codex_bin="$(resolve_codex_bin)"
  local mcp_trusted_roots="${LOCAL_QWEN_LOCAL_HARNESS_TRUSTED_ROOTS:-$CODEX_CWD}"
  (cd "$CODEX_CWD" && CODEX_HOME="$SAFE_HOME" "$codex_bin" \
    -c 'mcp_servers.local-harness.command="python3"' \
    -c "mcp_servers.local-harness.args=[\"$LOCAL_HARNESS_MCP\"]" \
    -c "mcp_servers.local-harness.cwd=\"$CODEX_CWD\"" \
    -c "mcp_servers.local-harness.env.ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS=\"$mcp_trusted_roots\"" \
    -c 'mcp_servers.local-harness.env.SEARXNG_URL="http://127.0.0.1:6060"' \
    mcp list)
}

validate_budget_value() {
  local name="$1"
  local value="$2"
  local allow_zero="$3"
  local allowed="-1 or a positive integer"
  if [[ "$allow_zero" == "1" ]]; then
    allowed="-1, 0, or a positive integer"
  fi
  if [[ -z "$value" || "$value" == "-1" ]]; then
    return 0
  fi
  if [[ "$allow_zero" == "1" && "$value" == "0" ]]; then
    return 0
  fi
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    echo "$name must be $allowed; got: $value" >&2
    exit 2
  fi
  if [[ "$value" == "0" ]]; then
    echo "$name must be $allowed; got: 0" >&2
    exit 2
  fi
}

validate_runtime_budgets() {
  case "$LOCAL_QWEN_GUARD_PROFILE" in
    balanced|max_safety) ;;
    *)
      echo "LOCAL_QWEN_GUARD_PROFILE must be balanced or max_safety; got: $LOCAL_QWEN_GUARD_PROFILE" >&2
      exit 2
      ;;
  esac
  validate_budget_value LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS "$LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS" 0
  validate_budget_value LOCAL_QWEN_CODEX_MAX_TOOL_CALLS "$LOCAL_QWEN_CODEX_MAX_TOOL_CALLS" 1
}

ensure_bridge_runtime_guard_matches() {
  if [[ -z "$BRIDGE_STATUS_JSON" ]]; then
    if ! BRIDGE_STATUS_JSON="$(bridge_status "$CODEX_BASE")"; then
      echo "Could not read bridge runtime guard status from $CODEX_BASE" >&2
      exit 1
    fi
  fi
  BRIDGE_STATUS_JSON="$BRIDGE_STATUS_JSON" python3 - "$LOCAL_QWEN_GUARD_PROFILE" "$LOCAL_QWEN_CODEX_MAX_TOOL_CALLS" "$EXPECTED_BRIDGE_VERSION" "$CODEX_CONTEXT_WINDOW" "$CODEX_AUTO_COMPACT_LIMIT" <<'PY'
import json
import os
import sys

expected_profile = sys.argv[1]
expected_tool_budget = int(sys.argv[2])
expected_version = sys.argv[3]
requested_context = int(sys.argv[4])
auto_compact = int(sys.argv[5])
try:
    status = json.loads(os.environ["BRIDGE_STATUS_JSON"])
except (KeyError, json.JSONDecodeError) as exc:
    print(f"Could not parse bridge runtime guard status: {exc}", file=sys.stderr)
    sys.exit(2)

if status.get("runtime_guard_version") != "local-qwen-runtime-guard-v1":
    print(
        "Codex bridge is running without local-Qwen runtime guard status. "
        "Restart the bridge with: scripts/llm restart bridge",
        file=sys.stderr,
    )
    sys.exit(2)
if status.get("version") != expected_version:
    print(
        "Codex bridge version mismatch: "
        f"launcher expects {expected_version}, bridge reports {status.get('version')}. "
        "Restart the bridge with: scripts/llm restart bridge",
        file=sys.stderr,
    )
    sys.exit(2)
bridge_context = int(status.get("context_limit_tokens") or 0)
if bridge_context < requested_context:
    print(
        "Codex context mismatch: "
        f"launcher requests {requested_context} tokens, bridge reports {bridge_context}. "
        "Restart the bridge with config/local_llm_stack/local_harness.env or lower LOCAL_QWEN_CODEX_CONTEXT_WINDOW explicitly.",
        file=sys.stderr,
    )
    sys.exit(2)
if auto_compact <= 0 or auto_compact >= requested_context:
    print(
        "Invalid auto-compact limit: "
        f"model_auto_compact_token_limit={auto_compact}, model_context_window={requested_context}.",
        file=sys.stderr,
    )
    sys.exit(2)
if status.get("runtime_guard_enabled") is not True:
    print("Codex bridge runtime guard is disabled; refusing local-Qwen launch.", file=sys.stderr)
    sys.exit(2)

profile = status.get("runtime_guard_profile")
if profile != expected_profile:
    print(
        "Codex bridge runtime guard profile mismatch: "
        f"launcher={expected_profile} bridge={profile}. "
        "Update config/local_llm_stack/local_harness.env or restart the bridge with matching guard settings.",
        file=sys.stderr,
    )
    sys.exit(2)

budget_defaults = status.get("run_budget_defaults")
if not isinstance(budget_defaults, dict):
    print("Codex bridge runtime guard status is missing run_budget_defaults.", file=sys.stderr)
    sys.exit(2)
bridge_tool_budget = int(budget_defaults.get("max_tool_calls", -999999))
if bridge_tool_budget != expected_tool_budget:
    print(
        "Codex bridge tool-call budget mismatch: "
        f"launcher={expected_tool_budget} bridge={bridge_tool_budget}. "
        "Tool-call budgets are enforced in the bridge process, so restart the bridge with matching "
        "LOCAL_QWEN_CODEX_MAX_TOOL_CALLS or use the default -1.",
        file=sys.stderr,
    )
    sys.exit(2)
PY
}

run_mcp_list() {
  prepare_safe_home
  cd "$CODEX_CWD"
  local codex_bin
  codex_bin="$(resolve_codex_bin)"
  CODEX_HOME="$SAFE_HOME" "$codex_bin" mcp list
}

run_mcp_login() {
  if [[ "$#" -lt 1 ]]; then
    echo "--mcp-login requires a server name" >&2
    exit 2
  fi
  prepare_safe_home
  cd "$CODEX_CWD"
  local codex_bin
  codex_bin="$(resolve_codex_bin)"
  CODEX_HOME="$SAFE_HOME" "$codex_bin" mcp login "$1"
}

run_codex() {
  local mode="${1:-interactive}"
  shift || true

  prepare_safe_home
  case "$LOCAL_QWEN_CODEX_SANDBOX_MODE" in
    workspace-write|read-only) ;;
    *)
      echo "Unsupported local Qwen Codex sandbox mode: $LOCAL_QWEN_CODEX_SANDBOX_MODE" >&2
      exit 2
      ;;
  esac
  validate_runtime_budgets
  ensure_backend
  ensure_model_visible
  ensure_codex_bridge
  ensure_bridge_runtime_guard_matches
  if [[ "$mode" != "exec" || "$CODEX_EXEC_MINIMAL" != "1" ]]; then
    check_mcp_bins
  fi
  if [[ -n "$MODEL_CATALOG_JSON" && ! -f "$MODEL_CATALOG_JSON" ]]; then
    echo "Configured local Qwen model catalog does not exist: $MODEL_CATALOG_JSON" >&2
    exit 1
  fi

  cd "$CODEX_CWD"
  export CODEX_HOME="$SAFE_HOME"
  export LOCAL_QWEN_GUARD_PROFILE
  export LOCAL_QWEN_CODEX_MAX_TOOL_CALLS
  export LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS
  export CODEX_OSS_BASE_URL
  local codex_bin
  codex_bin="$(resolve_codex_bin)"
  local goal_args=()
  if [[ "$mode" == "exec" && "$CODEX_EXEC_MINIMAL" == "1" ]]; then
    goal_args=()
  else
    case "$CODEX_GOAL_MODE" in
      off|OFF|0|false|FALSE|no|NO) ;;
      *) goal_args=(--enable goals) ;;
    esac
  fi
  local repo_check_args=()
  case "$CODEX_SKIP_GIT_REPO_CHECK" in
    1|true|TRUE|yes|YES) repo_check_args=(--skip-git-repo-check) ;;
    *) ;;
  esac
  local add_dir_args=()
  if [[ -n "$LOCAL_QWEN_CODEX_ADD_DIRS" ]]; then
    local add_dir
    IFS=':' read -r -a _qwendex_add_dirs <<< "$LOCAL_QWEN_CODEX_ADD_DIRS"
    for add_dir in "${_qwendex_add_dirs[@]}"; do
      if [[ -n "$add_dir" ]]; then
        add_dir_args+=(--add-dir "$add_dir")
      fi
    done
  fi
  local catalog_args=()
  if [[ -n "$MODEL_CATALOG_JSON" ]]; then
    catalog_args=(-c "model_catalog_json=\"$MODEL_CATALOG_JSON\"")
  fi
  local mcp_trusted_roots="${LOCAL_QWEN_LOCAL_HARNESS_TRUSTED_ROOTS:-$CODEX_CWD}"
  local mcp_override_args=(
    -c 'mcp_servers.local-harness.command="python3"'
    -c "mcp_servers.local-harness.args=[\"$LOCAL_HARNESS_MCP\"]"
    -c "mcp_servers.local-harness.cwd=\"$CODEX_CWD\""
    -c "mcp_servers.local-harness.env.ARTIFACT_QUEUE_MCP_TRUSTED_ROOTS=\"$mcp_trusted_roots\""
    -c 'mcp_servers.local-harness.env.SEARXNG_URL="http://127.0.0.1:6060"'
  )
  if [[ "$mode" == "exec" && "$CODEX_EXEC_MINIMAL" == "1" ]]; then
    mcp_override_args=()
  fi

  if [[ "$mode" == "exec" ]]; then
    local exec_args=()
    local timeout_prefix=()
    if [[ "$CODEX_EXEC_MINIMAL" == "1" ]]; then
      exec_args+=(--ignore-user-config -c 'mcp_servers={}')
    fi
    exec_args+=(--sandbox "$LOCAL_QWEN_CODEX_SANDBOX_MODE")
    case "$CODEX_EXEC_EPHEMERAL" in
      1|true|TRUE|yes|YES|on|ON) exec_args+=(--ephemeral) ;;
    esac
    if [[ "$CODEX_EXEC_JSON" == "1" ]]; then
      exec_args+=(--json)
    fi
    if [[ -n "$CODEX_OUTPUT_SCHEMA" ]]; then
      exec_args+=(--output-schema "$CODEX_OUTPUT_SCHEMA")
    fi
    if [[ -n "$CODEX_OUTPUT_LAST_MESSAGE" ]]; then
      exec_args+=(-o "$CODEX_OUTPUT_LAST_MESSAGE")
    fi
    if [[ "$LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS" != "-1" ]]; then
      timeout_prefix=(timeout "$LOCAL_QWEN_CODEX_MAX_WALL_TIME_SECONDS")
    fi
    "${timeout_prefix[@]}" "$codex_bin" exec \
      "${exec_args[@]}" \
      "${goal_args[@]}" \
      "${repo_check_args[@]}" \
      "${add_dir_args[@]}" \
      "${mcp_override_args[@]}" \
      --oss \
      --local-provider lmstudio \
      "${catalog_args[@]}" \
      -c "tool_output_token_limit=$TOOL_OUTPUT_TOKEN_LIMIT" \
      -c "model_context_window=$CODEX_CONTEXT_WINDOW" \
      -c "model_auto_compact_token_limit=$CODEX_AUTO_COMPACT_LIMIT" \
      -c 'approval_policy="never"' \
      -c 'model_reasoning_effort="none"' \
      -c 'model_reasoning_summary="none"' \
      -c 'model_verbosity="low"' \
      -m "$MODEL" \
      -C "$CODEX_CWD" \
      "$@"
    return
  fi

  "$codex_bin" \
    "${goal_args[@]}" \
    "${repo_check_args[@]}" \
    "${add_dir_args[@]}" \
    "${mcp_override_args[@]}" \
    --sandbox "$LOCAL_QWEN_CODEX_SANDBOX_MODE" \
    --oss \
    --local-provider lmstudio \
    "${catalog_args[@]}" \
    -c "tool_output_token_limit=$TOOL_OUTPUT_TOKEN_LIMIT" \
    -c "model_context_window=$CODEX_CONTEXT_WINDOW" \
    -c "model_auto_compact_token_limit=$CODEX_AUTO_COMPACT_LIMIT" \
    -c 'model_reasoning_effort="none"' \
    -c 'model_reasoning_summary="none"' \
    -c 'model_verbosity="low"' \
    -m "$MODEL" \
    -C "$CODEX_CWD" \
    "$@"
}

main() {
  while [[ "$#" -gt 0 ]]; do
    case "${1:-}" in
      --help|-h)
        usage
        exit 0
        ;;
      --cwd)
        shift
        if [[ "$#" -lt 1 ]]; then
          echo "--cwd requires a folder" >&2
          exit 1
        fi
        CODEX_CWD="$1"
        shift
        if [[ ! -d "$CODEX_CWD" ]]; then
          echo "Codex cwd does not exist: $CODEX_CWD" >&2
          exit 1
        fi
        ;;
      --check)
        run_check_only
        exit 0
        ;;
      --ephemeral)
        CODEX_EXEC_EPHEMERAL=1
        shift
        ;;
      --minimal)
        CODEX_EXEC_MINIMAL=1
        shift
        ;;
      --sandbox)
        shift
        if [[ "$#" -lt 1 ]]; then
          echo "--sandbox requires workspace-write or read-only" >&2
          exit 2
        fi
        LOCAL_QWEN_CODEX_SANDBOX_MODE="$1"
        shift
        ;;
      --json)
        CODEX_EXEC_JSON=1
        shift
        ;;
      --output-schema)
        shift
        if [[ "$#" -lt 1 ]]; then
          echo "--output-schema requires a file path" >&2
          exit 2
        fi
        CODEX_OUTPUT_SCHEMA="$1"
        shift
        ;;
      --output-last-message)
        shift
        if [[ "$#" -lt 1 ]]; then
          echo "--output-last-message requires a file path" >&2
          exit 2
        fi
        CODEX_OUTPUT_LAST_MESSAGE="$1"
        if [[ "$CODEX_OUTPUT_LAST_MESSAGE" != /* ]]; then
          CODEX_OUTPUT_LAST_MESSAGE="$LAUNCH_CWD/$CODEX_OUTPUT_LAST_MESSAGE"
        fi
        shift
        ;;
      --fresh-home)
        shift
        if [[ "$#" -lt 1 ]]; then
          echo "--fresh-home requires a directory" >&2
          exit 2
        fi
        set_safe_home "$1"
        shift
        ;;
      --mcp-list)
        run_mcp_list
        exit 0
        ;;
      --mcp-login)
        shift
        run_mcp_login "${1:-}"
        exit 0
        ;;
      --exec)
        shift
        if [[ "$#" -lt 1 ]]; then
          echo "--exec requires a prompt string" >&2
          exit 1
        fi
        run_codex exec "$*"
        exit 0
        ;;
      *)
        run_codex interactive "$@"
        exit 0
        ;;
    esac
  done

  run_codex interactive "$@"
}

main "$@"
