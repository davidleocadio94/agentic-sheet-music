"""Rebuild eval-runs/MEMORY.md from all iter_*.md files.

Pure Python — no LLM. The agents WRITE iter docs; we cluster them
into a digest the next agent reads at startup.
"""

from __future__ import annotations

import re
from pathlib import Path

CATEGORY_HEADER_RE = re.compile(r"^\s*category:\s*(\w+)", re.IGNORECASE | re.MULTILINE)
SCORE_LINE_RE = re.compile(
    r"##\s*Score:\s*([\d.]+)%\s*→\s*([\d.]+)%\s*\(?Δ?\s*=?\s*([+\-]?[\d.]+)?\)?",
    re.IGNORECASE,
)
OUTCOME_RE = re.compile(r"##\s*Outcome\s*\n+\s*\b(KEPT|REVERTED)\b", re.IGNORECASE)


def rebuild(eval_runs_dir: Path) -> Path:
    eval_runs_dir.mkdir(parents=True, exist_ok=True)
    iter_files = sorted(eval_runs_dir.glob("iter_*.md"))
    docs = [(_parse(f.read_text()), f) for f in iter_files]

    works: list[str] = []
    fails: list[str] = []
    open_hyp: list[str] = []
    pitfalls: list[str] = ["per-call variance is high (≈30 points). N≥3 self-consistency recommended."]

    for d, f in docs:
        line_summary = (
            f"- iter {_iter_number(f.name)}: "
            f"[{d.category or '?'}] {d.description or '(no description)'}"
        )
        if d.delta is not None:
            line_summary += f"  ({d.delta:+.1f}%)"
        if d.outcome and "KEPT" in d.outcome.upper():
            works.append(line_summary)
        elif d.outcome and "REVERT" in d.outcome.upper():
            fails.append(line_summary + (f" — {d.why}" if d.why else ""))

        if d.next_hypothesis:
            open_hyp.append(
                f"- after iter {_iter_number(f.name)}: {d.next_hypothesis.splitlines()[0]}"
            )

    sections: list[str] = ["# What's been tried (most recent first)\n"]
    sections.append("## ❌ DOESN'T WORK")
    sections.extend(reversed(fails) if fails else ["- (nothing yet)"])
    sections.append("\n## ✅ WORKS")
    sections.extend(reversed(works) if works else ["- (nothing yet)"])
    sections.append("\n## 💡 OPEN HYPOTHESES")
    sections.extend(reversed(open_hyp) if open_hyp else ["- (nothing yet)"])
    sections.append("\n## ⚠️ KNOWN PITFALLS")
    sections.extend(f"- {p}" for p in pitfalls)
    sections.append("")

    out_path = eval_runs_dir / "MEMORY.md"
    out_path.write_text("\n".join(sections))
    return out_path


# ---------------------------------------------------------------------------


from dataclasses import dataclass


@dataclass
class _Parsed:
    score_before: float | None = None
    score_after: float | None = None
    delta: float | None = None
    category: str | None = None
    description: str | None = None
    outcome: str | None = None
    why: str | None = None
    next_hypothesis: str | None = None


def _parse(text: str) -> _Parsed:
    out = _Parsed()
    m = SCORE_LINE_RE.search(text)
    if m:
        try:
            out.score_before = float(m.group(1))
            out.score_after = float(m.group(2))
            if m.group(3):
                out.delta = float(m.group(3))
            else:
                out.delta = out.score_after - out.score_before
        except (TypeError, ValueError):
            pass
    cm = CATEGORY_HEADER_RE.search(text)
    if cm:
        out.category = cm.group(1).lower()
    out.description = _section_after(text, "description:")
    om = OUTCOME_RE.search(text)
    if om:
        out.outcome = om.group(1).upper()
    out.why = _section_after(text, "why:")
    out.next_hypothesis = _section_block(text, "## Hypothesis for next iteration")
    return out


def _section_after(text: str, marker: str) -> str | None:
    """Grab the rest of the line (and any directly continuing line) after marker."""
    idx = text.lower().find(marker.lower())
    if idx == -1:
        return None
    rest = text[idx + len(marker):].lstrip()
    # Take up to the next blank line or markdown header.
    chunks: list[str] = []
    for line in rest.splitlines():
        if not line.strip():
            break
        if line.lstrip().startswith("#"):
            break
        chunks.append(line.strip())
    return " ".join(chunks).strip() or None


def _section_block(text: str, header: str) -> str | None:
    """Grab the body of a `## Header` section."""
    idx = text.find(header)
    if idx == -1:
        return None
    after = text[idx + len(header):]
    out_lines: list[str] = []
    for line in after.splitlines():
        if line.startswith("## ") and not line.startswith(header):
            break
        out_lines.append(line)
    return "\n".join(out_lines).strip() or None


def _iter_number(filename: str) -> int:
    m = re.search(r"iter[_-]?(\d+)", filename)
    return int(m.group(1)) if m else 0
