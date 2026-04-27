"""Pre/post-iteration snapshots for guardrail enforcement."""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PYPROJECT = REPO_ROOT / "pyproject.toml"
UV_LOCK = REPO_ROOT / "uv.lock"

# Files the agent must NOT modify during a loop iteration.
IMMUTABLE_PATHS = (
    "src/agentic_sheet_music/eval/evaluator.py",
    "eval-fixtures",
    "tests/eval",
)

REPO_SIZE_LIMIT_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB


@dataclass(frozen=True)
class Snapshot:
    git_head: str
    repo_size_bytes: int
    pyproject_hash: str
    uv_lock_hash: str
    brew_list: tuple[str, ...]
    immutable_hashes: dict[str, str]  # path -> sha256


def take(repo_root: Path = REPO_ROOT) -> Snapshot:
    return Snapshot(
        git_head=_git_head(repo_root),
        repo_size_bytes=_repo_size(repo_root),
        pyproject_hash=_file_hash(repo_root / "pyproject.toml"),
        uv_lock_hash=_file_hash(repo_root / "uv.lock"),
        brew_list=_brew_list(),
        immutable_hashes=_immutable_hashes(repo_root),
    )


@dataclass
class GuardrailViolation:
    name: str
    detail: str


def diff(before: Snapshot, after: Snapshot) -> list[GuardrailViolation]:
    """Return list of violations introduced between before and after."""
    out: list[GuardrailViolation] = []
    if after.repo_size_bytes > REPO_SIZE_LIMIT_BYTES:
        out.append(
            GuardrailViolation(
                "repo-size",
                f"{after.repo_size_bytes / 1e9:.2f} GB exceeds 2 GB cap",
            )
        )
    for path, h in before.immutable_hashes.items():
        if after.immutable_hashes.get(path) != h:
            out.append(
                GuardrailViolation("immutable-path", f"{path} was modified")
            )
    return out


def _git_head(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def _repo_size(root: Path) -> int:
    """Total size of `root` excluding .venv, .git, outputs, eval-runs candidates."""
    skip = {".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
            ".mypy_cache", "outputs", "node_modules"}
    total = 0
    for p in root.rglob("*"):
        if any(part in skip for part in p.parts):
            continue
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def _file_hash(path: Path) -> str:
    if not path.exists():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _brew_list() -> tuple[str, ...]:
    try:
        out = subprocess.check_output(["brew", "list", "--versions"], text=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ()
    return tuple(sorted(line.strip() for line in out.splitlines() if line.strip()))


def _immutable_hashes(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel in IMMUTABLE_PATHS:
        p = root / rel
        if p.is_file():
            out[rel] = _file_hash(p)
        elif p.is_dir():
            for sub in sorted(p.rglob("*")):
                if sub.is_file():
                    rel_sub = sub.relative_to(root).as_posix()
                    out[rel_sub] = _file_hash(sub)
    return out


def write(snapshot: Snapshot, path: Path) -> None:
    payload = {
        "git_head": snapshot.git_head,
        "repo_size_bytes": snapshot.repo_size_bytes,
        "repo_size_mb": round(snapshot.repo_size_bytes / 1e6, 1),
        "pyproject_hash": snapshot.pyproject_hash,
        "uv_lock_hash": snapshot.uv_lock_hash,
        "brew_list": list(snapshot.brew_list),
        "immutable_hashes_count": len(snapshot.immutable_hashes),
    }
    path.write_text(json.dumps(payload, indent=2))
