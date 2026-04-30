#!/usr/bin/env bash
# Stop the autoresearch loop AND restore power management settings.
#
# Usage:  ./scripts/stop-loop.sh

set -euo pipefail

SESSION="improve-omr"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

# 1. Send SIGTERM to the loop (finishes current iter, cleans up).
echo "stopping the loop..."
if [ -f eval-runs/loop.pid ]; then
  PID=$(cat eval-runs/loop.pid)
  kill -TERM "$PID" 2>/dev/null && echo "  sent SIGTERM to pid $PID" || echo "  pid $PID already gone"
fi
pkill -TERM improve-omr-loop 2>/dev/null || true

# Wait up to 60s for clean exit.
for _ in $(seq 1 30); do
  if ! pgrep -f improve-omr-loop >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# Force-kill stragglers.
pkill -9 -f "claude -p" 2>/dev/null || true
pkill -9 improve-omr-loop 2>/dev/null || true
tmux kill-session -t "$SESSION" 2>/dev/null || true
rm -f eval-runs/loop.pid

# 2. Restore lid-close sleep behaviour.
echo "restoring power management..."
if sudo -n pmset -a disablesleep 0 2>/dev/null; then
  echo "  ✓ pmset disablesleep=0 (lid-close sleep restored)"
else
  echo "  prompting sudo to restore lid-close sleep..."
  sudo pmset -a disablesleep 0 || echo "  ⚠ failed; run manually: sudo pmset -a disablesleep 0"
fi

echo "done."
