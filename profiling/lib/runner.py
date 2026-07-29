"""Execute one (package, profiler) run.

The central mechanism is the *target contract* (§6 of the spec): before the
profiler hook is invoked, the runner writes ``<RUN_DIR>/target.sh`` declaring
the workload as bash arrays.  Sourcing a generated file sidesteps every
quoting and export problem that passing argv through the environment would
create, and exposing the argv in two shapes serves both profiler families:

  * prefix wrappers (`/usr/bin/time`, `py-spy record -- ...`) consume
    ``TARGET_ARGV``, the complete plain command;
  * interpreter replacements (`viztracer`, `kernprof`) consume
    ``TARGET_PYTHON_ARGV``, the same command minus the interpreter, which is
    exactly Python's own trailing CLI shape (`-m module args...`).
"""

from __future__ import annotations

import os
import re
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from discovery import Package, Profiler

__all__ = [
    "RunError",
    "RunResult",
    "Target",
    "build_target",
    "make_run_id",
    "render_target_sh",
    "execute",
    "reset_run_dir",
    "resolve_argv",
]

RUN_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{4}$")
_RUN_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class RunError(Exception):
    """Raised for a run that cannot be set up (bad kind, missing entry, ...)."""


# The run currently in flight, so an interrupt can take its process group down
# with it.  Runs are strictly sequential, so a single slot is enough.
_ACTIVE: "Optional[subprocess.Popen]" = None


def terminate_active() -> bool:
    """Kill the in-flight run's process group, if there is one.

    The workload runs in its own session so that a timeout can kill the
    profiler and everything it spawned together.  The flip side is that a
    signal sent to the harness does *not* reach it, so an interrupted run
    would otherwise leave py-spy and the workload orphaned in the container.
    """
    proc = _ACTIVE
    if proc is None or proc.poll() is not None:
        return False
    _kill_group(proc)
    return True


@dataclass
class Target:
    kind: str
    python: Optional[str]
    argv: List[str]
    python_argv: Optional[List[str]]


@dataclass
class RunResult:
    package: str
    profiler: str
    status: str  # ok | failed | timeout | skipped
    run_id: Optional[str] = None
    run_dir: Optional[Path] = None
    exit_code: Optional[int] = None
    duration_s: float = 0.0
    reason: str = ""
    forced: bool = False
    argv: List[str] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""


# ---------------------------------------------------------------- run ids ---


def make_run_id(env: Mapping[str, str]) -> str:
    """Timestamped id, or a sanitized override from ``RUN_ID`` (CI build no.).

    An injected value becomes a directory name, so it is sanitized hard: only
    ``[A-Za-z0-9._-]`` survives, and the path-traversal names are rejected.
    """
    override = (env.get("RUN_ID") or "").strip()
    if override:
        clean = _RUN_ID_SAFE.sub("-", override).strip("-.")[:128]
        if clean and clean not in (".", ".."):
            return clean
        raise RunError(f"RUN_ID={override!r} does not contain any usable characters")
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(2)}"


# ------------------------------------------------------------ the target ---


def build_target(package: Package) -> Target:
    """Derive the target contract from the package's `.env` declarations.

    A ``package_command`` hook may override this wholesale; see
    ``resolve_argv``, which asks bash for the final arrays.
    """
    kind = package.kind
    entry = package.entry_point
    args = package.args
    workdir = Path(package.workdir)

    if kind not in ("python-module", "python-script", "exec"):
        raise RunError(
            f"package '{package.name}': unknown PACKAGE_KIND {kind!r} "
            "(expected python-module, python-script or exec)"
        )
    if not entry:
        raise RunError(
            f"package '{package.name}': PACKAGE_ENTRY is empty and package.sh "
            "defines no package_command"
        )

    if kind == "python-module":
        python = package.python
        python_argv = ["-m", entry, *args]
        return Target(kind, python, [python, *python_argv], python_argv)

    if kind == "python-script":
        python = package.python
        script = Path(entry)
        if not script.is_absolute():
            script = workdir / script
        python_argv = [str(script), *args]
        return Target(kind, python, [python, *python_argv], python_argv)

    # exec: no interpreter, so TARGET_PYTHON_ARGV stays unset.
    binary = Path(entry)
    if not binary.is_absolute() and (workdir / binary).exists():
        binary = workdir / binary
    return Target(kind, None, [str(binary), *args], None)


