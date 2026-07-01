#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROXY_SCRIPT="$ROOT/scripts/tabbyapi_responses_proxy.py"
SAFE_HOME="${CODEX_HOME:-$HOME/.codex_lmstudio_safe}"
MODEL="${LOCAL_QWEN_TABBY_MODEL:-qwen36-27Bb}"
LOCAL_BASE="${LOCAL_QWEN_TABBY_LOCAL_BASE:-http://127.0.0.1:1234}"
REMOTE_BASE="${LOCAL_QWEN_TABBY_REMOTE_BASE:-http://100.70.94.39:12345}"
SYSTEM_PROMPT_FILE="${LOCAL_QWEN_TABBY_SYSTEM_PROMPT_FILE:-$SAFE_HOME/tabby_system_prompt.md}"
PROXY_LOG="${LOCAL_QWEN_TABBY_PROXY_LOG:-$SAFE_HOME/logs/tabbyapi_responses_proxy.launch.log}"
HEALTH_LOG="${LOCAL_QWEN_TABBY_HEALTH_LOG:-$SAFE_HOME/logs/tabbyapi_responses_proxy.health.log}"
GEN_LOG="${LOCAL_QWEN_TABBY_GENERATE_LOG:-$SAFE_HOME/logs/tabbyapi_generate_probe.log}"
STATUS_LOG="${LOCAL_QWEN_TABBY_STATUS_LOG:-$SAFE_HOME/logs/tabbyapi_responses_proxy.status.json}"
TOOL_OUTPUT_TOKEN_LIMIT="${LOCAL_QWEN_TABBY_TOOL_OUTPUT_TOKEN_LIMIT:-6000}"
CURL_CONNECT_TIMEOUT="${LOCAL_QWEN_TABBY_CURL_CONNECT_TIMEOUT:-3}"
CURL_MAX_TIME="${LOCAL_QWEN_TABBY_CURL_MAX_TIME:-10}"
GENERATE_CURL_MAX_TIME="${LOCAL_QWEN_TABBY_GENERATE_CURL_MAX_TIME:-45}"
PROXY_VERSION="tabby-responses-proxy-2026-06-09-qwen-recovery-endframe"
PROXY_PID=""

usage() {
  cat <<'EOF'
Usage:
  scripts/run_local_qwen_codex_tabby.sh
  scripts/run_local_qwen_codex_tabby.sh --exec 'Reply with exactly: TABBY_OK'
  scripts/run_local_qwen_codex_tabby.sh --review --uncommitted 'Optional review instructions'
  scripts/run_local_qwen_codex_tabby.sh --review-bounded [path ...]
  scripts/run_local_qwen_codex_tabby.sh --check

Behavior:
  - verifies the remote TabbyAPI endpoint
  - probes remote chat generation once before launch
  - starts or reuses the local responses bridge on 127.0.0.1:1234
  - launches Codex against the local Qwen model through the bridge

Environment overrides:
  LOCAL_QWEN_TABBY_MODEL
  LOCAL_QWEN_TABBY_LOCAL_BASE
  LOCAL_QWEN_TABBY_REMOTE_BASE
  LOCAL_QWEN_TABBY_SYSTEM_PROMPT_FILE
  LOCAL_QWEN_TABBY_NATIVE_TOOLS=1
  LOCAL_QWEN_TABBY_SKIP_GENERATE_PROBE=1
  LOCAL_QWEN_TABBY_TOOL_OUTPUT_TOKEN_LIMIT
  LOCAL_QWEN_TABBY_CURL_CONNECT_TIMEOUT
  LOCAL_QWEN_TABBY_CURL_MAX_TIME
  LOCAL_QWEN_TABBY_GENERATE_CURL_MAX_TIME
  CODEX_HOME
EOF
}

check_base() {
  local base="$1"
  curl --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" -fsS "$base/v1/models" >/dev/null
}

check_base_stable() {
  local base="$1"
  check_base "$base" 2>"$HEALTH_LOG" || return 1
  sleep 1
  check_base "$base" 2>"$HEALTH_LOG"
}

check_proxy_identity() {
  curl --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" -fsS "$LOCAL_BASE/__tabby_proxy_status" >"$STATUS_LOG" 2>"$HEALTH_LOG" || return 1
  grep -q "\"version\": \"$PROXY_VERSION\"" "$STATUS_LOG"
}

local_listen_host() {
  local endpoint="${LOCAL_BASE#http://}"
  endpoint="${endpoint#https://}"
  endpoint="${endpoint%%/*}"
  printf '%s\n' "${endpoint%:*}"
}

