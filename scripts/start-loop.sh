#!/usr/bin/env bash
# Start the autoresearch loop in a detached tmux session.
# Survives terminal close; killable from any other terminal.
#
# Usage:
#   ./scripts/start-loop.sh                # default: 8 hours
#   ./scripts/start-loop.sh --max-hours 4
#
# Then:
#   tmux attach -t improve-omr             # peek (Ctrl-B then D to detach)
#   tail -f eval-runs/loop.log             # watch log without tmux
#   pkill improve-omr-loop                 # clean stop
#   uv run improve-omr-stop                # same thing, friendlier

set -euo pipefail

SESSION="improve-omr"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux not installed. Install with: brew install tmux" >&2
  exit 1
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "loop is already running in tmux session '$SESSION'."
  echo "  attach: tmux attach -t $SESSION"
  echo "  kill:   pkill improve-omr-loop  OR  tmux kill-session -t $SESSION"
  exit 1
fi

# `caffeinate -i` keeps the system from idle-sleeping while the loop runs.
# DYLD_FALLBACK_LIBRARY_PATH lets cairosvg find /opt/homebrew/lib/libcairo.
CMD=$(cat <<EOF
DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib caffeinate -i uv run improve-omr "$@"
echo
echo "loop exited. press Enter to close this tmux pane..."
read -r
EOF
)

tmux new-session -d -s "$SESSION" "bash -lc '$CMD'"

echo "started in tmux session '$SESSION'."
echo "  attach: tmux attach -t $SESSION"
echo "  watch:  tail -f eval-runs/loop.log"
echo "  kill:   pkill improve-omr-loop  OR  uv run improve-omr-stop"
