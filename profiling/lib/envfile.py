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
from typing import Dict, Mapping, Optional

class EnvFileError(RuntimeError):
    """Raised when a `.env` file fails to source."""


# Bash bookkeeping variables that would otherwise pollute every capture and
# make it depend on where the harness happened to be invoked from.
_VOLATILE = frozenset({"_", "PWD", "OLDPWD", "SHLVL", "BASH_ENV"})

# Internal names used by lib/capture_env.sh.
_INTERNAL_PREFIX = "__profiling_"

# The bash side of the capture: sources "$@" in order and prints `env -0`.
# On any premature exit it emits a marker naming the file, parsed by _parse.
_CAPTURE_SH = Path(__file__).resolve().parent / "capture_env.sh"


def load_layers(
    *paths: Path,
    overrides: Optional[Mapping[str, str]] = None,
    real_env: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Build a run's environment: ambient, then each `.env`, then `overrides`.

        1. the ambient environment       the subshell's starting point
        2. each path in order            config.env, profiler, package
        3. overrides                     --env-profiler / --env-package

    All paths are sourced in one bash process, in order, so a later layer can
    interpolate and override an earlier one.  The ambient environment is the
    *base*, not the winner: a `.env` assigns unconditionally, so it beats
    whatever was exported into the shell -- a stray ``PACKAGE_ARGS`` left over
    in someone's session cannot silently redirect a run -- while
    ``PYTHONPATH="$MY_SRC:$PYTHONPATH"`` still sticks.  Harness variables in
    config.env are declared with ``: "${VAR:=default}"`` and so *do* answer to
    the ambient environment (``PROFILING_OUT_PATH=...``, ``docker run -e``).

    Raises ``EnvFileError`` naming the offending file if any layer fails.
    """
    files = [str(Path(p)) for p in paths]
    for path in files:
        if not Path(path).is_file():
            raise EnvFileError(f"env file not found: {path}")

    proc = subprocess.run(
        ["bash", str(_CAPTURE_SH), *files],
        env=_base_environment(real_env),
        # A `.env` must never wait on stdin; sourcing has to be unattended.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    env = _parse(proc, files)
    if overrides:
        env.update(overrides)
    return env


def _base_environment(real_env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """The real process environment minus bash bookkeeping, so the capture does
    not depend on the caller's working directory or shell nesting."""
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

    if culprit is not None or not proc.stdout:
        # Exit status 0 but the shell died while a layer was still being
        # sourced: a `.env` called `exit 0` (or exec'd away).  Without this
        # check the capture would come back empty and look like a run with no
        # environment at all.
        where = culprit or (paths[-1] if paths else "<unknown>")
        raise EnvFileError(
            f"sourcing {where} exited the shell before the environment could "
            "be captured; a .env file must not call exit"
        )

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
