"""`--remove-output` pruning.

Retention is per (package, profiler) pair, not global: ``--keep 3`` on a
package with four profilers leaves twelve run directories, which is what you
want when comparing a profiler's history against itself.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from runner import RUN_ID_RE

__all__ = ["RetentionError", "PrunePlan", "check_output_dir", "plan_prune", "apply_plan"]


class RetentionError(Exception):
    """Raised when the output directory fails its safety checks."""


@dataclass
class PrunePlan:
    remove: List[Path]
    kept: List[Path]
    skipped: List[Path]  # directories whose names are not run ids
    relink: List[Path]  # `latest` symlinks needing repair


def check_output_dir(output_dir: Optional[str]) -> Path:
    """Refuse to prune anything that does not look like a real output tree.

    This function stands between a mis-set ``PROFILING_OUTPUT_DIR`` and
    ``shutil.rmtree``, so it is deliberately strict.
    """
    if not output_dir or not str(output_dir).strip():
        raise RetentionError("PROFILING_OUTPUT_DIR is unset; refusing to remove anything")

    path = Path(output_dir).expanduser()
    if not path.is_absolute():
        raise RetentionError(
            f"PROFILING_OUTPUT_DIR must be an absolute path, got {output_dir!r}"
        )

    resolved = path.resolve()
    if resolved == Path(resolved.anchor):
        raise RetentionError(
            f"PROFILING_OUTPUT_DIR resolves to the filesystem root ({resolved}); "
            "refusing to remove anything"
        )
    if len(resolved.parts) < 3:
        raise RetentionError(
            f"PROFILING_OUTPUT_DIR is suspiciously shallow ({resolved}); "
            "point it at a dedicated directory"
        )
    return resolved


def _run_dirs(pair_dir: Path) -> "tuple[List[Path], List[Path]]":
    """Split a `<package>/<profiler>/` directory into run dirs and everything
    else.  Directories whose names do not match the run-id pattern are never
    guessed at -- they are returned as `skipped` and left untouched."""
    runs, other = [], []
    for child in sorted(pair_dir.iterdir()):
        if child.is_symlink() or not child.is_dir():
            continue
        (runs if RUN_ID_RE.match(child.name) else other).append(child)
    return runs, other


def _sort_key(path: Path):
    """Newest last.  Run ids sort lexicographically by construction; mtime is
    the tiebreaker for ids that share a timestamp."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (path.name, mtime)


def plan_prune(
    output_dir: Path,
    keep: int,
    packages: Optional[Sequence[str]] = None,
    profilers: Optional[Sequence[str]] = None,
) -> PrunePlan:
    """Decide what to remove.  Scope follows --package / --profiler; when
    neither is given the whole output tree is in scope."""
    plan = PrunePlan(remove=[], kept=[], skipped=[], relink=[])
    if not output_dir.is_dir():
        return plan

    pkg_filter = set(packages) if packages else None
    prof_filter = set(profilers) if profilers else None

    for pkg_dir in sorted(p for p in output_dir.iterdir() if p.is_dir()):
        if pkg_filter is not None and pkg_dir.name not in pkg_filter:
            continue
        for prof_dir in sorted(p for p in pkg_dir.iterdir() if p.is_dir()):
            if prof_dir.is_symlink():
                continue
            if prof_filter is not None and prof_dir.name not in prof_filter:
                continue

            runs, other = _run_dirs(prof_dir)
            plan.skipped.extend(other)
            runs.sort(key=_sort_key)

            # runs[-keep:] clamps when keep exceeds the count; computing the
            # index as len(runs) - keep would go negative and silently delete
            # runs that --keep asked to preserve.
            survivors = runs[-keep:] if keep > 0 else []
            doomed = runs[: len(runs) - len(survivors)]

            plan.remove.extend(doomed)
            plan.kept.extend(survivors)

            link = prof_dir / "latest"
            if link.is_symlink():
                target = prof_dir / _link_target(link)
                if any(target == d for d in doomed) or not target.exists():
                    plan.relink.append(link)

    return plan


def _link_target(link: Path) -> str:
    try:
        return str(link.readlink())
    except OSError:
        return ""


def apply_plan(plan: PrunePlan, output_dir: Path) -> List[Path]:
    """Delete the planned directories and repair `latest` symlinks."""
    removed: List[Path] = []
    for path in plan.remove:
        _assert_inside(path, output_dir)
        shutil.rmtree(path, ignore_errors=False)
        removed.append(path)

    # Repair links after the deletions so re-pointing sees the final state.
    for link in plan.relink:
        _assert_inside(link, output_dir)
        pair_dir = link.parent
        runs, _ = _run_dirs(pair_dir)
        runs.sort(key=_sort_key)
        try:
            if link.is_symlink():
                link.unlink()
            if runs:
                link.symlink_to(runs[-1].name, target_is_directory=True)
        except OSError as exc:
            print(f"WARN: could not repair {link}: {exc}")
    return removed


def _assert_inside(path: Path, root: Path) -> None:
    """Belt and braces: never touch anything outside the resolved output dir."""
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise RetentionError(
            f"refusing to remove {path}: it resolves outside {root}"
        ) from exc


def iter_pairs(output_dir: Path) -> Iterable["tuple[str, str, Path]"]:
    if not output_dir.is_dir():
        return
    for pkg_dir in sorted(p for p in output_dir.iterdir() if p.is_dir()):
        for prof_dir in sorted(p for p in pkg_dir.iterdir() if p.is_dir()):
            yield pkg_dir.name, prof_dir.name, prof_dir
