"""Render ``--list-packages`` / ``--list-profilers``.

These are terminal actions: they describe the tree and never touch a run
directory, which is why they live apart from report.py.
"""

from __future__ import annotations

from typing import Dict, List, Mapping, Sequence

import discovery
from discovery import Package, Profiler

__all__ = ["packages_listing", "profilers_listing", "render_table"]


def packages_listing(
    packages: Mapping[str, Package],
    default_profilers: List[str],
    all_profilers: List[str],
) -> List[Dict]:
    rows = []
    for name in sorted(packages):
        pkg = packages[name]
        rows.append(
            {
                "name": name,
                "description": pkg.description,
                "kind": pkg.kind,
                # The same call --profiler all makes, so this is what will run.
                "profilers": discovery.effective_profilers(
                    pkg, default_profilers, all_profilers
                ),
                "declared_profilers": pkg.profilers,
                "entry": pkg.entry_point,
                "workdir": str(pkg.workdir),
            }
        )
    return rows


def profilers_listing(profilers: Mapping[str, Profiler]) -> List[Dict]:
    return [
        {
            "name": name,
            "description": profilers[name].description,
            "kinds": profilers[name].kinds,
            "flamegraph": profilers[name].flamegraph,
            "requires_bin": profilers[name].requires_bin,
        }
        for name in sorted(profilers)
    ]


def render_table(rows: Sequence[Mapping], columns: Sequence["tuple"]) -> str:
    """Render an aligned table.  ``columns`` is a sequence of (header, key-or-
    callable) pairs."""
    if not rows:
        return "(none)"

    def cell(row, accessor):
        value = accessor(row) if callable(accessor) else row.get(accessor, "")
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
