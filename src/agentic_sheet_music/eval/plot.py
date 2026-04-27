"""Score-over-iteration plotting (PNG + ASCII fallback)."""

from __future__ import annotations

import csv
from pathlib import Path


def append_history(csv_path: Path, iteration: int, score: float, notes: str) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not csv_path.exists()
    with csv_path.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["iteration", "score", "notes"])
        w.writerow([iteration, f"{score:.4f}", notes.replace("\n", " ")])


def read_history(csv_path: Path) -> list[tuple[int, float, str]]:
    if not csv_path.exists():
        return []
    out: list[tuple[int, float, str]] = []
    with csv_path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                out.append((int(row["iteration"]), float(row["score"]), row.get("notes", "")))
            except (ValueError, KeyError):
                continue
    return out


def write_png(csv_path: Path, png_path: Path) -> None:
    """Write a matplotlib plot of score vs iteration."""
    history = read_history(csv_path)
    if not history:
        return
    try:
        import matplotlib

        matplotlib.use("Agg")  # headless
        import matplotlib.pyplot as plt
    except ImportError:
        return  # ASCII still works
    iters = [h[0] for h in history]
    scores = [h[1] * 100 for h in history]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.plot(iters, scores, marker="o", linewidth=1.5)
    ax.set_xlabel("iteration")
    ax.set_ylabel("score (%)")
    ax.set_ylim(0, 105)
    ax.axhline(100, color="green", linestyle="--", alpha=0.5, label="target")
    ax.set_title("OMR eval score over autoresearch iterations")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="lower right")
    fig.tight_layout()
    png_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png_path, dpi=120)
    plt.close(fig)


def write_ascii(csv_path: Path, txt_path: Path, height: int = 12) -> None:
    """Write an ASCII version of the same plot."""
    history = read_history(csv_path)
    if not history:
        txt_path.write_text("(no history yet)\n")
        return
    iters = [h[0] for h in history]
    scores = [h[1] * 100 for h in history]
    width = max(40, min(80, len(history) * 3))
    canvas = [[" "] * width for _ in range(height)]
    # Y axis
    for r in range(height):
        canvas[r][0] = "│"
    # X axis
    canvas[height - 1] = ["─"] * width
    canvas[height - 1][0] = "└"
    # plot points
    n = len(history)
    for i, s in enumerate(scores):
        col = 2 + (i * (width - 4) // max(1, n - 1)) if n > 1 else 2
        row = height - 2 - int((s / 100.0) * (height - 2))
        row = max(0, min(height - 2, row))
        canvas[row][col] = "●"
    lines = []
    lines.append(f"score % over iterations  ({iters[0]}..{iters[-1]}, latest={scores[-1]:.1f}%)")
    lines.append("")
    for row, line in enumerate(canvas):
        # Y-axis label every other row
        pct = 100 - int(row * (100 / max(1, height - 2)))
        if row % 2 == 0 and row < height - 1:
            label = f"{pct:3d}%"
        else:
            label = "    "
        lines.append(f"{label} {''.join(line)}")
    lines.append("")
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text("\n".join(lines) + "\n")
