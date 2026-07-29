"""What a run leaves behind: meta.json, summary.json, and the latest symlink."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from discovery import Package, Profiler
from runner import RunResult

__all__ = [
    "git_sha",
    "write_meta",
    "write_summary",
    "update_latest",
]


def _is_internal(rel: str) -> bool:
    """The runner's own bookkeeping is not an artifact.  Dotfiles at the top of a
    run directory are reserved for it, which covers .argv and .command."""
    return rel == "meta.json" or rel.startswith(".")


def git_sha(cwd: Path) -> Optional[str]:
    """Best-effort commit sha; ``None`` when git is unavailable or cwd is not a
    repository.  Never raises -- a missing sha must not fail a run."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    sha = proc.stdout.decode("ascii", "ignore").strip()
    return sha or None


def _artifacts(run_dir: Path) -> List[str]:
    if not run_dir.is_dir():
        return []
    names = []
    for child in run_dir.rglob("*"):
        if not child.is_file():
            continue
        rel = child.relative_to(run_dir).as_posix()
        if _is_internal(rel):
            continue
        names.append(rel)
    return sorted(names)


def write_meta(
    result: RunResult,
    package: Package,
    profiler: Profiler,
    repo_root: Path,
    overrides: Optional[Dict[str, Dict[str, str]]] = None,
) -> Dict:
    """Write ``<RUN_DIR>/meta.json`` and return the record."""
    run_dir = result.run_dir
    assert run_dir is not None

    flamegraph = None
    if profiler.flamegraph and profiler.flamegraph_file:
        candidate = run_dir / profiler.flamegraph_file
        if candidate.is_file():
            flamegraph = profiler.flamegraph_file

    meta = {
        "package": result.package,
        "profiler": result.profiler,
        "run_id": result.run_id,
        "status": result.status,
        "exit_code": result.exit_code,
        "duration_s": round(result.duration_s, 3),
        "started_at": result.started_at,
        "finished_at": result.finished_at,
        "argv": result.argv,
        "command": result.command,
        "kind": package.kind,
        "workdir": str(package.workdir),
        "forced": result.forced,
        "flamegraph": flamegraph,
        "artifacts": _artifacts(run_dir),
        "host": platform.node(),
    }
    if result.reason:
        meta["reason"] = result.reason
    if overrides:
        # Provenance for the --env-* layer: which keys the command line forced,
        # and at which level.  Absent when nothing was overridden.
        meta["env_overrides"] = overrides

    sha = git_sha(repo_root)
    if sha:  # omit the field entirely rather than erroring when git is absent
        meta["git_sha"] = sha

    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2) + "\n", "utf-8")
    return meta


def update_latest(run_dir: Path) -> None:
    """Point ``<package>/<profiler>/latest`` at this run, relatively."""
    link = run_dir.parent / "latest"
    try:
        if link.is_symlink() or link.exists():
            if link.is_dir() and not link.is_symlink():
                return  # a real directory named "latest": leave it alone
            link.unlink()
        link.symlink_to(run_dir.name, target_is_directory=True)
    except OSError as exc:
        print(f"WARN: could not update {link}: {exc}", file=sys.stderr)


def write_summary(
    output_dir: Path,
    argv: Sequence[str],
    records: Sequence[Dict],
    exit_code: int,
) -> Path:
    """Overwrite ``summary.json`` with this invocation's arguments and results.

    A regression gate downstream then has exactly one file to read.
    """
    # Read twice below, so anything one-shot has to be materialised first.
    records = list(records)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "invocation": {
            "argv": list(argv),
            "cwd": os.getcwd(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            "host": platform.node(),
        },
        "exit_code": exit_code,
        "counts": _counts(records),
        "runs": list(records),
    }
    path = output_dir / "summary.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", "utf-8")
    return path


def _counts(records: Sequence[Dict]) -> Dict[str, int]:
    counts = {"ok": 0, "failed": 0, "timeout": 0, "skipped": 0}
    for record in records:
        counts[record.get("status", "failed")] = (
            counts.get(record.get("status", "failed"), 0) + 1
        )
    return counts
