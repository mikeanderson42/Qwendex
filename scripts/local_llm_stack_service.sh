#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "${1:-}" == "view" ]]; then
  shift
  exec python3 "$ROOT/scripts/local_llm_stack.py" attach "${1:-textgen}"
fi

if [[ "${1:-}" == "menu" || "${1:-}" == "ui" ]]; then
  exec python3 "$ROOT/scripts/local_llm_stack.py" ui
fi

if [[ "${1:-}" == "" ]]; then
  exec python3 "$ROOT/scripts/local_llm_stack.py" start all
fi

exec python3 "$ROOT/scripts/local_llm_stack.py" "$@"