def render_target_sh(target: Target, package: Package, profiler: Profiler) -> str:
    """Serialize the target contract as a sourceable bash file."""
    lines = [
        "# Generated by the profiling harness -- do not edit.",
        "# Sourced by lib/common.sh consumers; see README.md 'The target contract'.",
        f"TARGET_KIND={shlex.quote(target.kind)}",
        f"TARGET_PACKAGE={shlex.quote(package.name)}",
        f"TARGET_PROFILER={shlex.quote(profiler.name)}",
    ]
    if target.python:
        lines.append(f"TARGET_PYTHON={shlex.quote(target.python)}")
    else:
        lines.append("unset TARGET_PYTHON")

    lines.append(
        "TARGET_ARGV=(" + " ".join(shlex.quote(a) for a in target.argv) + ")"
    )
    if target.python_argv is None:
        # PACKAGE_KIND=exec has no interpreter to strip off.  Leaving the array
        # unset lets require_python_target fail with a readable message instead
        # of expanding to nothing and confusing the profiler's own CLI parser.
        lines.append("unset TARGET_PYTHON_ARGV")
    else:
        lines.append(
            "TARGET_PYTHON_ARGV=("
            + " ".join(shlex.quote(a) for a in target.python_argv)
            + ")"
        )
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------- harnesses ---

# Resolves the final argv without running anything: sources the same files the
# real harness does and lets a package_command hook have the last word.  Used
# by --dry-run and to record the true argv in meta.json.
_RESOLVE_HARNESS = r"""
set -e
. "$PROFILING_ROOT/lib/common.sh"
. "$PROFILING_TARGET_FILE"
if [ -f "$PROFILING_PACKAGE_SH" ]; then . "$PROFILING_PACKAGE_SH"; fi
if declare -F package_command >/dev/null; then
  package_command
fi
printf '%s\0' "${TARGET_ARGV[@]}" > "$PROFILING_ARGV_FILE"
"""

# Asks the profiler for the exact command it would run, via the optional
# profiler_dry_run hook.  Profilers that do not implement it simply produce no
# output and --dry-run falls back to naming the wrapper.
_DRYRUN_HARNESS = r"""
set -e
. "$PROFILING_ROOT/lib/common.sh"
. "$PROFILING_TARGET_FILE"
if [ -f "$PROFILING_PACKAGE_SH" ]; then . "$PROFILING_PACKAGE_SH"; fi
if [ -f "$PROFILING_PROFILER_SH" ]; then . "$PROFILING_PROFILER_SH"; fi
if declare -F package_command >/dev/null; then
  package_command
fi
if declare -F profiler_dry_run >/dev/null; then
  profiler_dry_run > "$PROFILING_ARGV_FILE"
fi
"""

# The real run.  Everything happens in one bash process so that the hooks share
# state, and package_post_run is installed as an EXIT trap so it also runs when
# the workload fails or the process is killed on timeout.
_RUN_HARNESS = r"""
set -o pipefail
. "$PROFILING_ROOT/lib/common.sh"
. "$RUN_DIR/target.sh"
if [ -f "$PROFILING_PACKAGE_SH" ]; then . "$PROFILING_PACKAGE_SH"; fi
if [ -f "$PROFILING_PROFILER_SH" ]; then . "$PROFILING_PROFILER_SH"; fi

if declare -F package_command >/dev/null; then
  package_command
fi
printf '%s\0' "${TARGET_ARGV[@]}" > "$PROFILING_ARGV_FILE"

if ! declare -F profiler_wrap >/dev/null; then
  profiling_error "profiler '$PROFILER_NAME' defines no profiler_wrap function"
  exit 78
fi

__profiling_post_run() {
  if declare -F package_post_run >/dev/null; then
    package_post_run || profiling_warn "package_post_run exited $?"
  fi
}
trap __profiling_post_run EXIT

cd "$PACKAGE_WORKDIR" || {
  profiling_error "PACKAGE_WORKDIR does not exist: $PACKAGE_WORKDIR"
  exit 77
}

if declare -F package_pre_run >/dev/null; then
  package_pre_run || {
    __profiling_status=$?
    profiling_error "package_pre_run exited $__profiling_status"
    exit "$__profiling_status"
  }
fi

profiler_wrap "${TARGET_ARGV[@]}"
__profiling_status=$?

if [ "$__profiling_status" -eq 0 ] && declare -F profiler_post >/dev/null; then
  profiler_post || {
    __profiling_status=$?
    profiling_error "profiler_post exited $__profiling_status"
  }
fi

exit "$__profiling_status"
"""


