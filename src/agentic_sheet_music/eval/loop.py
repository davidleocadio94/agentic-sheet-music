"""Autoresearch loop: spawn `claude -p` agents until score == 1.0.

Run with: `uv run improve-omr` (or wrap with tmux + caffeinate).
Kill with: pkill improve-omr-loop / kill $(cat eval-runs/loop.pid) / Ctrl-C.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from setproctitle import setproctitle
except ImportError:  # graceful fallback
    def setproctitle(_name: str) -> None:
        pass

from agentic_sheet_music.eval import memory, plot, snapshot
from agentic_sheet_music.eval.runner import run as run_eval

REPO_ROOT = Path(__file__).resolve().parents[3]
EVAL_RUNS = REPO_ROOT / "eval-runs"
PID_FILE = EVAL_RUNS / "loop.pid"
LOG_FILE = EVAL_RUNS / "loop.log"
HISTORY_CSV = EVAL_RUNS / "score-history.csv"
PLOT_PNG = EVAL_RUNS / "score-plot.png"
PLOT_TXT = EVAL_RUNS / "score-plot.txt"
MEMORY_MD = EVAL_RUNS / "MEMORY.md"

DEFAULT_MAX_HOURS = 8.0
AGENT_HARD_TIMEOUT_SEC = 20 * 60
AGENT_NAME_TEMPLATE = "improve-omr-iter-{n}"
LOOP_NAME = "improve-omr-loop"

_should_stop = False


def _signal_handler(signum, frame):  # noqa: ARG001
    global _should_stop
    _should_stop = True
    print(f"\n[loop] caught signal {signum}, will stop after current iteration", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="improve-omr")
    parser.add_argument("--max-hours", type=float, default=DEFAULT_MAX_HOURS)
    parser.add_argument(
        "--target-score", type=float, default=1.0,
        help="Stop when this score is reached (default 1.0)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Run a single iteration and exit (for testing)",
    )
    args = parser.parse_args(argv)

    setproctitle(LOOP_NAME)
    EVAL_RUNS.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    started = time.monotonic()
    iteration = _next_iter_number()
    _log(f"loop started; pid={os.getpid()}; max_hours={args.max_hours}; "
         f"first iter={iteration}")
    print(f"[loop] started, pid={os.getpid()}, max_hours={args.max_hours}", flush=True)

    try:
        # Take an initial baseline if no history yet.
        if not HISTORY_CSV.exists():
            _print_banner("baseline eval")
            score = _run_eval_and_record(iteration=iteration, note="baseline")
            print(f"[loop] baseline score: {score:.1%}", flush=True)
            if score >= args.target_score:
                _print_banner(f"already at {score:.1%} ≥ target {args.target_score}, exiting")
                return 0
            iteration += 1

        while True:
            elapsed_h = (time.monotonic() - started) / 3600.0
            if _should_stop:
                _log("user quit; exiting cleanly")
                break
            if elapsed_h >= args.max_hours:
                _log(f"max hours reached ({elapsed_h:.2f}h); exiting cleanly")
                print(f"[loop] max hours reached ({elapsed_h:.2f}h); exit", flush=True)
                break

            outcome = _run_one_iter(iteration)
            if outcome is None:  # eval failed before agent could run
                iteration += 1
                continue

            if outcome.score >= args.target_score:
                _print_banner(f"🎯 SUCCESS: score {outcome.score:.1%} ≥ target")
                _log(f"100% reached at iter {iteration}, exiting")
                return 0

            if args.once:
                _log("--once specified, exiting")
                break

            iteration += 1

        return 0
    finally:
        try:
            PID_FILE.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------


from dataclasses import dataclass


@dataclass
class IterOutcome:
    iteration: int
    score: float
    delta: float
    kept: bool
    note: str


def _run_one_iter(iteration: int) -> IterOutcome | None:
    _print_banner(f"iter {iteration} starting")
    pre = snapshot.take()
    pre_path = EVAL_RUNS / f"iter_{iteration:03d}.snapshot-pre.json"
    snapshot.write(pre, pre_path)

    # Pre-iter score is the last entry in score-history.csv (or 0).
    history = plot.read_history(HISTORY_CSV)
    pre_score = history[-1][1] if history else 0.0

    # Rebuild MEMORY.md so the agent reads fresh state.
    memory.rebuild(EVAL_RUNS)

    # Spawn the agent. It is responsible for:
    #   - reading MEMORY.md and recent iter_*.md
    #   - picking ONE experiment
    #   - implementing it (Edit/Write)
    #   - running `uv run eval --json eval-runs/iter_NNN.json --notes ...`
    #   - committing or reverting
    #   - writing eval-runs/iter_NNN.md
    agent_result = _spawn_agent(iteration)

    # Post-iter: read the new score from this iter's eval JSON if present.
    iter_json = EVAL_RUNS / f"iter_{iteration:03d}.json"
    if iter_json.exists():
        try:
            data = json.loads(iter_json.read_text())
            new_score = float(data.get("score", pre_score))
        except Exception:  # noqa: BLE001
            new_score = pre_score
    else:
        # Agent didn't run eval; we run it once to be sure.
        new_score = _run_eval_and_record(iteration, note="post-iter forced eval")

    delta = new_score - pre_score
    _log(f"iter {iteration}: score {pre_score:.4f} → {new_score:.4f} (Δ {delta:+.4f}); "
         f"agent_exit={agent_result}")

    # Guardrails enforced here.
    post = snapshot.take()
    violations = snapshot.diff(pre, post)
    if violations:
        for v in violations:
            print(f"[loop] GUARDRAIL: {v.name}: {v.detail}", flush=True)
            _log(f"GUARDRAIL VIOLATION at iter {iteration}: {v.name}: {v.detail}")
        # Hard reset to pre-iter HEAD; rolls back any commits the agent made.
        _git_reset_to(pre.git_head)
        new_score = pre_score
        delta = 0.0
        _log(f"iter {iteration}: reverted to HEAD={pre.git_head} due to guardrails")

    plot.append_history(HISTORY_CSV, iteration, new_score,
                         note=f"agent_exit={agent_result}")
    plot.write_png(HISTORY_CSV, PLOT_PNG)
    plot.write_ascii(HISTORY_CSV, PLOT_TXT)

    print(f"[loop] iter {iteration}: {pre_score:.1%} → {new_score:.1%} "
          f"(Δ {delta:+.1%})", flush=True)

    return IterOutcome(
        iteration=iteration,
        score=new_score,
        delta=delta,
        kept=delta > 0,
        note=f"agent_exit={agent_result}",
    )


def _spawn_agent(iteration: int) -> str:
    """Spawn `claude -p` for one iteration, return short status string."""
    if shutil.which("claude") is None:
        _log("ERROR: `claude` not on PATH; loop cannot continue without it")
        print("[loop] ERROR: `claude` CLI not on PATH — install Claude Code first.",
              flush=True)
        return "claude-not-installed"

    prompt = _build_agent_prompt(iteration)

    cmd = [
        "claude",
        "-p", prompt,
        "--allowed-tools",
        "Read,Edit,Write,Glob,Grep,Bash,WebSearch,WebFetch,Agent",
        "--max-turns", "60",
        "--permission-mode", "acceptEdits",
    ]

    iter_log = EVAL_RUNS / f"iter_{iteration:03d}.agent.log"
    HEARTBEAT_WINDOW_SEC = 5 * 60  # kill if log unchanged for this long
    POLL_SEC = 15
    try:
        with iter_log.open("w") as logf:
            proc = subprocess.Popen(
                cmd,
                stdout=logf,
                stderr=subprocess.STDOUT,
                cwd=REPO_ROOT,
                env={**os.environ, "DYLD_FALLBACK_LIBRARY_PATH": "/opt/homebrew/lib"},
            )
            start = time.monotonic()
            last_size = 0
            last_growth = start
            while True:
                if _should_stop:
                    _log(f"iter {iteration}: user stop, killing agent pid={proc.pid}")
                    _kill_proc(proc)
                    return "user-stop"
                rc = proc.poll()
                if rc is not None:
                    return f"rc={rc}"
                elapsed = time.monotonic() - start
                if elapsed >= AGENT_HARD_TIMEOUT_SEC:
                    _log(
                        f"iter {iteration}: hard timeout {elapsed:.0f}s, "
                        f"killing pid={proc.pid}"
                    )
                    _kill_proc(proc)
                    return "hard-timeout"
                size = iter_log.stat().st_size if iter_log.exists() else 0
                if size > last_size:
                    last_size = size
                    last_growth = time.monotonic()
                stalled = time.monotonic() - last_growth
                if stalled >= HEARTBEAT_WINDOW_SEC:
                    _log(
                        f"iter {iteration}: agent idle for {stalled:.0f}s "
                        f"(log size {size}); assuming hung, killing pid={proc.pid}"
                    )
                    _kill_proc(proc)
                    return "heartbeat-hang"
                time.sleep(POLL_SEC)
    except Exception as e:  # noqa: BLE001
        _log(f"iter {iteration}: agent spawn failed: {e}")
        return f"spawn-failed: {e}"


def _kill_proc(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _build_agent_prompt(iteration: int) -> str:
    iter_json = f"eval-runs/iter_{iteration:03d}.json"
    iter_md = f"eval-runs/iter_{iteration:03d}.md"
    return f"""\
