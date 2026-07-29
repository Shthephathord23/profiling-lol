"""Source bash `.env` files and capture the resulting environment.

The harness deliberately delegates `.env` evaluation to bash rather than
parsing it in Python: `$VAR` interpolation, command substitution and
`PATH="...:$PATH"` all then behave exactly as an author would expect.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

__all__ = [
    "EnvFileError",
    "load_layers",
    "base_environment",
    "source_files",
    "apply_real_env",
]


class EnvFileError(RuntimeError):
    """Raised when a `.env` file fails to source."""


# Bash bookkeeping variables that would otherwise pollute every capture and
# make it depend on where the harness happened to be invoked from.
_VOLATILE = frozenset({"_", "PWD", "OLDPWD", "SHLVL", "BASH_ENV"})

# Internal names used by the sourcing snippet below.
_INTERNAL_PREFIX = "__profiling_"

# Variables that a `.env` layer is expected to *extend* rather than replace.
# For these, a layer that changed the inherited value keeps its change even
# though the real process environment normally has the last word; without this
# exception `PATH="$MY_BIN:$PATH"` inside a `.env` could never take effect.
PATHLIKE = frozenset(
    {
        "PATH",
        "PYTHONPATH",
        "LD_LIBRARY_PATH",
        "LD_PRELOAD",
        "MANPATH",
        "PKG_CONFIG_PATH",
        "CPATH",
    }
)

# Sourced in a fresh bash process.  "$@" is the list of files, in order.
# The EXIT trap names the offending file even when a syntax error kills the
# shell outright, which a plain `if ! source` cannot catch.
_SOURCE_SNIPPET = r"""
__profiling_current=""
trap '
  __profiling_status=$?
  if [ "$__profiling_status" -ne 0 ]; then
    printf "__PROFILING_ENV_FAIL__%s\n" "$__profiling_current" >&2
  fi
' EXIT
set -e
set -a
for __profiling_current in "$@"; do
  . "$__profiling_current"
done
set +a
set +e
trap - EXIT
env -0
"""


def load_layers(
    *paths: Path,
    real_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Source `paths` in order, then apply the real environment on top.

    This is the whole operation this module exists to perform, and the only
    one callers should need.  Sourcing and the precedence overlay are never
    useful apart -- an overlay without a source has nothing to overlay, and a
    source without the overlay silently drops layer 4 of the precedence rule.

    Computing the base environment once and handing it to both halves also
    means they cannot disagree about what they inherited, which is what makes
    the PATHLIKE comparison in ``apply_real_env`` meaningful.

    Raises ``EnvFileError`` naming the offending file if any layer fails.
    """
    base = base_environment(real_env)
    sourced = source_files(paths, base=base)
    return apply_real_env(sourced, real_env=real_env, base=base)


def base_environment(real_env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Build the controlled base environment for a sourcing subshell.

    It is the real process environment with bash bookkeeping stripped, so the
    capture does not depend on the caller's working directory or shell nesting
    while still letting `.env` files interpolate the ambient `PATH`.
    """
    src = os.environ if real_env is None else real_env
    env = {
        k: v
        for k, v in src.items()
        if k not in _VOLATILE and not k.startswith(_INTERNAL_PREFIX)
    }
    env.setdefault("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
    # Keep tool output parseable and stable across hosts.
    env.setdefault("LC_ALL", "C.UTF-8")
    return env


def source_files(
    files: Iterable[Path],
    base: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Source `files` in order in one bash process; return the exported env.

    Raises ``EnvFileError`` naming the offending file if any of them fails.
    """
    paths = [str(Path(f)) for f in files]
    if not paths:
        return dict(base if base is not None else base_environment())

    for p in paths:
        if not Path(p).is_file():
            raise EnvFileError(f"env file not found: {p}")

    env = dict(base) if base is not None else base_environment()
    proc = subprocess.run(
        ["bash", "-c", _SOURCE_SNIPPET, "_", *paths],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return _parse(proc, paths)


def _parse(proc: "subprocess.CompletedProcess[bytes]", paths) -> Dict[str, str]:
    stderr = proc.stderr.decode("utf-8", "replace")
    culprit = None
    lines = []
    for line in stderr.splitlines():
        if line.startswith("__PROFILING_ENV_FAIL__"):
            culprit = line[len("__PROFILING_ENV_FAIL__") :].strip() or None
        else:
            lines.append(line)
    detail = "\n".join(lines).strip()

    if proc.returncode != 0:
        where = culprit or (paths[-1] if paths else "<unknown>")
        msg = f"failed to source env file: {where}"
        if detail:
            msg += f"\n{detail}"
        raise EnvFileError(msg)

    if detail:
        # A `.env` may legitimately print warnings; pass them through.
        print(detail, file=sys.stderr)

    out: Dict[str, str] = {}
    for chunk in proc.stdout.split(b"\0"):
        if not chunk:
            continue
        name, sep, value = chunk.partition(b"=")
        if not sep:
            continue
        key = name.decode("utf-8", "replace")
        if key in _VOLATILE or key.startswith(_INTERNAL_PREFIX):
            continue
        out[key] = value.decode("utf-8", "replace")
    return out


def apply_real_env(
    sourced: Mapping[str, str],
    real_env: Optional[Mapping[str, str]] = None,
    base: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Overlay the real process environment -- the highest-priority layer.

    Anything exported by the caller wins over the `.env` files.  The one
    exception is the PATH-like variables in ``PATHLIKE``: if a layer changed
    one relative to the base it inherited, that change is kept, because those
    variables are conventionally extended rather than assigned.
    """
    real = os.environ if real_env is None else real_env
    base = base_environment(real) if base is None else base

    merged = dict(sourced)
    for key, value in real.items():
        if key in _VOLATILE or key.startswith(_INTERNAL_PREFIX):
            continue
        if key in PATHLIKE and merged.get(key, base.get(key)) != base.get(key):
            continue  # a layer deliberately extended it
        merged[key] = value
    return merged
