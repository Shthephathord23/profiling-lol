"""Filesystem discovery of packages and profilers.

Nothing is hardcoded: a directory under ``packages/`` or ``profilers/`` is a
valid entry iff it contains a ``.env`` file and its name does not start with
``_``.  The underscore prefix reserves ``_template`` and any shared helper
directories.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from envfile import load_layers

__all__ = [
    "DiscoveryError",
    "Entry",
    "Package",
    "Profiler",
    "NAME_RE",
    "discover_packages",
    "discover_profilers",
    "load_package",
    "load_profiler",
    "resolve_selection",
]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

PACKAGE_KINDS = ("python-module", "python-script", "exec")


class DiscoveryError(Exception):
    """Raised for an unknown, malformed or ambiguous package/profiler."""


@dataclass
class Entry:
    """A discovered directory: its name, path and the two leaf files."""

    name: str
    path: Path
    env_file: Path
    script_file: Path

    @property
    def has_script(self) -> bool:
        return self.script_file.is_file()


@dataclass
class Package:
    entry: Entry
    env: Dict[str, str] = field(repr=False, default_factory=dict)

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def description(self) -> str:
        return self.env.get("PACKAGE_DESCRIPTION", "")

    @property
    def kind(self) -> str:
        return self.env.get("PACKAGE_KIND", "python-module").strip()

    @property
    def entry_point(self) -> str:
        return self.env.get("PACKAGE_ENTRY", "").strip()

    @property
    def args(self) -> List[str]:
        return shlex.split(self.env.get("PACKAGE_ARGS", ""))

    @property
    def workdir(self) -> str:
        return self.env.get("PACKAGE_WORKDIR") or self.env.get("REPO_ROOT") or "."

    @property
    def python(self) -> str:
        return self.env.get("PACKAGE_PYTHON") or "python3"

    @property
    def profilers(self) -> List[str]:
        return self.env.get("PACKAGE_PROFILERS", "").split()

    @property
    def init_enabled(self) -> bool:
        """PACKAGE_INIT=1 means "call package_init before this package's runs".

        There is no staleness tracking: the harness calls the hook, and making
        it cheap when there is nothing to do is the hook's own job.  Only the
        package knows what "already built" means for it.
        """
        return (self.env.get("PACKAGE_INIT") or "0").strip() == "1"

    @property
    def timeout(self) -> Optional[float]:
        raw = (self.env.get("PACKAGE_TIMEOUT") or "").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError as exc:
            raise DiscoveryError(
                f"package '{self.name}': PACKAGE_TIMEOUT must be a number, got {raw!r}"
            ) from exc
        return None if value <= 0 else value


@dataclass
class Profiler:
    entry: Entry
    env: Dict[str, str] = field(repr=False, default_factory=dict)

    @property
    def name(self) -> str:
        return self.entry.name

    @property
    def description(self) -> str:
        return self.env.get("PROFILER_DESCRIPTION", "")

    @property
    def kinds(self) -> List[str]:
        return self.env.get("PROFILER_KINDS", "").split()

    @property
    def flamegraph(self) -> bool:
        return (self.env.get("PROFILER_FLAMEGRAPH") or "0").strip() == "1"

    @property
    def flamegraph_file(self) -> str:
        return (self.env.get("PROFILER_FLAMEGRAPH_FILE") or "").strip()

    @property
    def requires_bin(self) -> List[str]:
        return self.env.get("PROFILER_REQUIRES_BIN", "").split()

    def supports(self, kind: str) -> bool:
        # An empty PROFILER_KINDS means "no restriction declared"; be permissive
        # rather than silently skipping every run.
        return not self.kinds or kind in self.kinds


def _scan(root: Path, label: str, script_name: str) -> Dict[str, Entry]:
    if not root.is_dir():
        raise DiscoveryError(f"{label} directory not found: {root}")

    found: Dict[str, Entry] = {}
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith("_") or child.name.startswith("."):
            continue
        env_file = child / ".env"
        if not env_file.is_file():
            # Not an entry at all (stray directory, editor cruft); ignore.
            continue
        if not NAME_RE.match(child.name):
            raise DiscoveryError(
                f"invalid {label} name {child.name!r} in {root}: names must match "
                f"{NAME_RE.pattern} (lowercase letters, digits, '.', '_', '-')"
            )
        found[child.name] = Entry(
            name=child.name,
            path=child,
            env_file=env_file,
            script_file=child / script_name,
        )
    return found


def discover_packages(profiling_root: Path) -> Dict[str, Entry]:
    return _scan(profiling_root / "packages", "package", "package.sh")


def discover_profilers(profiling_root: Path) -> Dict[str, Entry]:
    return _scan(profiling_root / "profilers", "profiler", "profiler.sh")


def load_profiler(config_env: Path, entry: Entry) -> Profiler:
    """Load a profiler with only config.env beneath it (listing / metadata)."""
    return Profiler(entry=entry, env=load_layers(config_env, entry.env_file))


def load_package(
    config_env: Path,
    entry: Entry,
    profiler_env_file: Optional[Path] = None,
) -> Package:
    """Load a package.

    When ``profiler_env_file`` is given the layering is the full run stack --
    config.env, then the profiler, then the package (§5) -- which is what lets
    a package tune a profiler for itself.
    """
    layers = [profiler_env_file] if profiler_env_file else []
    layers.append(entry.env_file)
    return Package(entry=entry, env=load_layers(config_env, *layers))


def resolve_selection(
    values: List[str],
    available: Dict[str, Entry],
    label: str,
) -> List[str]:
    """Expand `--package`/`--profiler` selectors into an ordered name list.

    Accepts repeated flags, comma-separated lists, and the literal ``all``.
    Order follows the command line; ``all`` expands alphabetically.  Duplicates
    are collapsed, keeping first appearance.
    """
    names: List[str] = []
    for value in values:
        for piece in value.split(","):
            piece = piece.strip()
            if piece:
                names.append(piece)

    if not names:
        return []

    if "all" in names:
        if len(names) > 1:
            raise DiscoveryError(
                f"--{label} 'all' cannot be combined with explicit names: "
                + ", ".join(n for n in names if n != "all")
            )
        return sorted(available)

    resolved: List[str] = []
    for name in names:
        if name not in available:
            known = ", ".join(sorted(available)) or "(none)"
            raise DiscoveryError(f"unknown {label} {name!r}; available: {known}")
        if name not in resolved:
            resolved.append(name)
    return resolved