You are running iteration {iteration} of the OMR autoresearch loop.

Read these files first, in order:
1. .claude/CLAUDE.md
2. .claude/rules/loop-guardrails.md
3. eval-runs/MEMORY.md   (rolling digest of past attempts)
4. The most recent 5 eval-runs/iter_*.md files (skim, don't deep-read every one)
5. eval-runs/score-history.csv

Then:
1. Pick ONE experiment from one of the categories in loop-guardrails.md.
   Don't repeat experiments listed in MEMORY.md's "❌ DOESN'T WORK"
   without justification.
2. Implement the change. You may write/edit/delete files. You may
   `uv add` and `brew install`. Do NOT touch eval-fixtures, the
   evaluator, or eval tests.
3. Run the eval and write its JSON to {iter_json}:
   `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib uv run eval --notes "iter {iteration}: <experiment>" --json {iter_json}`
4. Compare the new score to the previous score in score-history.csv.
   - If higher: `git add -A && git commit -m "[<category>] <one-line>"`
   - If equal or lower: revert your changes (`git checkout -- .` and remove
     any new files), AND uninstall any deps you added.
5. Write {iter_md} following the format in loop-guardrails.md.
   Be honest about what didn't work. Future agents read this.

You have 20 minutes hard timeout. Wrap up at 10 minutes if not converged.
Quality of the iter doc matters more than chasing one more retry.
"""


def _run_eval_and_record(iteration: int, note: str) -> float:
    iter_json = EVAL_RUNS / f"iter_{iteration:03d}.json"
    summary = run_eval(REPO_ROOT / "eval-fixtures", notes=note)
    iter_json.write_text(json.dumps({
        "model": summary.model,
        "notes": summary.notes,
        "score": summary.score,
        "perfect": summary.perfect,
        "total_fixtures": summary.total_fixtures,
        "passed_fixtures": summary.passed_fixtures,
        "total_measures": summary.total_measures,
        "matched_measures": summary.matched_measures,
        "breakdown": summary.breakdown,
    }, indent=2, default=str))
    plot.append_history(HISTORY_CSV, iteration, summary.score, note)
    plot.write_png(HISTORY_CSV, PLOT_PNG)
    plot.write_ascii(HISTORY_CSV, PLOT_TXT)
    return summary.score


def _next_iter_number() -> int:
    history = plot.read_history(HISTORY_CSV)
    return (history[-1][0] + 1) if history else 1


def _git_reset_to(commit: str) -> None:
    if not commit:
        return
    try:
        subprocess.run(["git", "reset", "--hard", commit], cwd=REPO_ROOT, check=True)
        subprocess.run(["git", "clean", "-fd"], cwd=REPO_ROOT, check=True)
    except subprocess.CalledProcessError as e:
        _log(f"git reset failed: {e}")


def _log(message: str) -> None:
    EVAL_RUNS.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"{ts} {message}\n"
    with LOG_FILE.open("a") as f:
        f.write(line)


def _print_banner(text: str) -> None:
    bar = "═" * (len(text) + 4)
    print(f"\n╔{bar}╗\n║  {text}  ║\n╚{bar}╝", flush=True)


def stop_main(_argv: list[str] | None = None) -> int:
    """Companion CLI: `uv run improve-omr-stop`. Kills any running loop."""
    if not PID_FILE.exists():
        print("no running loop (eval-runs/loop.pid not found)")
        return 1
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        print("eval-runs/loop.pid is corrupt")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"sent SIGTERM to pid {pid}")
        return 0
    except ProcessLookupError:
        print(f"pid {pid} not running; cleaning up stale pid file")
        PID_FILE.unlink(missing_ok=True)
        return 0


if __name__ == "__main__":
    sys.exit(main())
