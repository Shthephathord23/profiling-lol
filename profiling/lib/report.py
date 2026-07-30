"""Artifact metadata, the invocation summary, and the listing renderers."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

from discovery import Package, Profiler
from runner import RunResult

__all__ = [
    "git_sha",
    "write_meta",
    "write_summary",
    "update_latest",
    "render_table",
    "packages_listing",
    "profilers_listing",
]

def _is_internal(rel: str) -> bool:
    """meta.json and top-level dotfiles (.argv, .command) are the harness's
    bookkeeping, not run artifacts."""
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
        print(f"WARN: could not update {link}: {exc}", flush=True)


def write_summary(
    output_dir: Path,
    argv: Sequence[str],
    records: Iterable[Dict],
    exit_code: int,
) -> Path:
    """Overwrite ``summary.json`` with this invocation's arguments and results.

    A regression gate downstream then has exactly one file to read.
    """
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


def _counts(records: Iterable[Dict]) -> Dict[str, int]:
    counts = {"ok": 0, "failed": 0, "timeout": 0, "skipped": 0}
    for record in records:
        counts[record.get("status", "failed")] = (
            counts.get(record.get("status", "failed"), 0) + 1
        )
    return counts


# --------------------------------------------------------------- listings ---


def packages_listing(
    packages: Mapping[str, Package],
    default_profilers: List[str],
    all_profilers: List[str],
) -> List[Dict]:
    rows = []
    for name in sorted(packages):
        pkg = packages[name]
        # Must match what `--profiler all` actually runs, ordering included:
        # this list is what a CI job iterates to build its matrix.
        effective = pkg.profilers or default_profilers or all_profilers
        rows.append(
            {
                "name": name,
                "description": pkg.description,
                "kind": pkg.kind,
                "profilers": sorted(set(effective)),
                "declared_profilers": pkg.profilers,
                "entry": pkg.entry_point,
                "workdir": str(pkg.workdir),
            }
        )
    return rows


def profilers_listing(profilers: Mapping[str, Profiler]) -> List[Dict]:
    rows = []
    for name in sorted(profilers):
        prof = profilers[name]
        rows.append(
            {
                "name": name,
                "description": prof.description,
                "kinds": prof.kinds,
                "flamegraph": prof.flamegraph,
                "requires_bin": prof.requires_bin,
            }
        )
    return rows


def render_table(rows: Sequence[Mapping], columns: Sequence["tuple"]) -> str:
    """Render an aligned table.  ``columns`` is a sequence of (header, key)
    pairs."""
    if not rows:
        return "(none)"

    def cell(row, key):
        value = row.get(key, "")
        if isinstance(value, bool):
            return "yes" if value else "no"
        if isinstance(value, (list, tuple)):
            return " ".join(str(v) for v in value)
        return "" if value is None else str(value)

    headers = [h for h, _ in columns]
    body = [[cell(row, acc) for _, acc in columns] for row in rows]
    widths = [
        max(len(headers[i]), *(len(r[i]) for r in body)) for i in range(len(columns))
    ]

    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in body:
        lines.append("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    return "\n".join(lines)