local_listen_port() {
  local endpoint="${LOCAL_BASE#http://}"
  endpoint="${endpoint#https://}"
  endpoint="${endpoint%%/*}"
  printf '%s\n' "${endpoint##*:}"
}

stop_stale_proxy() {
  local port
  port="$(local_listen_port)"
  local pids
  pids=$(ss -ltnp 2>/dev/null | awk -v suffix=":$port" '$4 ~ suffix "$" {print $0}' | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' | sort -u)
  if [[ -z "$pids" ]]; then
    return
  fi
  echo "Stopping stale listener on $LOCAL_BASE: $pids"
  for pid in $pids; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  sleep 1
}

check_remote_generate() {
  if [[ "${LOCAL_QWEN_TABBY_SKIP_GENERATE_PROBE:-0}" == "1" ]]; then
    return
  fi

  local payload
  payload=$(printf '{"model":"%s","messages":[{"role":"user","content":"Reply with exactly: TABBY_OK"}],"temperature":0,"max_tokens":24,"add_generation_prompt":true,"template_vars":{"enable_thinking":true,"preserve_thinking":true,"thinking_budget":-1}}' "$MODEL")
  local code
  code=$(curl -sS -o "$GEN_LOG" -w '%{http_code}' \
    --connect-timeout "$CURL_CONNECT_TIMEOUT" \
    --max-time "$GENERATE_CURL_MAX_TIME" \
    -H 'Content-Type: application/json' \
    -X POST "$REMOTE_BASE/v1/chat/completions" \
    -d "$payload" || true)
  if [[ "$code" != "200" ]]; then
    echo "Remote TabbyAPI generation probe failed at $REMOTE_BASE/v1/chat/completions (HTTP $code)" >&2
    echo "Model: $MODEL" >&2
    echo "This usually means the upstream Tabby template or runtime is still failing before Codex is involved." >&2
    echo "Recent probe body saved at: $GEN_LOG" >&2
    exit 1
  fi
}

ensure_remote() {
  if ! check_base "$REMOTE_BASE"; then
    echo "Remote TabbyAPI endpoint is not healthy at $REMOTE_BASE" >&2
    echo "Try: curl -sS $REMOTE_BASE/v1/models" >&2
    exit 1
  fi
  check_remote_generate
}

ensure_proxy() {
  mkdir -p "$(dirname "$PROXY_LOG")"
  if check_base_stable "$LOCAL_BASE" && check_proxy_identity; then
    echo "Reusing healthy local Tabby responses bridge at $LOCAL_BASE"
    return
  fi
  if check_base "$LOCAL_BASE" 2>/dev/null && ! check_proxy_identity; then
    echo "Local listener at $LOCAL_BASE is not the current Tabby responses bridge"
    stop_stale_proxy
  fi

  echo "Starting local Tabby responses bridge at $LOCAL_BASE -> $REMOTE_BASE"
  local native_args=()
  if [[ "${LOCAL_QWEN_TABBY_NATIVE_TOOLS:-0}" == "1" ]]; then
    native_args+=(--native-tools)
  fi
  python "$PROXY_SCRIPT" \
    --listen-host "$(local_listen_host)" \
    --listen-port "$(local_listen_port)" \
    --target-base "$REMOTE_BASE" \
    --system-prompt-file "$SYSTEM_PROMPT_FILE" \
    "${native_args[@]}" >"$PROXY_LOG" 2>&1 &
  PROXY_PID="$!"

  local tries=0
  until check_base "$LOCAL_BASE" 2>"$HEALTH_LOG"; do
    tries=$((tries + 1))
    if [[ "$tries" -ge 20 ]]; then
      echo "Bridge did not become healthy at $LOCAL_BASE" >&2
      echo "--- proxy log ---" >&2
      tail -n 40 "$PROXY_LOG" >&2 || true
      echo "--- health log ---" >&2
      tail -n 20 "$HEALTH_LOG" >&2 || true
      exit 1
    fi
    sleep 1
  done
  if ! check_proxy_identity; then
    echo "Bridge started but did not report expected version $PROXY_VERSION" >&2
    tail -n 40 "$PROXY_LOG" >&2 || true
    exit 1
  fi
}

cleanup_proxy() {
  if [[ -n "${PROXY_PID:-}" ]]; then
    kill "$PROXY_PID" >/dev/null 2>&1 || true
    wait "$PROXY_PID" >/dev/null 2>&1 || true
  fi
}

run_check_only() {
  ensure_remote
  ensure_proxy
  echo "Local bridge ready: $LOCAL_BASE"
  echo "Remote endpoint ready: $REMOTE_BASE"
  echo "Model: $MODEL"
}

require_codex_local_base() {
  case "${LOCAL_BASE%/}" in
    http://127.0.0.1:1234|http://localhost:1234)
      return
      ;;
  esac
  echo "Codex local-provider lmstudio expects the local bridge at http://127.0.0.1:1234." >&2
  echo "Current LOCAL_QWEN_TABBY_LOCAL_BASE is $LOCAL_BASE." >&2
  echo "Use the default local base for interactive/exec/review sessions; non-1234 local bases are only useful for bridge health tests." >&2
  exit 1
}

run_codex() {
  local mode="${1:-interactive}"
  shift || true

  require_codex_local_base
  ensure_remote
  ensure_proxy

  cd "$ROOT"
  export CODEX_HOME="$SAFE_HOME"

  if [[ "$mode" == "exec" ]]; then
    codex exec \
      --enable goals \
      --oss \
      --local-provider lmstudio \
      -c "tool_output_token_limit=$TOOL_OUTPUT_TOKEN_LIMIT" \
      -m "$MODEL" \
      -C "$ROOT" \
      "$@"
    return
  fi

  if [[ "$mode" == "review" ]]; then
    codex \
      --enable goals \
      --oss \
      --local-provider lmstudio \
      -c "tool_output_token_limit=$TOOL_OUTPUT_TOKEN_LIMIT" \
      -m "$MODEL" \
      -C "$ROOT" \
      review \
      "$@"
    return
  fi

  if [[ "$mode" == "review-bounded" ]]; then
    local paths
    local path_args=()
    if [[ "$#" -gt 0 ]]; then
      paths="$*"
      path_args=("$@")
    else
      paths="scripts/tabbyapi_responses_proxy.py scripts/run_local_qwen_codex_tabby.sh results/paper_readiness/tabbyapi_codex_launch_validation.md results/paper_readiness/local_qwen_codex_handoff_packet.md"
      path_args=(
        scripts/tabbyapi_responses_proxy.py
        scripts/run_local_qwen_codex_tabby.sh
        results/paper_readiness/tabbyapi_codex_launch_validation.md
        results/paper_readiness/local_qwen_codex_handoff_packet.md
      )
    fi
    local review_packet
    review_packet="$(
      {
        printf 'Review packet for local Qwen. Do not call tools; review only the provided content.\n'
        printf 'Paths: %s\n\n' "$paths"
        printf '## Git Status\n'
        git status --short -- "${path_args[@]}" | head -120
        printf '\n## Diff Stat\n'
        git diff --stat -- "${path_args[@]}" | head -120
        printf '\n## File Contents Or Diffs\n'
        local path
        for path in "${path_args[@]}"; do
          printf '\n### %s\n' "$path"
          if git ls-files --error-unmatch "$path" >/dev/null 2>&1; then
            git diff -- "$path" | head -900
          elif [[ -f "$path" ]]; then
            sed -n '1,260p' "$path"
          else
            printf 'Missing path: %s\n' "$path"
          fi
        done
      }
    )"
    codex exec \
      --enable goals \
      --oss \
      --local-provider lmstudio \
      -c "tool_output_token_limit=$TOOL_OUTPUT_TOKEN_LIMIT" \
      -m "$MODEL" \
      -C "$ROOT" \
      "You are doing a bounded code review from a precomputed packet. Do not call tools. Lead with concrete bug findings with file paths and line numbers. If no bugs are found, say so clearly and mention residual risks. Review packet follows:\n\n$review_packet"
    return
  fi

  codex \
    --enable goals \
    --oss \
    --local-provider lmstudio \
    -c "tool_output_token_limit=$TOOL_OUTPUT_TOKEN_LIMIT" \
    -m "$MODEL" \
    -C "$ROOT" \
    "$@"
}

main() {
  trap cleanup_proxy EXIT

  if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    usage
    exit 0
  fi

  if [[ "${1:-}" == "--check" ]]; then
    run_check_only
    exit 0
  fi

  if [[ "${1:-}" == "--exec" ]]; then
    shift
    if [[ "$#" -lt 1 ]]; then
      echo "--exec requires a prompt string" >&2
      exit 1
    fi
    run_codex exec "$*"
    exit 0
  fi

  if [[ "${1:-}" == "--review" ]]; then
    shift
    run_codex review "$@"
    exit 0
  fi

  if [[ "${1:-}" == "--review-bounded" ]]; then
    shift
    run_codex review-bounded "$@"
    exit 0
  fi

  run_codex interactive "$@"
}

main "$@"
