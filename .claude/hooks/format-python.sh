#!/usr/bin/env bash
# Auto-format Python files after Edit/Write, if ruff is available.
# Reads Claude Code hook JSON on stdin; formats the affected file if it's .py.
set -euo pipefail

input=$(cat)
file_path=$(printf '%s' "$input" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d.get("tool_input",{}).get("file_path",""))' 2>/dev/null || true)

if [[ -z "${file_path}" || "${file_path##*.}" != "py" ]]; then
  exit 0
fi

if command -v ruff >/dev/null 2>&1; then
  ruff format "$file_path" >/dev/null 2>&1 || true
  ruff check --fix "$file_path" >/dev/null 2>&1 || true
fi

exit 0
