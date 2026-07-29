"""Run-once package init, guarded by a content fingerprint.

The fingerprint covers the package's two leaf files plus every path listed in
``PACKAGE_INIT_FINGERPRINT`` (lockfiles, entry points, ...).  A change to any
of them means the built artefact is stale and init must run again.

The stamp and log live under ``$PROFILING_STATE_DIR``, never in a run
directory, so pruning output can never trigger a rebuild.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping, Optional

from discovery import Package

__all__ = ["InitError", "InitResult", "compute_fingerprint", "needs_init", "run_init"]


class InitError(Exception):
    """Raised when init cannot even be attempted (e.g. a bad fingerprint path)."""


@dataclass
class InitResult:
    ran: bool
    ok: bool
    reason: str
    duration_s: float = 0.0
    log_path: Optional[Path] = None


def state_dir_for(state_root: Path, package: str) -> Path:
    return state_root / package


def state_file_for(state_root: Path, package: str) -> Path:
    return state_dir_for(state_root, package) / "init.json"


def compute_fingerprint(package: Package) -> str:
    """sha256 over the package's leaf files and its declared fingerprint files.

    Paths in ``PACKAGE_INIT_FINGERPRINT`` resolve relative to
    ``PACKAGE_WORKDIR``.  A listed file that does not exist is a hard error: if
    a typo hashed to "nothing" instead, init would silently never re-run again.
    """
    digest = hashlib.sha256()

    def absorb(label: str, data: bytes) -> None:
        digest.update(label.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(len(data)).encode("ascii"))
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")

    for leaf in (package.entry.env_file, package.entry.script_file):
        absorb(leaf.name, leaf.read_bytes() if leaf.is_file() else b"")

    workdir = Path(package.workdir)
    for rel in package.fingerprint_files:
        target = Path(rel)
        if not target.is_absolute():
            target = workdir / target
        if not target.is_file():
            raise InitError(
                f"package '{package.name}': PACKAGE_INIT_FINGERPRINT lists "
                f"{rel!r}, which does not exist (resolved to {target}). "
                "Fix the path or drop it from the list."
            )
        absorb(rel, target.read_bytes())

    return digest.hexdigest()


def read_state(state_root: Path, package_name: str) -> Optional[Dict]:
    path = state_file_for(state_root, package_name)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text("utf-8"))
    except (ValueError, OSError):
        # A corrupt stamp is treated as "no stamp": re-run init rather than
        # trusting a build we cannot verify.
        return None


def has_init_hook(package: Package, env: Mapping[str, str]) -> bool:
    """True if package.sh defines ``package_init``."""
    script = package.entry.script_file
    if not script.is_file():
        return False
    probe = 'set -e; . "$1"; declare -F package_init >/dev/null'
    proc = subprocess.run(
        ["bash", "-c", probe, "_", str(script)],
        env=dict(env),
        cwd=_safe_cwd(package),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def needs_init(
    package: Package,
    env: Mapping[str, str],
    state_root: Path,
    force: bool,
) -> "tuple[bool, str, Optional[str]]":
    """Decide whether init must run.  Returns (run?, reason, fingerprint)."""
    if not has_init_hook(package, env):
        return False, "no package_init hook", None

    fingerprint = compute_fingerprint(package)

    if force:
        return True, "--force-init", fingerprint

    state = read_state(state_root, package.name)
    if state is None:
        return True, "no init stamp", fingerprint
    if state.get("fingerprint") != fingerprint:
        return True, "fingerprint changed", fingerprint
    if state.get("status") != "ok":
        return True, f"previous init status {state.get('status')!r}", fingerprint
    return False, "up to date", fingerprint


def run_init(
    package: Package,
    env: Mapping[str, str],
    state_root: Path,
    fingerprint: str,
) -> InitResult:
    """Execute ``package_init`` with output tee'd to the state log."""
    sdir = state_dir_for(state_root, package.name)
    sdir.mkdir(parents=True, exist_ok=True)
    log_path = sdir / "init.log"

    script = (
        'set -o pipefail\n'
        '. "$PROFILING_ROOT/lib/common.sh"\n'
        '. "$1"\n'
        'package_init\n'
    )

    started = time.time()
    with log_path.open("wb") as log:
        header = f"=== package_init: {package.name} @ {_stamp()} ===\n"
        log.write(header.encode())
        log.flush()
        proc = subprocess.run(
            ["bash", "-c", script, "_", str(package.entry.script_file)],
            env=dict(env),
            cwd=_safe_cwd(package),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        log.write(proc.stdout)
    duration = time.time() - started

    ok = proc.returncode == 0
    if not ok:
        # Surface the tail so CI logs show why, without dumping a whole build.
        tail = proc.stdout.decode("utf-8", "replace").splitlines()[-30:]
        for line in tail:
            print(f"  init| {line}", flush=True)

    state_file_for(state_root, package.name).write_text(
        json.dumps(
            {
                "package": package.name,
                "fingerprint": fingerprint,
                "status": "ok" if ok else "failed",
                "exit_code": proc.returncode,
                "timestamp": _stamp(),
                "duration_s": round(duration, 3),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    return InitResult(
        ran=True,
        ok=ok,
        reason="ok" if ok else f"package_init exited {proc.returncode}",
        duration_s=duration,
        log_path=log_path,
    )


def _safe_cwd(package: Package) -> str:
    workdir = Path(package.workdir)
    return str(workdir) if workdir.is_dir() else str(package.entry.path)


def _stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