def _hook_env(
    env: Mapping[str, str],
    package: Package,
    profiler: Profiler,
    run_dir: Path,
    run_id: str,
    argv_file: Path,
    target_file: Optional[Path] = None,
) -> Dict[str, str]:
    """The environment every hook sees (§6)."""
    out = dict(env)
    out.update(
        {
            "RUN_DIR": str(run_dir),
            "RUN_ID": run_id,
            "PACKAGE_NAME": package.name,
            "PROFILER_NAME": profiler.name,
            "PACKAGE_WORKDIR": str(package.workdir),
            "PROFILING_PACKAGE_SH": str(package.entry.script_file),
            "PROFILING_PROFILER_SH": str(profiler.entry.script_file),
            "PROFILING_ARGV_FILE": str(argv_file),
        }
    )
    if target_file is not None:
        out["PROFILING_TARGET_FILE"] = str(target_file)
    return out


def _read_argv(argv_file: Path) -> Optional[List[str]]:
    if not argv_file.is_file():
        return None
    raw = argv_file.read_bytes()
    parts = [p.decode("utf-8", "replace") for p in raw.split(b"\0") if p]
    return parts or None


def resolve_argv(
    env: Mapping[str, str],
    package: Package,
    profiler: Profiler,
    target: Target,
    scratch_dir: Path,
    run_dir: Path,
    run_id: str,
) -> List[str]:
    """Ask bash for the final argv, honouring a ``package_command`` override.

    Used by ``--dry-run`` so the printed command is the one that would really
    run, without creating the run directory or starting the workload.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    target_file = scratch_dir / "target.sh"
    argv_file = scratch_dir / "argv"
    target_file.write_text(render_target_sh(target, package, profiler), "utf-8")

    hook_env = _hook_env(
        env, package, profiler, run_dir, run_id, argv_file, target_file=target_file
    )
    proc = subprocess.run(
        ["bash", "-c", _RESOLVE_HARNESS],
        env=hook_env,
        cwd=_existing_dir(package.workdir, package.entry.path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RunError(
            f"could not resolve the command for {package.name}/{profiler.name}"
            + (f":\n{detail}" if detail else "")
        )
    return _read_argv(argv_file) or list(target.argv)


def dry_run_command(
    env: Mapping[str, str],
    package: Package,
    profiler: Profiler,
    target: Target,
    scratch_dir: Path,
    run_dir: Path,
    run_id: str,
) -> Optional[List[str]]:
    """The exact command the profiler would execute, or ``None``.

    Exactness for an arbitrary bash hook is only possible with the hook's
    cooperation, so this asks the optional ``profiler_dry_run`` hook -- which
    every shipped profiler implements by sharing its argv builder with
    ``profiler_wrap``.  Third-party profilers without the hook get ``None``
    and --dry-run degrades to naming the wrapper.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    target_file = scratch_dir / "target.sh"
    argv_file = scratch_dir / "dryrun-argv"
    target_file.write_text(render_target_sh(target, package, profiler), "utf-8")

    hook_env = _hook_env(
        env, package, profiler, run_dir, run_id, argv_file, target_file=target_file
    )
    proc = subprocess.run(
        ["bash", "-c", _DRYRUN_HARNESS],
        env=hook_env,
        cwd=_existing_dir(package.workdir, package.entry.path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        if detail:
            print(detail, file=sys.stderr)
        return None
    return _read_argv(argv_file)


# ------------------------------------------------------------- execution ---


def reset_run_dir(run_dir: Path, output_root: Path) -> bool:
    """Clear a run directory that already exists.

    Only reachable when ``RUN_ID`` is pinned (a CI build number) and that build
    is re-run.  Without this, the previous run's artifacts survive alongside
    the new one and get listed in ``meta.json`` as if this run had produced
    them -- e.g. a stale ``profile.svg`` next to a fresh ``profile.json``.

    Returns True if anything was cleared.  The containment check is belt and
    braces: ``run_id`` is already sanitized to a single path component.
    """
    if not run_dir.is_dir():
        return False
    try:
        run_dir.resolve().relative_to(output_root.resolve())
    except ValueError:
        raise RunError(
            f"refusing to reset {run_dir}: it resolves outside {output_root}"
        )
    if not any(run_dir.iterdir()):
        return False

    for child in run_dir.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    return True


def execute(
    env: Mapping[str, str],
    package: Package,
    profiler: Profiler,
    target: Target,
    run_dir: Path,
    run_id: str,
    forced: bool,
    timeout: Optional[float],
) -> RunResult:
    """Run the workload under the profiler, tee'ing output and enforcing the
    package timeout by killing the whole process group."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "target.sh").write_text(
        render_target_sh(target, package, profiler), "utf-8"
    )

    argv_file = run_dir / ".argv"
    hook_env = _hook_env(env, package, profiler, run_dir, run_id, argv_file)

    started_at = _stamp()
    started = time.time()

    proc = subprocess.Popen(
        ["bash", "-c", _RUN_HARNESS],
        env=hook_env,
        cwd=_existing_dir(package.workdir, package.entry.path),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        # Its own session, so a timeout can take the profiler *and* the
        # workload it spawned down together.
        start_new_session=True,
    )

    stdout_log = run_dir / "stdout.log"
    stderr_log = run_dir / "stderr.log"
    teams = [
        threading.Thread(
            target=_tee, args=(proc.stdout, stdout_log, sys.stdout), daemon=True
        ),
        threading.Thread(
            target=_tee, args=(proc.stderr, stderr_log, sys.stderr), daemon=True
        ),
    ]
    for t in teams:
        t.start()

    global _ACTIVE
    _ACTIVE = proc

    timed_out = False
    try:
        try:
            exit_code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(proc)
            exit_code = proc.wait()
        except BaseException:
            # Ctrl-C, SIGTERM from a cancelled CI job, anything else: take the
            # process group with us rather than orphaning the workload.
            _kill_group(proc)
            raise
    finally:
        _ACTIVE = None

    for t in teams:
        t.join(timeout=5)

    duration = time.time() - started
    finished_at = _stamp()

    argv = _read_argv(argv_file) or list(target.argv)
    argv_file.unlink(missing_ok=True)

    if timed_out:
        status, reason = "timeout", f"exceeded PACKAGE_TIMEOUT of {timeout:g}s"
    elif exit_code == 0:
        status, reason = "ok", ""
    else:
        status, reason = "failed", f"exited {exit_code}"

    return RunResult(
        package=package.name,
        profiler=profiler.name,
        status=status,
        run_id=run_id,
        run_dir=run_dir,
        exit_code=exit_code,
        duration_s=duration,
        reason=reason,
        forced=forced,
        argv=argv,
        started_at=started_at,
        finished_at=finished_at,
    )


def _tee(stream, path: Path, console) -> None:
    """Stream to the console and to a log file at the same time."""
    try:
        with path.open("wb") as fh:
            for chunk in iter(lambda: stream.readline(), b""):
                fh.write(chunk)
                fh.flush()
                try:
                    console.write(chunk.decode("utf-8", "replace"))
                    console.flush()
                except (ValueError, OSError):
                    pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _group_alive(pgid: int) -> bool:
    """True while any process remains in the group (signal 0 probes it)."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _signal_group(pgid: int, sig: int) -> bool:
    try:
        os.killpg(pgid, sig)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _kill_group(
    proc: "subprocess.Popen",
    term_grace: float = 2.0,
    kill_grace: float = 5.0,
) -> None:
    """SIGTERM the process group, then SIGKILL whatever is still in it.

    Escalation is driven by whether the *group* is empty, not by whether the
    direct child exited.  Those differ in practice: GNU time sets SIGTERM to
    SIG_IGN, and an ignored disposition survives exec, so `time -- sleep 99`
    leaves a sleep that shrugs off the SIGTERM that killed its parent. Keying
    on the child alone let that sleep outlive the harness.
    """
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return

    _signal_group(pgid, signal.SIGTERM)

    # Reap the direct child first.  An un-reaped zombie is still a member of
    # the group, so probing before this would always report the group alive
    # and stall for the full grace period on every kill.
    try:
        proc.wait(timeout=term_grace)
    except subprocess.TimeoutExpired:
        pass

    if _group_alive(pgid):
        _signal_group(pgid, signal.SIGKILL)
        deadline = time.monotonic() + kill_grace
        while time.monotonic() < deadline and _group_alive(pgid):
            time.sleep(0.05)
    _reap(proc)


def _reap(proc: "subprocess.Popen") -> None:
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _existing_dir(*candidates) -> str:
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return str(candidate)
    return str(Path.cwd())


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
