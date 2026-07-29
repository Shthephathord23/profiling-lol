"""Source bash `.env` files and capture the resulting environment.

The harness deliberately delegates `.env` evaluation to bash rather than
parsing it in Python: `$VAR` interpolation, command substitution and
`PATH="...:$PATH"` all then behave exactly as an author would expect.

See ``load_layers`` for the precedence rule.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional

__all__ = ["EnvFileError", "load_layers", "base_environment", "source_files"]


class EnvFileError(RuntimeError):
    """Raised when a `.env` file fails to source."""


# Bash bookkeeping variables that would otherwise pollute every capture and
# make it depend on where the harness happened to be invoked from.
_VOLATILE = frozenset({"_", "PWD", "OLDPWD", "SHLVL", "BASH_ENV"})

# Internal names used by the sourcing snippet below.
_INTERNAL_PREFIX = "__profiling_"

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
    overrides: Optional[Mapping[str, str]] = None,
    real_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Build a run's environment: ambient, then each `.env`, then `overrides`.

        1. the ambient environment       the subshell's starting point
        2. each path in order            config.env, profiler, package
        3. overrides                     --env-profiler / --env-package

    The ambient environment is the *base*, not the winner: a `.env` assigns
    unconditionally, so a stray ``PACKAGE_ARGS`` in someone's shell cannot
    redirect a run, and ``PYTHONPATH="$MY_SRC:$PYTHONPATH"`` in a `.env` sticks.
    Harness knobs still answer to the shell, because config.env declares them
    with ``: "${VAR:=default}"``.

    Raises ``EnvFileError`` naming the offending file if any layer fails.
    """
    env = source_files(paths, base=base_environment(real_env))
    if overrides:
        env.update(overrides)
    return env


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
