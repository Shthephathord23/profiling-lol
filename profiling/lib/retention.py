"""`--remove-output` pruning.

The output tree has exactly one shape -- ``<output>/<package>/<profiler>/<run>``
-- so pruning is: walk each package/profiler pair, sort its runs, keep the
newest N, delete the rest.  Anything else found in a pair directory goes with
them; the tree is the harness's to manage, not somewhere to keep things.

Retention is per pair, not global: ``--keep 3`` on a package with four
profilers leaves twelve run directories, which is what you want when comparing
a profiler's history against itself.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import List, Optional, Sequence


class RetentionError(Exception):
    """Raised when the output directory fails its safety checks."""


def check_output_dir(output_dir: Optional[str]) -> Path:
    """Refuse to prune anything that does not look like a real output tree.

    This function stands between a mis-set ``PROFILING_OUTPUT_DIR`` and
    ``shutil.rmtree``, so it is deliberately strict -- the more so because the
    tree is meant to live outside the repository, where a mistake is not
    something you would notice in ``git status``.
    """
    if not (output_dir or "").strip():
        raise RetentionError(
            "PROFILING_OUTPUT_DIR is unset; refusing to remove anything"
        )

    path = Path(output_dir).expanduser()
    if not path.is_absolute():
        raise RetentionError(
            f"PROFILING_OUTPUT_DIR must be an absolute path, got {output_dir!r}"
        )

    resolved = path.resolve()
    if len(resolved.parts) < 3:
        raise RetentionError(
            f"PROFILING_OUTPUT_DIR is suspiciously shallow ({resolved}); "
            "point it at a dedicated directory"
        )
    return resolved


def prune(
    output_dir: Path,
    keep: int,
    packages: Optional[Sequence[str]] = None,
    profilers: Optional[Sequence[str]] = None,
    dry_run: bool = False,
) -> List[Path]:
    """Keep the newest `keep` runs per package/profiler pair; remove the rest.

    Scope follows --package / --profiler; with neither, the whole tree.
    Returns what was removed (or would be, under ``dry_run``).
    """
    removed: List[Path] = []
    if not output_dir.is_dir():
        return removed

    for pair in sorted(output_dir.glob("*/*")):
        if not pair.is_dir() or pair.is_symlink():
            continue
        if packages and pair.parent.name not in packages:
            continue
        if profilers and pair.name not in profilers:
            continue

        # Newest last.  mtime rather than name, because run ids are only
        # chronological when the harness generated them -- a CI-supplied
        # RUN_ID like "build-9" would sort after "build-10".
        runs = sorted(
            (d for d in pair.iterdir() if d.is_dir() and not d.is_symlink()),
            key=lambda d: (d.stat().st_mtime, d.name),
        )
        survivors = runs[-keep:] if keep > 0 else []

        for doomed in runs[: len(runs) - len(survivors)]:
            removed.append(doomed)
            if not dry_run:
                shutil.rmtree(doomed)

        if not dry_run:
            _relink_latest(pair, survivors)

    return removed


def _relink_latest(pair: Path, survivors: Sequence[Path]) -> None:
    """Point `latest` at the newest survivor, or drop it when none is left."""
    link = pair / "latest"
    try:
        if link.is_symlink():
            link.unlink()
        if survivors:
            link.symlink_to(survivors[-1].name, target_is_directory=True)
    except OSError as exc:
        print(f"WARN: could not update {link}: {exc}")
