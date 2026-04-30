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

# Keep the system fully awake. Two layers:
#  1. caffeinate -d -i -m -s -u   prevents display/idle/disk/system sleep
#  2. pmset disablesleep=1         prevents LID-CLOSE sleep on AC (needs sudo)
# pmset is restored when the loop ends via stop-loop.sh / improve-omr-stop.
echo "configuring power management..."
if sudo -n pmset -a disablesleep 1 2>/dev/null; then
  echo "  ✓ pmset disablesleep=1 — laptop won't sleep on lid close (AC required)"
  echo "  ⚠ when you stop the loop, run: ./scripts/stop-loop.sh"
  echo "    or:  sudo pmset -a disablesleep 0"
else
  echo "  prompting for sudo to disable lid-close sleep…"
  if sudo pmset -a disablesleep 1; then
    echo "  ✓ pmset disablesleep=1 — laptop won't sleep on lid close (AC required)"
    echo "  ⚠ when you stop the loop, run: ./scripts/stop-loop.sh"
  else
    echo "  ⚠ pmset failed; lid close will still pause the loop."
    echo "    Workaround: leave the lid open, plug in AC."
  fi
fi

# DYLD_FALLBACK_LIBRARY_PATH lets cairosvg find /opt/homebrew/lib/libcairo.
# Pass the command directly to tmux (no nested bash -lc) so $@ expands cleanly.
tmux new-session -d -s "$SESSION" \
  env DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib \
  caffeinate -d -i -m -s -u uv run improve-omr "$@"

echo "started in tmux session '$SESSION'."
echo "  attach: tmux attach -t $SESSION"
echo "  watch:  tail -f eval-runs/loop.log"
echo "  kill:   pkill improve-omr-loop  OR  uv run improve-omr-stop"
