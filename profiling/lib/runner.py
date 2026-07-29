"""Execute one (package, profiler) run.

The central mechanism is the *target contract*: before the
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

import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional

import process
from discovery import PACKAGE_KINDS, Package, Profiler

__all__ = [
    "RunError",
    "RunResult",
    "Target",
    "build_target",
    "make_run_id",
    "render_target_sh",
    "execute",
    "reset_run_dir",
    "resolve",
]

_RUN_ID_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


class RunError(Exception):
    """Raised for a run that cannot be set up (bad kind, missing entry, ...)."""


# The run currently in flight, so an interrupt can take its process group down
# with it.  Runs are strictly sequential, so a single slot is enough.
_ACTIVE: "Optional[subprocess.Popen]" = None


def terminate_active() -> bool:
    """Kill the in-flight run's process group, if there is one.

    The workload owns its session, so a signal to the harness does not reach it:
    without this an interrupt orphans the profiler and the workload.
    """
    proc = _ACTIVE
    if proc is None or proc.poll() is not None:
        return False
    process.kill_group(proc)
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
    command: List[str] = field(default_factory=list)
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
    ``resolve``, which asks bash for the final arrays.
    """
    kind = package.kind
    entry = package.entry_point
    args = package.args
    workdir = Path(package.workdir)

    if kind not in PACKAGE_KINDS:
        raise RunError(
            f"package '{package.name}': unknown PACKAGE_KIND {kind!r} "
            "(expected " + ", ".join(PACKAGE_KINDS) + ")"
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


#: The bash side of a run; see lib/harness.sh for what it does.
HARNESS_SH = Path(__file__).resolve().parent / "harness.sh"



def _hook_env(
    env: Mapping[str, str],
    package: Package,
    profiler: Profiler,
    run_dir: Path,
    run_id: str,
    target_file: Path,
    argv_file: Path,
    command_file: Path,
) -> Dict[str, str]:
    """The environment every hook sees."""
    out = dict(env)
    out.update(
        {
            "RUN_DIR": str(run_dir),
            "RUN_ID": run_id,
            "PACKAGE_NAME": package.name,
            "PROFILER_NAME": profiler.name,
            "PACKAGE_WORKDIR": str(package.workdir),
            # A hook's own directory, so profiler-specific helper scripts can
            # live beside the profiler that needs them instead of in lib/.
            "PACKAGE_DIR": str(package.entry.path),
            "PROFILER_DIR": str(profiler.entry.path),
            "PROFILING_PACKAGE_SH": str(package.entry.script_file),
            "PROFILING_PROFILER_SH": str(profiler.entry.script_file),
            "PROFILING_TARGET_FILE": str(target_file),
            "PROFILING_ARGV_FILE": str(argv_file),
            "PROFILING_COMMAND_FILE": str(command_file),
        }
    )
    return out


def _read_nul(path: Path) -> Optional[List[str]]:
    if not path.is_file():
        return None
    parts = [p.decode("utf-8", "replace") for p in path.read_bytes().split(b"\0") if p]
    return parts or None


def resolve(
    env: Mapping[str, str],
    package: Package,
    profiler: Profiler,
    target: Target,
    scratch_dir: Path,
    run_dir: Path,
    run_id: str,
) -> "tuple[List[str], List[str]]":
    """Resolve (workload argv, profiler command) without running anything.

    Used by --dry-run.  ``run_dir`` is where the run *would* go, so the printed
    command names the real artifact paths; nothing is created there.
    """
    scratch_dir.mkdir(parents=True, exist_ok=True)
    target_file = scratch_dir / "target.sh"
    argv_file = scratch_dir / "argv"
    command_file = scratch_dir / "command"
    target_file.write_text(render_target_sh(target, package, profiler), "utf-8")

    hook_env = _hook_env(
        env, package, profiler, run_dir, run_id, target_file, argv_file, command_file
    )
    hook_env["PROFILING_RESOLVE_ONLY"] = "1"

    proc = subprocess.run(
        ["bash", str(HARNESS_SH)],
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

    return _read_nul(argv_file) or list(target.argv), _read_nul(command_file) or []


#: ``run_package_init`` returns this when package.sh defines no such hook.
NO_INIT_HOOK = 79


def run_package_init(env: Mapping[str, str], package: Package) -> int:
    """Call ``package_init``.  Returns its exit code; output goes to the console.

    ``NO_INIT_HOOK`` means there was no hook to call -- a skip, not a failure, so
    ``--init`` can be pointed at any package.  Nothing is cached; whether there
    is work to do is the hook's own business.
    """
    script = (
        "set -o pipefail\n"
        '. "$PROFILING_ROOT/lib/common.sh"\n'
        '. "$1"\n'
        "if ! declare -F package_init >/dev/null; then\n"
        f"  exit {NO_INIT_HOOK}\n"
        "fi\n"
        "package_init\n"
    )
    hook_env = dict(env)
    hook_env["PACKAGE_NAME"] = package.name
    hook_env["PACKAGE_DIR"] = str(package.entry.path)
    proc = subprocess.run(
        ["bash", "-c", script, "_", str(package.entry.script_file)],
        env=hook_env,
        cwd=_existing_dir(package.workdir, package.entry.path),
    )
    return proc.returncode


def reset_run_dir(run_dir: Path, output_root: Path) -> bool:
    """Clear a run directory that already exists.  Returns True if it did.

    Only reachable when ``RUN_ID`` is pinned and that build is re-run.  Without
    it the previous artifacts survive alongside the new ones and get listed in
    ``meta.json`` as if this run produced them -- a stale ``profile.svg`` beside
    a fresh ``profile.json``.
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
    target_file = run_dir / "target.sh"
    target_file.write_text(render_target_sh(target, package, profiler), "utf-8")

    argv_file = run_dir / ".argv"
    command_file = run_dir / ".command"
    hook_env = _hook_env(
        env, package, profiler, run_dir, run_id, target_file, argv_file, command_file
    )

    started_at = _stamp()
    started = time.time()

    proc = subprocess.Popen(
        ["bash", str(HARNESS_SH)],
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
            target=process.tee, args=(proc.stdout, stdout_log, sys.stdout), daemon=True
        ),
        threading.Thread(
            target=process.tee, args=(proc.stderr, stderr_log, sys.stderr), daemon=True
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
            process.kill_group(proc)
            exit_code = proc.wait()
        except BaseException:
            # Ctrl-C, SIGTERM from a cancelled CI job, anything else: take the
            # process group with us rather than orphaning the workload.
            process.kill_group(proc)
            raise
    finally:
        _ACTIVE = None

    for t in teams:
        t.join(timeout=5)

    duration = time.time() - started
    finished_at = _stamp()

    # What the harness actually resolved, which is what belongs in meta.json:
    # a package_command hook may have rewritten the workload, and the profiler
    # command is only known once its builder has run.
    argv = _read_nul(argv_file) or list(target.argv)
    command = _read_nul(command_file) or []
    argv_file.unlink(missing_ok=True)
    command_file.unlink(missing_ok=True)

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
        command=command,
        started_at=started_at,
        finished_at=finished_at,
    )


def _existing_dir(*candidates) -> str:
    for candidate in candidates:
        if candidate and Path(candidate).is_dir():
            return str(candidate)
    return str(Path.cwd())


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
